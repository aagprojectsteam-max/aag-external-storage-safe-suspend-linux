"""Reference-platform plain-Hibernate safety and recovery.

The module never enables suspend-then-hibernate or hybrid sleep.  Live power
entry is available only through ``aag-safe-suspend hibernate``; health and
readiness inspection are non-destructive.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

from . import config as configuration
from . import coordinator, state

STATE_ROOT = Path("/var/lib/aag-external-storage-safe-suspend")
EPISODE = STATE_ROOT / "hibernate-episode.json"
BOOT_UNIT = "aag-external-storage-safe-hibernate-boot-check.service"
GIB = 1024**3


class HibernateError(RuntimeError):
    pass


def _run(
    argv: list[str], *, timeout: float = 30, check: bool = True
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HibernateError(f"command unavailable: {argv[0]}: {exc}") from exc
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1000:]
        raise HibernateError(f"command failed ({result.returncode}): {argv[0]}: {detail}")
    return result


def boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def meminfo(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([A-Za-z_()]+):\s+(\d+)\s+kB", line)
        if match:
            values[match.group(1)] = int(match.group(2)) * 1024
    return values


def capacity_assessment(memory: dict[str, int], image_size: int, swap_free: int) -> dict[str, Any]:
    """Apply the physically accepted, non-double-counted capacity model."""
    total = memory.get("MemTotal", 0)
    available = memory.get("MemAvailable", 0)
    resident_pressure = max(0, total - available)
    # AnonPages/Shmem type counters must not be added to Unevictable: doing so
    # can count the same physical folios twice.
    nonreclaimable = sum(
        memory.get(name, 0)
        for name in ("Unevictable", "SUnreclaim", "KernelStack", "PageTables", "SecPageTables")
    )
    storage_gate = max(int(total * 1.10), int(image_size * 1.25))
    checks = {
        "image_policy_covers_resident_pressure": image_size >= resident_pressure,
        "image_policy_covers_nonreclaimable_floor_plus_2gib": image_size
        >= nonreclaimable + 2 * GIB,
        "available_memory_allows_snapshot_workspace": available >= image_size + 4 * GIB,
        "swap_free_covers_ram_plus_ten_percent": swap_free >= storage_gate,
        "image_size_nonzero": image_size > 0,
    }
    return {
        "result": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "mem_total_bytes": total,
        "mem_available_bytes": available,
        "image_size_bytes": image_size,
        "swap_free_bytes": swap_free,
        "resident_pressure_bytes": resident_pressure,
        "nonreclaimable_floor_bytes": nonreclaimable,
        "storage_gate_bytes": storage_gate,
        "accounting": "non-overlapping MemTotal-MemAvailable pressure model",
    }


def parse_cmdline(text: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for word in text.split():
        key, separator, value = word.partition("=")
        result.setdefault(key, []).append(value if separator else "")
    return result


def parse_swaps(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) != 5:
            raise HibernateError("malformed /proc/swaps row")
        rows.append(
            {
                "path": fields[0],
                "type": fields[1],
                "size_bytes": int(fields[2]) * 1024,
                "used_bytes": int(fields[3]) * 1024,
            }
        )
    return rows


def parse_filefrag_first_physical(text: str) -> int:
    match = re.search(r"^\s*0:\s+0\.\.\s*\d+:\s*(\d+)\.\.", text, re.MULTILINE)
    if not match:
        raise HibernateError("filefrag did not expose logical extent zero")
    return int(match.group(1))


def swap_area_matches_file(file_size: int, active_size: int, page_size: int) -> bool:
    """Match Linux's usable swap size, which excludes the swap-header page."""
    return file_size > page_size and active_size == file_size - page_size


def _findmnt(path: str) -> dict[str, str]:
    output = _run(
        [
            "/usr/bin/findmnt",
            "--evaluate",
            "-J",
            "-o",
            "TARGET,SOURCE,MAJ:MIN,FSTYPE,UUID",
            "-T",
            path,
        ]
    ).stdout
    rows = json.loads(output).get("filesystems", [])
    if len(rows) != 1:
        raise HibernateError(f"ambiguous filesystem identity for {path}")
    return {str(key): str(value) for key, value in rows[0].items()}


