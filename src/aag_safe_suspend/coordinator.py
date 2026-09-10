from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from . import audit, identity, state, thermal
from . import config as configuration

CONFIG_PATH = Path(
    os.environ.get("AAG_SAFE_SUSPEND_CONFIG", "/etc/aag-external-storage-safe-suspend/config.json")
)
RULE_PATH = Path(
    os.environ.get(
        "AAG_SAFE_SUSPEND_UDEV_RULE", "/etc/udev/rules.d/99-aag-external-storage-safe-suspend.rules"
    )
)
MARKER_PATH = "/run/aag-external-storage-safe-suspend/automount-suppressed.json"
TEST_MODE = os.environ.get("AAG_SAFE_SUSPEND_TEST_MODE") == "1"


class Refusal(RuntimeError):
    pass


def log(message: str) -> None:
    print(message, flush=True)
    if not TEST_MODE:
        with contextlib.suppress(Exception):
            subprocess.run(
                ["/usr/bin/logger", "-t", "aag-safe-suspend", "--", message],
                check=False,
                timeout=3,
            )


def require_root() -> None:
    if not TEST_MODE and os.geteuid() != 0:
        raise Refusal("production action requires root")


def load_config() -> dict[str, Any]:
    return configuration.load(CONFIG_PATH)


def _run(
    argv: list[str], timeout: float, required: bool = True
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refusal(f"command did not complete: {argv[0]}: {exc}") from exc
    if required and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-1000:]
        raise Refusal(f"command failed ({result.returncode}): {argv[0]}: {detail}")
    return result


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_rule(config: dict[str, Any]) -> None:
    expected = identity.udev_rule(config, MARKER_PATH)
    try:
        actual = RULE_PATH.read_text()
    except OSError as exc:
        raise Refusal(f"targeted automount rule is unavailable: {exc}") from exc
    if actual != expected:
        raise Refusal("targeted automount rule does not match validated configuration")


