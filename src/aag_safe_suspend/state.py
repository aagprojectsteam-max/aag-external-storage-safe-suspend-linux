from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import stat
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

RUNTIME = Path(os.environ.get("AAG_SAFE_SUSPEND_RUNTIME", "/run/aag-external-storage-safe-suspend"))
STATE_ROOT = Path(
    os.environ.get("AAG_SAFE_SUSPEND_STATE", "/var/lib/aag-external-storage-safe-suspend")
)
BOOT_FILE = Path(os.environ.get("AAG_SAFE_SUSPEND_BOOT_FILE", "/proc/sys/kernel/random/boot_id"))
MARKER = RUNTIME / "automount-suppressed.json"


class StateError(RuntimeError):
    pass


def boot_id() -> str:
    return BOOT_FILE.read_text().strip()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".new-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def _expected_uid() -> int:
    return os.getuid() if os.environ.get("AAG_SAFE_SUSPEND_TEST_MODE") == "1" else 0


@contextlib.contextmanager
def lock() -> Iterator[None]:
    RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
    value = RUNTIME.lstat()
    if not stat.S_ISDIR(value.st_mode) or stat.S_ISLNK(value.st_mode):
        raise StateError("runtime path is not a real directory")
    if value.st_uid != _expected_uid() or stat.S_IMODE(value.st_mode) != 0o700:
        raise StateError("runtime directory owner or mode is unsafe")
    lock_path = RUNTIME / "lock"
    with lock_path.open("a+") as stream:
        os.chmod(lock_path, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def base_state() -> dict[str, Any]:
    return {"version": 1, "boot_id": boot_id(), "state": "IDLE", "episode": None}


def load() -> dict[str, Any]:
    path = RUNTIME / "state.json"
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return base_state()
    except (OSError, TypeError, ValueError) as exc:
        raise StateError(f"state is unreadable: {exc}") from exc
    if value.get("version") != 1 or value.get("boot_id") != boot_id():
        stale = dict(value)
        with contextlib.suppress(OSError):
            atomic_json(
                RUNTIME / "last-stale-state.json",
                {
                    "event": "STALE_PREVIOUS_BOOT_STATE_CLEARED",
                    "observed": time.time(),
                    "prior_boot_id": stale.get("boot_id"),
                    "prior_state": stale.get("state"),
                },
            )
        with contextlib.suppress(FileNotFoundError):
            MARKER.unlink()
        return base_state()
    return value


def save(value: dict[str, Any]) -> None:
    next_value = dict(value)
    next_value.update(version=1, boot_id=boot_id(), updated=time.time())
    atomic_json(RUNTIME / "state.json", next_value)


def config_fingerprint(config: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def begin(config: dict[str, Any]) -> dict[str, Any]:
    with lock():
        current = load()
        if current.get("state") != "IDLE":
            raise StateError(f"transaction fence is not IDLE: {current.get('state')}")
        episode = os.environ.get("INVOCATION_ID") or f"sleep-{time.time_ns()}"
        current.update(
            {
                "state": "PREPARING_SLEEP",
                "episode": episode,
                "started_monotonic": time.monotonic(),
                "started": time.time(),
                "config_fingerprint": config_fingerprint(config),
                "retry_count": 0,
            }
        )
        marker = {
            "version": 1,
            "boot_id": boot_id(),
            "episode": episode,
            "config_fingerprint": current["config_fingerprint"],
            "device": config["device"],
            "armed_monotonic": time.monotonic(),
        }
        atomic_json(MARKER, marker)
        save(current)
        return current


def validate_marker(current: dict[str, Any], config: dict[str, Any]) -> None:
    try:
        info = MARKER.lstat()
        value = json.loads(MARKER.read_text())
    except (OSError, TypeError, ValueError) as exc:
        raise StateError(f"automount suppression marker unavailable: {exc}") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise StateError("automount marker is not a regular file")
    if info.st_uid != _expected_uid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise StateError("automount marker owner or mode is unsafe")
    expected = {
        "version": 1,
        "boot_id": boot_id(),
        "episode": current.get("episode"),
        "config_fingerprint": config_fingerprint(config),
        "device": config["device"],
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise StateError("automount marker identity does not match the transaction")


def update(expected: set[str], **changes: Any) -> dict[str, Any]:
    with lock():
        current = load()
        if current.get("state") not in expected:
            raise StateError(f"unexpected state: {current.get('state')}")
        current.update(changes)
        save(current)
        return current


def finish(config: dict[str, Any], terminal: str, evidence: dict[str, Any]) -> None:
    with lock():
        current = load()
        if current.get("state") != "VERIFYING_RESUME":
            raise StateError(f"cannot finish from {current.get('state')}")
        validate_marker(current, config)
        final = dict(current)
        final.update({"state": terminal, "terminal": time.time(), "evidence": evidence})
        atomic_json(RUNTIME / "last-terminal.json", final)
        with contextlib.suppress(FileNotFoundError):
            MARKER.unlink()
        fresh = base_state()
        fresh["last_terminal"] = terminal
        save(fresh)


def clear_without_resume(config: dict[str, Any], terminal: str, evidence: object) -> None:
    """Release a failed transaction only after an explicit safe terminal."""
    with lock():
        current = load()
        if current.get("state") == "IDLE":
            return
        validate_marker(current, config)
        final = dict(current)
        final.update({"state": terminal, "terminal": time.time(), "evidence": evidence})
        atomic_json(RUNTIME / "last-terminal.json", final)
        with contextlib.suppress(FileNotFoundError):
            MARKER.unlink()
        fresh = base_state()
        fresh["last_terminal"] = terminal
        save(fresh)


def fail(reason: str, detail: object | None = None) -> None:
    with lock():
        current = load()
        current.update({"state": "BLOCKED", "reason": reason, "detail": detail})
        save(current)
    incident(reason, detail)


def incident(reason: str, detail: object | None = None) -> None:
    value = {
        "version": 1,
        "boot_id": boot_id(),
        "time": time.time(),
        "reason": reason,
        "detail": detail,
    }
    try:
        directory = STATE_ROOT / "incidents"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_json(directory / f"incident-{time.time_ns()}.json", value)
    except OSError:
        atomic_json(RUNTIME / "undurable-incident.json", value)


def pid_identity(pid: int, proc: Path = Path("/proc")) -> str | None:
    try:
        raw = (proc / str(pid) / "stat").read_text()
        return raw.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def live_jobs() -> list[dict[str, Any]]:
    directory = RUNTIME / "jobs"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    result: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            value = json.loads(path.read_text())
            if value.get("boot_id") == boot_id() and pid_identity(int(value["pid"])) == str(
                value["start"]
            ):
                result.append(value)
                continue
        except (OSError, TypeError, ValueError, KeyError):
            result.append({"gap": f"unreadable-job:{path.name}"})
            continue
        with contextlib.suppress(OSError):
            path.unlink()
    return result


def register_job(kind: str) -> Path:
    pid = os.getpid()
    start = pid_identity(pid)
    if not start:
        raise StateError("cannot establish backup wrapper process identity")
    with lock():
        current = load()
        if current.get("state") != "IDLE":
            raise StateError(f"backup start blocked by sleep fence: {current.get('state')}")
        path = RUNTIME / "jobs" / f"{pid}-{start}.json"
        atomic_json(
            path,
            {
                "version": 1,
                "boot_id": boot_id(),
                "pid": pid,
                "start": start,
                "kind": kind,
                "registered": time.time(),
            },
        )
        return path