def resume_assessment(swap_path: str) -> dict[str, Any]:
    swap = Path(swap_path)
    rows = [row for row in parse_swaps(Path("/proc/swaps").read_text()) if row["path"] == swap_path]
    if len(rows) != 1 or rows[0]["type"] != "file":
        raise HibernateError("configured swapfile is not the single matching active file swap")
    info = swap.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise HibernateError("configured swapfile is not a regular file")
    backing = _findmnt(swap_path)
    if backing.get("fstype") != "ext4":
        raise HibernateError("reference-platform swapfile must be backed by ext4")
    cmdline = parse_cmdline(Path("/proc/cmdline").read_text())
    resume = cmdline.get("resume", [])
    offsets = cmdline.get("resume_offset", [])
    if len(resume) != 1 or len(offsets) != 1 or not offsets[0].isdigit():
        raise HibernateError("kernel command line has ambiguous resume mapping")
    expected_device = f"UUID={backing.get('uuid')}"
    first = parse_filefrag_first_physical(_run(["/usr/sbin/filefrag", "-v", swap_path]).stdout)
    page_size = os.sysconf("SC_PAGE_SIZE")
    block_size = os.statvfs(swap).f_frsize
    if block_size != page_size:
        raise HibernateError("filesystem block and kernel page sizes differ")
    live_device = Path("/sys/power/resume").read_text().strip()
    live_offset = int(Path("/sys/power/resume_offset").read_text())
    checks = {
        "resume_device": resume[0] == expected_device and live_device == backing.get("maj:min"),
        "resume_offset": int(offsets[0]) == first and live_offset == first,
        "swapfile_identity": info.st_uid == 0
        and stat.S_IMODE(info.st_mode) == 0o600
        and swap_area_matches_file(info.st_size, rows[0]["size_bytes"], page_size),
    }
    return {
        "checks": checks,
        "expected_resume_device": expected_device,
        "current_resume_device": resume[0],
        "expected_resume_offset": first,
        "current_resume_offset": int(offsets[0]),
        "swap_inode": info.st_ino,
        "swap_size_bytes": info.st_size,
        "swap_free_bytes": rows[0]["size_bytes"] - rows[0]["used_bytes"],
    }


def initramfs_assessment(expected_device: str, expected_offset: int) -> bool:
    path = Path("/etc/initramfs-tools/conf.d/resume")
    if not path.is_file() or path.is_symlink():
        return False
    text = path.read_text()
    if f"RESUME={expected_device}" not in text or f"RESUME_OFFSET={expected_offset}" not in text:
        return False
    image = Path("/boot") / f"initrd.img-{os.uname().release}"
    if not image.is_file():
        return False
    listing = _run(["/usr/bin/lsinitramfs", str(image)], timeout=60, check=False)
    return listing.returncode == 0 and any(
        line.rstrip().endswith("conf/conf.d/resume") for line in listing.stdout.splitlines()
    )


def _power_support() -> bool:
    states = Path("/sys/power/state").read_text().split()
    modes = Path("/sys/power/disk").read_text().split()
    return "disk" in states and "[platform]" in modes


def _t700_identity(settings: dict[str, Any]) -> Path | None:
    for path in sorted(Path("/sys/bus/pci/devices").glob("*")):
        try:
            vendor = (path / "vendor").read_text().strip().lower()
            device = (path / "device").read_text().strip().lower()
            driver = (path / "driver").resolve().name
        except OSError:
            continue
        if (
            vendor == settings["pci_vendor"]
            and device == settings["pci_device"]
            and driver == "mtk_t7xx"
        ):
            return path
    return None


def t700_readiness(settings: dict[str, Any]) -> str:
    if not settings["enabled"]:
        return "NOT_CONFIGURED"
    if _t700_identity(settings) is None:
        return "FAIL"
    for executable in ("/usr/bin/mmcli", "/usr/bin/nmcli", "/usr/bin/systemctl"):
        if not Path(executable).is_file():
            return "FAIL"
    modem = _run(["/usr/bin/mmcli", "-L"], timeout=5, check=False).stdout
    network = _run(
        ["/usr/bin/nmcli", "-t", "-f", "TYPE,STATE", "device", "status"],
        timeout=5,
        check=False,
    ).stdout
    return (
        "PASS"
        if "/Modem/" in modem and re.search(r"^gsm:connected$", network, re.MULTILINE)
        else "FAIL"
    )


