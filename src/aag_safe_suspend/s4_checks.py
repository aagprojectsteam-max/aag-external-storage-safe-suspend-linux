"""Read-only reference S4 gates. Machine identities live in private configuration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from . import production as p
from . import s4_transaction as s4
from .transaction import Refusal

CONFIG = Path("/etc/aag-sleep-transaction/hibernate.json")


def configuration():
    p.trusted(CONFIG)
    data = json.loads(CONFIG.read_text())
    if data.get("schema") != 1 or data.get("wwan_owner") != s4.OWNER:
        raise Refusal("invalid S4 configuration/WWAN owner")
    return data


def memory(swap):
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        match = re.fullmatch(r"([A-Za-z_()]+):\s+(\d+)\s+kB", line)
        if match:
            values[match[1]] = int(match[2]) * 1024
    rows = [line.split() for line in Path("/proc/swaps").read_text().splitlines()[1:]]
    selected = [row for row in rows if len(row) == 5 and row[0] == swap]
    if len(selected) != 1 or selected[0][1] != "file":
        raise Refusal("selected Hibernate swapfile is not active")
    row = selected[0]
    free = (int(row[2]) - int(row[3])) * 1024
    return s4.memory_gate(values, int(Path("/sys/power/image_size").read_text()), free)


def findmnt(target):
    result = p.run(
        ["/usr/bin/findmnt", "-J", "-M", target, "-o", "TARGET,SOURCE,MAJ:MIN,FSTYPE,UUID,OPTIONS"]
    )
    # systemd automounts expose both an autofs underlay and the mounted data
    # filesystem at the same target. The underlay has no storage UUID and is
    # not a second data filesystem. Still refuse two real overlays or autofs
    # alone: neither proves the intended backing device is mounted.
    rows = [
        row
        for row in json.loads(result.stdout).get("filesystems", [])
        if row.get("fstype") != "autofs" and row.get("target") == target
    ]
    if len(rows) != 1:
        raise Refusal("mount identity is ambiguous: " + target)
    return rows[0]


def resume(cfg):
    swap = Path(cfg["swap_file"])
    info = swap.lstat()
    root = findmnt("/")
    command = {}
    for word in Path("/proc/cmdline").read_text().split():
        k, _, v = word.partition("=")
        command.setdefault(k, []).append(v)
    text = p.run(["/usr/sbin/filefrag", "-v", str(swap)], timeout=30).stdout
    match = re.search(r"^\s*0:\s+0\.\.\s*\d+:\s*(\d+)\.\.", text, re.M)
    first = int(match[1]) if match else None
    checks = {
        "regular": stat.S_ISREG(info.st_mode)
        and info.st_uid == 0
        and stat.S_IMODE(info.st_mode) == 0o600,
        "inode": info.st_ino == cfg["swap_inode"],
        "size": info.st_size == cfg["swap_bytes"],
        "filesystem": root.get("fstype") == "ext4" and root.get("uuid") == cfg["resume_uuid"],
        "device": Path("/sys/power/resume").read_text().strip() == root.get("maj:min"),
        "cmdline_device": command.get("resume") == ["UUID=" + cfg["resume_uuid"]],
        "cmdline_offset": command.get("resume_offset") == [str(cfg["resume_offset"])],
        "offset": first
        == cfg["resume_offset"]
        == int(Path("/sys/power/resume_offset").read_text()),
        "page_size": os.sysconf("SC_PAGE_SIZE") == os.statvfs(swap).f_frsize == 4096,
        "resume_enabled": "noresume" not in command,
    }
    return {"result": "PASS" if all(checks.values()) else "FAIL", "checks": checks}


def files(cfg):
    checks = {}
    for path, expected in cfg["pinned_files"].items():
        file = Path(path)
        p.trusted(file)
        checks[path] = hashlib.sha256(file.read_bytes()).hexdigest() == expected
    return {"result": "PASS" if checks and all(checks.values()) else "FAIL", "checks": checks}


def process_identity(pid):
    raw = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": int(pid), "start": raw[19], "exe": os.readlink(Path("/proc") / str(pid) / "exe")}


def sessions():
    identities = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text().strip() in {
                "gnome-shell",
                "gnome-session-b",
                "Xwayland",
            }:
                identities.append(process_identity(int(entry.name)))
        except (OSError, ValueError, IndexError):
            continue
    if not any(Path(x["exe"]).name == "gnome-shell" for x in identities):
        raise Refusal("desktop session identity unavailable")
    return sorted(identities, key=lambda x: x["pid"])


def same_sessions(before):
    try:
        return bool(before) and all(process_identity(x["pid"]) == x for x in before)
    except (OSError, ValueError, IndexError):
        return False


def modem_generation(cfg):
    pci = Path(cfg["modem_pci"])
    try:
        checks = (
            (pci / "vendor").read_text().strip() == cfg["modem_vendor"]
            and (pci / "device").read_text().strip() == cfg["modem_device"]
            and (pci / "driver").resolve().name == cfg["modem_driver"]
            and Path("/sys/module/" + cfg["modem_driver"] + "/srcversion").read_text().strip()
            == cfg["modem_srcversion"]
        )
        nodes = []
        for node in Path("/sys/class/wwan").glob("*mbim*"):
            resolved = node.resolve()
            if not resolved.is_relative_to(pci.resolve()):
                continue
            dev = Path("/dev") / node.name
            st = dev.stat()
            if not stat.S_ISCHR(st.st_mode):
                continue
            nodes.append(
                {
                    "path": str(resolved),
                    "inode": node.stat().st_ino,
                    "dev_inode": st.st_ino,
                    "rdev": st.st_rdev,
                }
            )
        nets = []
        for net in Path("/sys/class/net").glob("wwan*"):
            resolved = net.resolve()
            if resolved.is_relative_to(pci.resolve()):
                nets.append(
                    {
                        "path": str(resolved),
                        "inode": net.stat().st_ino,
                        "ifindex": (net / "ifindex").read_text().strip(),
                    }
                )
        fingerprint = {"pci_inode": pci.stat().st_ino, "nodes": nodes, "nets": nets}
        return {
            "hardware_ok": bool(checks and nodes and nets),
            "generation": hashlib.sha256(
                json.dumps(fingerprint, sort_keys=True).encode()
            ).hexdigest()
            if checks and nodes and nets
            else None,
        }
    except (OSError, ValueError):
        return {"hardware_ok": False, "generation": None}


def locklock(*, allow_lid_ignore=False):
    result = json.loads(p.run(["/usr/bin/input-lock", "status", "--json"]).stdout)
    # Physical qualification keeps the original strict open-lid/OFF contract.
    # Production-native Hibernate may run while lid-ignore is armed: that
    # inhibitor is scoped to handle-lid-switch and is not a general sleep block.
    if allow_lid_ignore:
        lid_policy_ok = (
            result.get("lid_inhibitor_active") is True
            if result.get("ignore_lid_close") is True
            else result.get("lid_inhibitor_active") is False
        )
    else:
        lid_policy_ok = (
            result.get("ignore_lid_close") is False
            and result.get("lid_is_closed") is False
            and result.get("lid_inhibitor_active") is False
        )
    return {
        "result": "PASS"
        if result.get("daemon") == "running"
        and lid_policy_ok
        and result.get("sleep_inhibitor_active") is False
        and not result.get("grabbed_devices")
        and not result.get("incomplete_devices")
        and not result.get("release_errors")
        else "FAIL",
        "state": result,
    }


def readiness(host, cfg, *, before_image=False, allow_lid_ignore=False):
    kernel = os.uname().release
    result = {
        "MEMORY_GATE": memory(cfg["swap_file"]),
        "RESUME_DEVICE": resume(cfg),
        "SOURCE_INSTALLED_HASH_MATCH": files(cfg),
    }
    result["SWAP_GATE"] = (
        "PASS"
        if result["MEMORY_GATE"]["checks"]["swap_free_covers_ram_plus_ten_percent"]
        else "FAIL"
    )
    result["RESUME_OFFSET"] = result["RESUME_DEVICE"]["result"]
    result["KERNEL_HIBERNATION"] = (
        "PASS"
        if (
            kernel == cfg["kernel"]
            and "disk" in Path("/sys/power/state").read_text().split()
            and "[platform]" in Path("/sys/power/disk").read_text()
        )
        else "FAIL"
    )
    result["INITRAMFS_RESUME"] = (
        "PASS"
        if (
            kernel == cfg["kernel"]
            and cfg.get("initramfs_static_verified") is True
            and hashlib.sha256(Path(cfg["initramfs"]).read_bytes()).hexdigest()
            == cfg["initramfs_sha256"]
        )
        else "FAIL"
    )
    data_ok, detail = host.r.internal_data_healthy()
    data_mount = findmnt("/mnt/data")
    result["DATA_GATE"] = {
        "result": "PASS" if data_ok else "FAIL",
        "detail": detail,
        "mount": data_mount,
    }
    audit = host.audit_storage()
    result["UGREEN_GATE"] = {
        "result": "PASS"
        if audit.get("AUDIT_COMPLETE")
        and (
            audit.get("EXTERNAL_OWNER") == "NONE"
            if before_image
            else not host.r.unsafe_mount_topology(audit.get("records", []))
        )
        else "FAIL",
        "audit": audit,
    }
    result["FM350_T700_GATE"] = "PASS" if modem_generation(cfg)["hardware_ok"] else "FAIL"
    result["LOCKLOCK_GATE"] = locklock(allow_lid_ignore=allow_lid_ignore)
    result["WWAN_SINGLE_OWNER"] = host.owner_graph()
    result["CONSUMER_RESTORE_GATE"] = (
        "PASS" if cfg.get("simulated_gates", {}).get("CONSUMER_RESTORE") == "PASS" else "FAIL"
    )
    # A required removable-storage consumer needs an explicit mount restoration
    # contract. Do not stop it and discover only after resume that the accepted
    # no-forced-remount policy cannot satisfy its dependency.
    if any(
        row.get("storage_dependency") == "UGREEN"
        and row.get("restart_after_resume") == "if_was_running"
        for row in host.cfg["workloads"]
    ):
        result["CONSUMER_RESTORE_GATE"] = "FAIL"
    for gate in ("HIBERNATE_TRANSACTION_MODEL", "ABORT_RECONCILIATION", "FULL_SUSPEND_REGRESSION"):
        result[gate] = cfg.get("simulated_gates", {}).get(gate, "FAIL")
    result["COLD_BOOT_RECONCILIATION"] = cfg.get("simulated_gates", {}).get(
        "COLD_BOOT_RECONCILIATION", "FAIL"
    )
    result["ROLLBACK_READY"] = "PASS" if cfg.get("rollback_ready") is True else "FAIL"
    result["STORAGE_GATE"] = (
        "PASS" if data_ok and result["UGREEN_GATE"]["result"] == "PASS" else "FAIL"
    )
    result["result"] = (
        "PASS"
        if all(
            (value.get("result") if isinstance(value, dict) else value) == "PASS"
            for value in result.values()
        )
        else "FAIL"
    )
    return result