def protected_mounts_healthy(config: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    for protected in config["protected_mounts"]:
        try:
            result = _run(
                [
                    "/usr/bin/findmnt",
                    "-J",
                    "-M",
                    protected["path"],
                    "-o",
                    "TARGET,SOURCE,UUID,FSTYPE,OPTIONS",
                ],
                timeout=5,
                required=True,
            )
            rows = json.loads(result.stdout).get("filesystems") or []
        except (Refusal, TypeError, ValueError) as exc:
            reasons.append(f"{protected['path']}:unobservable:{exc}")
            continue
        if len(rows) != 1 or rows[0].get("uuid") != protected["uuid"]:
            reasons.append(f"{protected['path']}:identity-changed")
        elif "shutdown" in str(rows[0].get("options", "")).split(","):
            reasons.append(f"{protected['path']}:filesystem-shutdown")
    return not reasons, reasons


def _processes(config: dict[str, Any]) -> list[dict[str, Any]]:
    names = set(config["managed_backup"]["process_names"])
    result = list(state.live_jobs())
    registered = {(row.get("pid"), str(row.get("start"))) for row in result if row.get("pid")}
    try:
        pids = sorted(int(path.name) for path in Path("/proc").iterdir() if path.name.isdigit())
    except OSError:
        return result + [{"gap": "proc-enumeration-failed"}]
    for pid in pids:
        before = audit.proc_stat(Path("/proc"), pid)
        if not before or before["state"] == "Z":
            continue
        try:
            executable = os.readlink(f"/proc/{pid}/exe")
        except PermissionError:
            executable = ""
            if before["comm"] in names:
                result.append({"gap": f"managed-process-exe-permission:{pid}"})
        except OSError:
            executable = ""
        name = Path(executable).name or before["comm"]
        if name not in names or (pid, before["start"]) in registered:
            continue
        after = audit.proc_stat(Path("/proc"), pid)
        if after and after["start"] == before["start"] and after["state"] != "Z":
            result.append(
                {
                    "pid": pid,
                    "start": before["start"],
                    "kind": "managed-process",
                    "name": name,
                }
            )
    return result


def _progress(owners: list[dict[str, Any]], generation: dict[str, Any]) -> dict[str, Any]:
    roots = {int(row["pid"]) for row in owners if row.get("pid")}
    stats: dict[int, dict[str, Any]] = {}
    for path in Path("/proc").glob("[0-9]*"):
        value = audit.proc_stat(Path("/proc"), int(path.name))
        if value:
            stats[value["pid"]] = value
    selected = set(roots)
    changed = True
    while changed:
        changed = False
        for pid, value in stats.items():
            if value["ppid"] in selected and pid not in selected:
                selected.add(pid)
                changed = True
    totals = {name: 0 for name in ("rchar", "wchar", "read_bytes", "write_bytes", "syscr", "syscw")}
    cpu = 0
    for pid in selected:
        value = stats.get(pid)
        if not value:
            continue
        cpu += value["utime"] + value["stime"]
        try:
            for line in Path(f"/proc/{pid}/io").read_text().splitlines():
                key, raw = line.split(":", 1)
                if key in totals:
                    totals[key] += int(raw.strip())
        except (OSError, TypeError, ValueError):
            pass
    block_stats: dict[str, str] = {}
    for part in generation.get("token", {}).get("partitions", []):
        try:
            block_stats[part["uuid"]] = " ".join(
                Path("/sys/class/block", part["name"], "stat").read_text().split()
            )
        except OSError:
            block_stats[part["uuid"]] = "unavailable"
    stable = {"cpu": cpu, "io": totals, "block_stats": block_stats}
    return {
        **stable,
        "signature": hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest(),
        "time": time.time(),
    }


def progress_classification(elapsed: float, stale_for: float, policy: dict[str, Any]) -> str:
    if elapsed < policy["soft_wait_seconds"]:
        return "INITIAL_GRACE"
    if stale_for < policy["progress_stale_seconds"]:
        return "PROGRESS_EXTENSION"
    return "STALLED_FAIL_CLOSED"


def _run_integration(argv: list[str], phase: str) -> None:
    if not argv:
        return
    result = _run(argv, timeout=120, required=False)
    if result.returncode != 0:
        raise Refusal(f"{phase} integration refused with status {result.returncode}")


def wait_for_managed_backup(config: dict[str, Any], generation: dict[str, Any]) -> None:
    policy = config["managed_backup"]
    started = time.monotonic()
    last_progress = started
    prior_signature: str | None = None
    quiesce_attempted = False
    while True:
        owners = _processes(config)
        gaps = [row for row in owners if row.get("gap")]
        if gaps:
            raise Refusal("managed backup observability is incomplete")
        if not owners:
            return
        if not quiesce_attempted and policy["quiesce_command"]:
            quiesce_attempted = True
            log("MANAGED_BACKUP_GRACEFUL_QUIESCE_REQUESTED")
            result = _run(policy["quiesce_command"], timeout=60, required=False)
            if result.returncode != 0:
                raise Refusal("managed backup graceful quiesce command failed")
        progress = _progress(owners, generation)
        now = time.monotonic()
        if prior_signature != progress["signature"]:
            prior_signature = progress["signature"]
            last_progress = now
        elapsed = now - started
        if elapsed >= policy["maximum_wait_seconds"]:
            raise Refusal("managed backup exceeded maximum awake safety window")
        classification = progress_classification(elapsed, now - last_progress, policy)
        log(f"MANAGED_BACKUP_{classification} elapsed={elapsed:.0f}s; no signal sent")
        time.sleep(min(5.0, policy["maximum_wait_seconds"] - elapsed))


def _generation_fingerprint(value: dict[str, Any]) -> str | None:
    if not value.get("present") or not value.get("complete"):
        return None
    return value.get("token", {}).get("fingerprint")


def _host_mount_plan(
    audit_result: dict[str, Any], generation: dict[str, Any]
) -> list[dict[str, str]]:
    devnos = {part["devno"] for part in generation["token"]["partitions"]}
    plan: dict[tuple[str, str], dict[str, str]] = {}
    for record in audit_result.get("records") or []:
        if record.get("kind") != "mount" or record.get("owner") != "HOST":
            continue
        devno = str(record.get("dev", ""))
        target = str(record.get("target", ""))
        if devno not in devnos or not target.startswith("/") or "\x00" in target:
            raise Refusal("unsafe host mount action handle")
        plan[(devno, target)] = {"devno": devno, "target": target}
    if not plan and any(row.get("kind") == "mount" for row in audit_result.get("records") or []):
        raise Refusal("mounts exist without a host namespace anchor")
    return sorted(
        plan.values(),
        key=lambda item: (-len(Path(item["target"]).parts), item["target"], item["devno"]),
    )


def _host_mount_matches(target: str, devno: str) -> bool:
    try:
        mounts = audit.parse_mountinfo(Path("/proc/1/mountinfo").read_text(), "host", "HOST")
    except OSError as exc:
        raise Refusal(f"host mount revalidation failed: {exc}") from exc
    matches = [row for row in mounts if row["target"] == target]
    if not matches:
        return False
    if len(matches) != 1 or matches[0]["dev"] != devno:
        raise Refusal("host mount target changed identity before clean unmount")
    return True


def clean_unmount(
    config: dict[str, Any],
    generation: dict[str, Any],
    audit_result: dict[str, Any],
) -> list[dict[str, str]]:
    expected = _generation_fingerprint(generation)
    actions: list[dict[str, str]] = []
    for item in _host_mount_plan(audit_result, generation):
        if _generation_fingerprint(identity.identify(config)) != expected:
            raise Refusal("external device generation changed before unmount")
        if not _host_mount_matches(item["target"], item["devno"]):
            actions.append({**item, "result": "already-unmounted"})
            continue
        result = _run(["/usr/bin/umount", "--", item["target"]], timeout=30, required=False)
        if result.returncode != 0:
            raise Refusal("clean unmount refused; a new owner may have appeared")
        if _generation_fingerprint(identity.identify(config)) != expected:
            raise Refusal("external device generation changed during unmount")
        actions.append({**item, "result": "clean-unmount-complete"})
    return actions


def _audit_until(
    config: dict[str, Any],
    generation: dict[str, Any],
    allow_mounts: bool,
    deadline: float,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    expected = _generation_fingerprint(generation)
    actions: list[dict[str, str]] = []
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        before = identity.identify(config)
        if _generation_fingerprint(before) != expected:
            raise Refusal("external device generation changed before owner audit")
        last = audit.audit_three(generation)
        after = identity.identify(config)
        if _generation_fingerprint(after) != expected:
            raise Refusal("external device generation changed during owner audit")
        decision, reason = audit.disposition(last, allow_mounts=allow_mounts)
        if decision == "FAIL":
            raise Refusal(reason)
        if decision == "RETRY":
            time.sleep(config["resume"]["poll_seconds"])
            continue
        if decision == "UNMOUNT":
            actions.extend(clean_unmount(config, generation, last))
            continue
        return last, actions
    raise Refusal("bounded owner-audit window exhausted")


def begin() -> int:
    require_root()
    config = load_config()
    validate_rule(config)
    healthy, reasons = protected_mounts_healthy(config)
    if not healthy:
        raise Refusal("protected mount preflight failed: " + ",".join(reasons))
    state.begin(config)
    log("SUSPEND_FENCE_ARMED")
    return 0


def prepare() -> int:
    require_root()
    config = load_config()
    with state.lock():
        current = state.load()
        if current.get("state") != "PREPARING_SLEEP":
            raise Refusal(f"prepare called from {current.get('state')}")
        state.validate_marker(current, config)
    generation = identity.identify(config)
    if not generation.get("complete"):
        raise Refusal(generation.get("gap", "external identity incomplete"))
    wait_for_managed_backup(config, generation)
    actions: list[dict[str, str]] = []
    terminal_audit: dict[str, Any]
    if generation.get("present"):
        terminal_audit, actions = _audit_until(
            config,
            generation,
            allow_mounts=True,
            deadline=time.monotonic() + config["resume"]["audit_window_seconds"],
        )
    else:
        terminal_audit = {"AUDIT_COMPLETE": True, "EXTERNAL_OWNER": "NONE", "records": []}
    healthy, reasons = protected_mounts_healthy(config)
    if not healthy:
        raise Refusal("protected mount changed during preparation: " + ",".join(reasons))
    _run_integration(config["integration"]["pre_suspend_command"], "pre-suspend")
    state.update(
        {"PREPARING_SLEEP"},
        state="SLEEP_COMMITTED",
        prepared_generation=_generation_fingerprint(generation),
        pre_sleep_audit={
            "AUDIT_COMPLETE": terminal_audit.get("AUDIT_COMPLETE"),
            "EXTERNAL_OWNER": terminal_audit.get("EXTERNAL_OWNER"),
            "actions": actions,
        },
    )
    log("EXTERNAL_STORAGE_RELEASED; native suspend may continue")
    return 0


def teardown() -> int:
    require_root()
    result = os.environ.get("SERVICE_RESULT", "success")
    if result != "success":
        with state.lock():
            current = state.load()
        if current.get("state") == "IDLE":
            log("PREPARE_REFUSED_BEFORE_FENCE; no recovery latch required")
            return 0
        state.fail("pre-suspend-service-failed", {"service_result": result})
    return 0


def _terminal_resume(config: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    enumeration_deadline = started + config["resume"]["enumeration_window_seconds"]
    generation: dict[str, Any]
    while True:
        generation = identity.identify(config)
        if generation.get("present") and generation.get("complete"):
            break
        if time.monotonic() >= enumeration_deadline:
            if generation.get("present") is False and generation.get("complete"):
                healthy, reasons = protected_mounts_healthy(config)
                if not healthy:
                    raise Refusal(
                        "protected mount failed while target absent: " + ",".join(reasons)
                    )
                return {"terminal": "TARGET_ABSENT_AFTER_BOUNDED_WINDOW", "actions": []}
            raise Refusal("external generation incomplete at resume deadline")
        time.sleep(config["resume"]["poll_seconds"])
    audit_result, actions = _audit_until(
        config,
        generation,
        allow_mounts=True,
        deadline=time.monotonic() + config["resume"]["audit_window_seconds"],
    )
    if _generation_fingerprint(identity.identify(config)) != _generation_fingerprint(generation):
        raise Refusal("external generation changed at resume terminal")
    healthy, reasons = protected_mounts_healthy(config)
    if not healthy:
        raise Refusal("protected mount failed at resume terminal: " + ",".join(reasons))
    return {
        "terminal": "TARGET_PRESENT_ALL_FILESYSTEMS_UNMOUNTED",
        "generation": _generation_fingerprint(generation),
        "AUDIT_COMPLETE": audit_result.get("AUDIT_COMPLETE"),
        "EXTERNAL_OWNER": audit_result.get("EXTERNAL_OWNER"),
        "actions": actions,
    }


def resume() -> int:
    require_root()
    config = load_config()
    state.update({"SLEEP_COMMITTED"}, state="VERIFYING_RESUME", resume_started=time.monotonic())
    try:
        _run_integration(config["integration"]["post_resume_command"], "post-resume")
        evidence = _terminal_resume(config)
        state.finish(config, "RESUME_COMPLETE", evidence)
        log("RESUME_TERMINAL_PASS: target is absent or all target filesystems are unmounted")
        return 0
    except Exception as exc:
        state.fail("post-resume-terminal-failed", {"error": str(exc)})
        log("RESUME_TERMINAL_FAIL_CLOSED: fence and targeted automount suppression remain")
        raise


def _lid_state() -> str:
    values: list[str] = []
    for path in Path("/proc/acpi/button/lid").glob("*/state"):
        try:
            fields = path.read_text().lower().split()
            if fields:
                values.append(fields[-1])
        except OSError:
            return "unknown"
    if values and all(value == "open" for value in values):
        return "open"
    if values and all(value == "closed" for value in values):
        return "closed"
    return "unknown"


def recover() -> int:
    require_root()
    # A Hibernate dependency failure is owned by the mode-specific abort unit.
    # Do not enter the ordinary closed-lid/hot-bag loop for that transaction.
    from . import hibernate

    if hibernate.transaction_active():
        log("HIBERNATE_ABORT_OWNER_ACTIVE: ordinary suspend failure policy suppressed")
        return 0
    config = load_config()
    with state.lock():
        if state.load().get("state") == "IDLE":
            log("RECOVERY_NOT_REQUIRED: transaction fence is already IDLE")
            return 0
    started = time.monotonic()
    warning_since: float | None = None
    unknown_since: float | None = None
    while True:
        lids = [_lid_state()]
        time.sleep(0.25)
        lids.append(_lid_state())
        temperature = thermal.sample()
        now = time.monotonic()
        if lids == ["open", "open"]:
            state.clear_without_resume(config, "SAFE_LID_OPEN_RECOVERY", {"thermal": temperature})
            log("LID_OPEN_RECOVERY: session preserved; fence released")
            return 0
        if temperature["state"] == "WARNING":
            warning_since = warning_since or now
        else:
            warning_since = None
        if temperature["state"] == "UNKNOWN":
            unknown_since = unknown_since or now
        else:
            unknown_since = None
        emergency = temperature["state"] == "EMERGENCY"
        emergency = emergency or (
            warning_since is not None
            and now - warning_since >= config["thermal"]["warning_grace_seconds"]
        )
        emergency = emergency or (
            unknown_since is not None
            and now - unknown_since >= config["thermal"]["unknown_ceiling_seconds"]
        )
        emergency = emergency or now - started >= config["managed_backup"]["maximum_wait_seconds"]
        if emergency:
            if config["thermal"]["emergency_action"] == "poweroff":
                final_lids = [_lid_state()]
                time.sleep(0.25)
                final_lids.append(_lid_state())
                if final_lids == ["open", "open"]:
                    state.clear_without_resume(
                        config, "SAFE_LID_OPEN_RECOVERY", {"thermal": temperature}
                    )
                    return 0
                log("ORDERLY_POWEROFF_LAST_RESORT: configured physical-safety fallback")
                _run(
                    ["/usr/bin/systemctl", "--no-block", "--check-inhibitors=no", "poweroff"],
                    timeout=8,
                    required=True,
                )
                return 0
            log("EMERGENCY_CONDITION_REACHED: default policy holds fail-closed; no power action")
            return 1
        log(f"FAIL_CLOSED_RECOVERY_WAIT thermal={temperature['state']}; no process signal sent")
        time.sleep(5)


def backup_run(kind: str, command: list[str]) -> int:
    require_root()
    allowed = {
        "/usr/lib/aag-external-storage-safe-suspend/timeshift.real",
        "/usr/lib/aag-external-storage-safe-suspend/timeshift-gtk.real",
    }
    if not command or str(Path(command[0])) not in allowed:
        raise Refusal("backup wrapper executable is not approved")
    registration = state.register_job(kind)
    try:
        return subprocess.run(command, check=False).returncode
    finally:
        with contextlib.suppress(OSError):
            registration.unlink()


def validate_installation() -> dict[str, Any]:
    config = load_config()
    validate_rule(config)
    generation = identity.identify(config)
    if generation.get("present") and not generation.get("complete"):
        raise Refusal(generation.get("gap", "target identity incomplete"))
    healthy, reasons = protected_mounts_healthy(config)
    if not healthy:
        raise Refusal("protected mount validation failed: " + ",".join(reasons))
    return {
        "configuration": "valid",
        "target": "present" if generation.get("present") else "absent",
        "target_identity_complete": generation.get("complete"),
        "protected_mounts": "healthy",
        "udev_rule": "exact",
        "power_state_actions": "none",
    }


def status() -> dict[str, Any]:
    config = load_config()
    with state.lock():
        current = state.load()
    generation = identity.identify(config)
    healthy, reasons = protected_mounts_healthy(config)
    return {
        "state": current.get("state"),
        "target_present": generation.get("present"),
        "target_identity_complete": generation.get("complete"),
        "target_generation": _generation_fingerprint(generation),
        "protected_mounts_healthy": healthy,
        "protected_mount_reasons": reasons,
        "thermal": thermal.sample(),
    }