def readiness() -> dict[str, Any]:
    settings = configuration.load()["hibernate"]
    result: dict[str, Any] = {
        "HIBERNATE_SUPPORT_STATUS": "SUPPORTED_ON_REFERENCE_PLATFORM"
        if settings["enabled"]
        else "NOT_CONFIGURED",
        "RESUME_DEVICE_GATE": "NOT_EVALUATED",
        "RESUME_OFFSET_GATE": "NOT_EVALUATED",
        "SWAPFILE_IDENTITY_GATE": "NOT_EVALUATED",
        "INITRAMFS_RESUME_GATE": "NOT_EVALUATED",
        "IMAGE_CAPACITY_GATE": "NOT_EVALUATED",
        "MEMORY_SAFETY_GATE": "NOT_EVALUATED",
        "T700_HIBERNATE_RECOVERY": t700_readiness(settings["t700"]),
        "UGREEN_HIBERNATE_POLICY": "PASS",
        "CURRENT_KERNEL_STATE_SUPPORT": "PASS" if _power_support() else "FAIL",
        "SUSPEND_THEN_HIBERNATE_STATUS": "NOT_ENABLED_NOT_ACCEPTED",
        "HYBRID_SLEEP_STATUS": "NOT_ENABLED_NOT_ACCEPTED",
        "power_state_actions": "none",
    }
    if not settings["enabled"]:
        return result
    try:
        resume = resume_assessment(settings["swap_file"])
        result["RESUME_DEVICE_GATE"] = "PASS" if resume["checks"]["resume_device"] else "FAIL"
        result["RESUME_OFFSET_GATE"] = "PASS" if resume["checks"]["resume_offset"] else "FAIL"
        result["SWAPFILE_IDENTITY_GATE"] = (
            "PASS" if resume["checks"]["swapfile_identity"] else "FAIL"
        )
        result["INITRAMFS_RESUME_GATE"] = (
            "PASS"
            if initramfs_assessment(
                resume["expected_resume_device"], resume["expected_resume_offset"]
            )
            else "FAIL"
        )
        capacity = capacity_assessment(
            meminfo(Path("/proc/meminfo").read_text()),
            int(Path("/sys/power/image_size").read_text()),
            resume["swap_free_bytes"],
        )
        result["IMAGE_CAPACITY_GATE"] = (
            "PASS"
            if (
                capacity["checks"]["image_size_nonzero"]
                and capacity["checks"]["swap_free_covers_ram_plus_ten_percent"]
            )
            else "FAIL"
        )
        result["MEMORY_SAFETY_GATE"] = (
            "PASS"
            if all(
                capacity["checks"][name]
                for name in (
                    "image_policy_covers_resident_pressure",
                    "image_policy_covers_nonreclaimable_floor_plus_2gib",
                    "available_memory_allows_snapshot_workspace",
                )
            )
            else "FAIL"
        )
        result["details"] = {"resume": resume, "capacity": capacity}
    except Exception as exc:
        result["diagnostic"] = str(exc)
    gates = [
        value
        for key, value in result.items()
        if key.endswith("_GATE") or key == "CURRENT_KERNEL_STATE_SUPPORT"
    ]
    result["HIBERNATE_READY"] = (
        "PASS"
        if gates
        and all(value == "PASS" for value in gates)
        and result["T700_HIBERNATE_RECOVERY"] in {"PASS", "NOT_CONFIGURED"}
        else "FAIL"
    )
    return result


def simulate_wwan_recovery(
    *, generation_ready_at: int | None, settle_seconds: int, stable_seconds: int, deadline: int
) -> dict[str, Any]:
    stable = 0
    invoked = False
    invocation_time: int | None = None
    for second in range(settle_seconds, deadline + 1):
        ready = generation_ready_at is not None and second >= generation_ready_at
        stable = stable + 1 if ready else 0
        if stable >= stable_seconds:
            invoked = True
            invocation_time = second
            break
    return {
        "result": "PASS" if invoked else "FAIL",
        "helper_invocations": 1 if invoked else 0,
        "invoked_at": invocation_time,
    }


def simulate_policy(case: dict[str, Any]) -> dict[str, Any]:
    """Side-effect-free qualification oracle for sanitized fixtures."""
    failures: list[str] = []
    for enabled, reason in (
        (case.get("kernel_support", True), "KERNEL_STATE_UNSUPPORTED"),
        (case.get("swap_active", True), "MISSING_SWAP"),
        (case.get("resume_device", True), "WRONG_RESUME_DEVICE"),
        (case.get("resume_offset", True), "WRONG_RESUME_OFFSET"),
        (case.get("swap_identity", True), "SWAPFILE_IDENTITY_MISMATCH"),
        (case.get("initramfs", True), "INITRAMFS_RESUME_MISSING"),
        (case.get("image_capacity", True), "IMAGE_CAPACITY_INSUFFICIENT"),
        (case.get("memory_safety", True), "MEMORY_SAFETY_UNPROVEN"),
    ):
        if not enabled:
            failures.append(reason)
    owner = case.get("ugreen_owner", "clean")
    if owner == "managed":
        action = "GRACEFUL_QUIESCE_THEN_REAUDIT"
    elif owner in {"clean", "absent"}:
        action = "RELEASE_AND_TERMINAL_REAUDIT"
    else:
        action = "NONE"
        failures.append("UGREEN_UNKNOWN_OR_RAW_OWNER")
    if case.get("automount_after", False):
        failures.append("UGREEN_TERMINAL_AUTOMOUNT")
    if case.get("cold_boot", False):
        failures.append("COLD_BOOT_NOT_IMAGE_RESUME")
    if not case.get("t700_stable", True):
        failures.append("T700_GENERATION_UNSTABLE")
    return {
        "decision": "REFUSE_OR_FAIL_CLOSED" if failures else "PASS",
        "failures": failures,
        "ugreen_action": action,
        "ordinary_suspend_graph_changed": False,
        "power_state_actions": "none-in-simulation",
    }


def _write_episode(value: dict[str, Any]) -> None:
    state.atomic_json(EPISODE, value)


def _load_episode() -> dict[str, Any]:
    try:
        info = EPISODE.lstat()
        expected_uid = os.getuid() if os.environ.get("AAG_SAFE_SUSPEND_TEST_MODE") == "1" else 0
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != expected_uid
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise HibernateError("Hibernate episode owner, type, or mode is unsafe")
        value = json.loads(EPISODE.read_text())
    except (OSError, ValueError, TypeError) as exc:
        raise HibernateError(f"Hibernate episode is unavailable: {exc}") from exc
    if not isinstance(value, dict):
        raise HibernateError("Hibernate episode is malformed")
    return value


def transaction_active() -> bool:
    """Narrowly identify a live same-boot plain-Hibernate transaction."""
    if not EPISODE.exists() or EPISODE.is_symlink():
        return False
    try:
        episode = _load_episode()
    except HibernateError:
        return False
    return (
        episode.get("mode") == "plain-hibernate"
        and episode.get("boot_id") == boot_id()
        and episode.get("status") in {"ARMED", "IMAGE_GATE_PASSED", "NATIVE_RETURNED"}
    )


def _enable_boot_check(enabled: bool) -> None:
    verb = "enable" if enabled else "disable"
    _run(["/usr/bin/systemctl", verb, BOOT_UNIT], timeout=20)


def arm_and_hibernate() -> int:
    if os.geteuid() != 0:
        raise HibernateError("plain Hibernate requires root")
    report = readiness()
    if report.get("HIBERNATE_READY") != "PASS":
        raise HibernateError("Hibernate readiness gates did not pass")
    if state.load().get("state") != "IDLE":
        raise HibernateError("ordinary sleep/storage transaction is not IDLE")
    episode = {
        "schema": 1,
        "mode": "plain-hibernate",
        "status": "ARMED",
        "boot_id": boot_id(),
        "started": time.time(),
    }
    _write_episode(episode)
    _enable_boot_check(True)
    result = _run(["/usr/bin/systemctl", "hibernate"], timeout=24 * 3600, check=False)
    if result.returncode:
        with contextlib.suppress(Exception):
            _enable_boot_check(False)
        raise HibernateError("native systemd Hibernate request failed")
    return 0


def systemd_pre() -> int:
    episode = _load_episode()
    if (
        episode.get("mode") != "plain-hibernate"
        or episode.get("status") != "ARMED"
        or episode.get("boot_id") != boot_id()
    ):
        raise HibernateError("no matching armed plain-Hibernate episode")
    report = readiness()
    if report.get("HIBERNATE_READY") != "PASS":
        raise HibernateError("final pre-image readiness gate failed")
    current = state.load()
    if current.get("state") != "SLEEP_COMMITTED":
        raise HibernateError("external-storage release is not committed")
    episode.update(status="IMAGE_GATE_PASSED", image_gate=time.time())
    _write_episode(episode)
    os.sync()
    return 0


def systemd_return() -> int:
    if not EPISODE.exists():
        return 0
    episode = _load_episode()
    episode.update(status="NATIVE_RETURNED", native_return=time.time(), boot_id_after=boot_id())
    _write_episode(episode)
    return 0


def _ports_ready() -> bool:
    return any(Path("/dev").glob("wwan*mbim*")) and any(Path("/sys/class/net").glob("wwan*"))


def _modem_present() -> bool:
    return "/Modem/" in _run(["/usr/bin/mmcli", "-L"], timeout=12, check=False).stdout


def _wwan_connected() -> bool:
    network = _run(
        ["/usr/bin/nmcli", "-t", "-f", "TYPE,STATE", "device", "status"],
        timeout=5,
        check=False,
    ).stdout
    return re.search(r"^gsm:connected$", network, re.MULTILINE) is not None


def _default_recovery(settings: dict[str, Any], *, sleep=time.sleep) -> None:
    """Generic accepted helper behavior without a private connection name."""
    _run(["/usr/bin/udevadm", "settle", "--timeout=10"], timeout=15, check=False)
    if not _modem_present():
        _run(["/usr/bin/systemctl", "restart", "ModemManager.service"], timeout=30)
        for _ in range(settings["modem_timeout_seconds"]):
            if _modem_present():
                break
            sleep(1)
        else:
            raise HibernateError("ModemManager did not enumerate T700 after one recovery")
    _run(["/usr/bin/nmcli", "radio", "wwan", "on"], timeout=10, check=False)
    for _ in range(settings["connect_timeout_seconds"]):
        if _wwan_connected():
            return
        sleep(1)
    profiles = _run(
        [
            "/usr/bin/nmcli",
            "-t",
            "-f",
            "UUID,TYPE,AUTOCONNECT",
            "connection",
            "show",
        ],
        timeout=10,
        check=False,
    ).stdout
    candidates = []
    for line in profiles.splitlines():
        fields = line.split(":")
        if len(fields) == 3 and fields[1] == "gsm" and fields[2] == "yes":
            candidates.append(fields[0])
    if len(candidates) == 1:
        _run(
            ["/usr/bin/nmcli", "connection", "up", "uuid", candidates[0]],
            timeout=60,
            check=False,
        )


def wwan_recover(*, sleep=time.sleep) -> int:
    settings = configuration.load()["hibernate"]["t700"]
    if not settings["enabled"]:
        return 0
    episode = _load_episode()
    if (
        episode.get("status") not in {"IMAGE_GATE_PASSED", "NATIVE_RETURNED"}
        or episode.get("boot_id") != boot_id()
    ):
        raise HibernateError("WWAN recovery has no matching resumed episode")
    sleep(settings["settle_seconds"])
    stable = 0
    for _ in range(settings["stability_timeout_seconds"]):
        if _t700_identity(settings) is not None and _ports_ready():
            stable += 1
            if stable >= settings["stable_seconds"]:
                break
        else:
            stable = 0
        sleep(1)
    if stable < settings["stable_seconds"]:
        raise HibernateError("T700 identity did not reach a stable post-Hibernate generation")
    command = settings["recovery_command"]
    if command:
        _run(command, timeout=settings["recovery_timeout_seconds"])
    else:
        _default_recovery(settings, sleep=sleep)
    for _ in range(settings["connect_timeout_seconds"]):
        if _modem_present() and _wwan_connected():
            return 0
        sleep(1)
    raise HibernateError("ModemManager or NetworkManager WWAN verification timed out")


def post_check() -> int:
    episode = _load_episode()
    if episode.get("boot_id") != boot_id():
        raise HibernateError("cold boot substituted for image resume")
    coordinator.resume()
    if state.load().get("state") != "IDLE":
        raise HibernateError("terminal external-storage audit did not release the fence")
    episode.update(status="COMPLETE", completed=time.time())
    _write_episode(episode)
    _enable_boot_check(False)
    return 0


def abort_reconcile() -> int:
    config = configuration.load()
    current = state.load()
    if current.get("state") == "SLEEP_COMMITTED":
        coordinator.resume()
    elif current.get("state") != "IDLE":
        healthy, reasons = coordinator.protected_mounts_healthy(config)
        if not healthy:
            raise HibernateError("protected mount failed during abort: " + ",".join(reasons))
        state.clear_without_resume(
            config, "HIBERNATE_ABORT_RECONCILED", {"power_state_actions": "none"}
        )
    if EPISODE.exists():
        episode = _load_episode()
        episode.update(status="ABORTED", completed=time.time())
        _write_episode(episode)
    with contextlib.suppress(Exception):
        _enable_boot_check(False)
    return 0


def boot_check() -> int:
    if not EPISODE.exists():
        return 0
    episode = _load_episode()
    if (
        episode.get("status") in {"ARMED", "IMAGE_GATE_PASSED", "NATIVE_RETURNED"}
        and episode.get("boot_id") != boot_id()
    ):
        episode.update(status="COLD_BOOT_DETECTED", cold_boot=time.time())
        _write_episode(episode)
    _enable_boot_check(False)
    return 0
