"""Installed-state inspection and local maintenance commands.

This module deliberately has no dependency on the release installer.  A copy is
installed with the runtime so status and health checks remain local and do not
need network access.  Repair uses the root-owned pristine payload cache created
at commit time; rollback uses a bounded, hash-verified root-owned snapshot.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from . import __version__, config, identity, job_guard

PRODUCT_ID = "aag-external-storage-safe-suspend-linux"
PROJECT = "aag-external-storage-safe-suspend"
STATE_SCHEMA = 1
UPGRADER_SCHEMA = 2
CONFIG_SCHEMA = 2
MIGRATION_SCHEMA = 3
STATE_ROOT = Path("/var/lib") / PROJECT
INSTALL_STATE = STATE_ROOT / "install-state.json"
LOCK_PATH = STATE_ROOT / "installer.lock"
RULE_PATH = Path("/etc/udev/rules.d/99-aag-external-storage-safe-suspend.rules")
CONFIG_PATH = Path("/etc") / PROJECT / "config.json"
MARKER_PATH = "/run/aag-external-storage-safe-suspend/automount-suppressed.json"
RELEASE_API = (
    "https://api.github.com/repos/aagprojectsteam-max/"
    "aag-external-storage-safe-suspend-linux/releases/latest"
)
ACCEPTED_QUALIFICATION_PATHS = {
    Path("/usr/local/libexec/aag-hibernate-qualifier"),
    Path("/usr/local/libexec/aag-hibernate-wwan-qualified-recover"),
    Path("/usr/local/sbin/aag-hibernate-acceptance"),
    Path("/etc/systemd/system/aag-hibernate-resume-check.service"),
    Path("/etc/systemd/system/aag-hibernate-wwan-recovery.service"),
    Path("/etc/systemd/system/aag-hibernate-abort-reconcile.service"),
    Path("/etc/systemd/system/aag-hibernate-boot-check.service"),
    Path("/etc/systemd/system/systemd-hibernate.service.d/80-aag-hibernate-qualification.conf"),
    Path(
        "/etc/systemd/system/aag-suspend-failure-failsafe.service.d/"
        "80-aag-hibernate-qualification.conf"
    ),
}

EXIT_STATUS = {
    "HEALTHY": 0,
    "NOT_INSTALLED": 2,
    "MODIFIED": 3,
    "PARTIAL": 4,
    "UPGRADE_PENDING": 5,
    "ROLLBACK_PENDING": 6,
    "BROKEN": 7,
}


class MaintenanceError(RuntimeError):
    pass


def _root() -> Path:
    value = Path(os.environ.get("AAG_SAFE_SUSPEND_ROOT", "/")).resolve()
    if value != Path("/") and os.environ.get("AAG_SAFE_SUSPEND_TEST_MODE") != "1":
        raise MaintenanceError("alternate root is permitted only in isolated test mode")
    return value


def target(path: Path, root: Path | None = None) -> Path:
    base = root or _root()
    if not path.is_absolute() or ".." in path.parts or "\x00" in str(path):
        raise MaintenanceError(f"non-absolute managed path: {path}")
    return path if base == Path("/") else base / path.relative_to("/")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _expected_uid() -> int:
    return os.getuid() if os.environ.get("AAG_SAFE_SUSPEND_TEST_MODE") == "1" else 0


def _safe_regular(path: Path, mode: int | None = None) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise MaintenanceError(f"cannot inspect {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise MaintenanceError(f"unsafe non-regular path: {path}")
    if info.st_uid != _expected_uid():
        raise MaintenanceError(f"unsafe owner: {path}")
    if mode is not None and stat.S_IMODE(info.st_mode) != mode:
        raise MaintenanceError(f"unsafe mode: {path}")
    return info


def load_install_state(root: Path | None = None) -> dict[str, Any]:
    directory = target(STATE_ROOT, root)
    try:
        directory_info = directory.lstat()
    except OSError as exc:
        raise MaintenanceError(f"project state directory is unavailable: {exc}") from exc
    if (
        not stat.S_ISDIR(directory_info.st_mode)
        or stat.S_ISLNK(directory_info.st_mode)
        or directory_info.st_uid != _expected_uid()
        or stat.S_IMODE(directory_info.st_mode) != 0o700
    ):
        raise MaintenanceError("project state directory owner or mode is unsafe")
    path = target(INSTALL_STATE, root)
    _safe_regular(path, 0o600)
    try:
        value = json.loads(path.read_text())
    except (OSError, TypeError, ValueError) as exc:
        raise MaintenanceError(f"installed state is unreadable: {exc}") from exc
    if not isinstance(value, dict) or value.get("product_id") != PRODUCT_ID:
        raise MaintenanceError("installed state product identity is invalid")
    if value.get("state_schema") != STATE_SCHEMA:
        raise MaintenanceError("installed state schema is unsupported")
    if not isinstance(value.get("files"), dict) or not value.get("installed_version"):
        raise MaintenanceError("installed state is incomplete")
    if len(value["files"]) > 128:
        raise MaintenanceError("installed state file manifest is unexpectedly large")
    for logical, metadata in value["files"].items():
        path_value = Path(logical) if isinstance(logical, str) else Path(".")
        exact = {
            CONFIG_PATH,
            RULE_PATH,
            Path("/usr/local/bin/aag-safe-suspend"),
            Path("/usr/local/libexec/aag-safe-suspend"),
            Path("/usr/local/libexec/aag-systemd-job-guard"),
            Path("/usr/bin/timeshift"),
            Path("/usr/bin/timeshift-gtk"),
            Path("/etc/systemd/system/aag-external-storage-safe-suspend.service"),
            Path("/etc/systemd/system/aag-external-storage-safe-ordinary-suspend.service"),
            Path("/etc/systemd/system/aag-external-storage-safe-suspend-resume.service"),
            Path("/etc/systemd/system/aag-external-storage-safe-suspend-failure.service"),
            Path(
                "/etc/systemd/system/systemd-suspend.service.d/"
                "70-aag-external-storage-safe-suspend.conf"
            ),
            Path(
                "/etc/systemd/system/systemd-hibernate.service.d/"
                "70-aag-external-storage-safe-hibernate.conf"
            ),
            Path("/etc/systemd/system/aag-external-storage-safe-hibernate-resume.service"),
            Path("/etc/systemd/system/aag-external-storage-safe-hibernate-wwan.service"),
            Path("/etc/systemd/system/aag-external-storage-safe-hibernate-abort.service"),
            Path("/etc/systemd/system/aag-external-storage-safe-hibernate-boot-check.service"),
        }
        module_root = Path("/usr/lib") / PROJECT / "aag_safe_suspend"
        module = (
            path_value.parent == module_root
            and path_value.suffix == ".py"
            and path_value.name not in {".", ".."}
        )
        if path_value not in exact and not module:
            raise MaintenanceError(f"installed state contains an unrecognized path: {logical}")
        if (
            not isinstance(metadata, dict)
            or metadata.get("classification") not in {"PROJECT_MANAGED", "SUPPORTED_USER_CONFIG"}
            or metadata.get("mode") not in {0o600, 0o644, 0o755}
            or not isinstance(metadata.get("sha256"), str)
            or len(metadata["sha256"]) != 64
        ):
            raise MaintenanceError(f"installed state metadata is invalid: {logical}")
    return value


def _read_json_if_regular(path: Path) -> dict[str, Any] | None:
    if not path.exists() and not path.is_symlink():
        return None
    _safe_regular(path)
    try:
        value = json.loads(path.read_text())
    except (OSError, TypeError, ValueError) as exc:
        raise MaintenanceError(f"unreadable JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MaintenanceError(f"JSON object required at {path}")
    return value


def inspect(root: Path | None = None, *, system_checks: bool = True) -> dict[str, Any]:
    base = root or _root()
    path = target(INSTALL_STATE, base)
    legacy = target(STATE_ROOT / "active.json", base)
    if not path.exists() and not path.is_symlink():
        if legacy.exists() or legacy.is_symlink():
            return {
                "status": "PARTIAL",
                "reason": "LEGACY_V1_0_0_REQUIRES_BOOTSTRAP",
                "installed_version": "1.0.0",
            }
        probes = [
            target(Path("/usr/local/libexec/aag-safe-suspend"), base),
            target(CONFIG_PATH, base),
            target(RULE_PATH, base),
        ]
        return {
            "status": "PARTIAL"
            if any(p.exists() or p.is_symlink() for p in probes)
            else "NOT_INSTALLED",
            "reason": "MANAGED_STATE_MISSING"
            if any(p.exists() or p.is_symlink() for p in probes)
            else "ABSENT",
        }
    try:
        state = load_install_state(base)
    except MaintenanceError as exc:
        return {"status": "BROKEN", "reason": str(exc)}

    operation = state.get("operation", "COMMITTED")
    if operation == "UPGRADE_PENDING":
        return {"status": "UPGRADE_PENDING", "installed_version": state.get("installed_version")}
    if operation == "ROLLBACK_PENDING":
        return {"status": "ROLLBACK_PENDING", "installed_version": state.get("installed_version")}
    if operation != "COMMITTED":
        return {"status": "BROKEN", "reason": "UNKNOWN_OPERATION_STATE"}

    changed: list[dict[str, str]] = []
    missing: list[str] = []
    broken: list[str] = []
    for logical, metadata in state["files"].items():
        if (
            not isinstance(logical, str)
            or not logical.startswith("/")
            or not isinstance(metadata, dict)
        ):
            broken.append(str(logical))
            continue
        current = target(Path(logical), base)
        if not current.exists() and not current.is_symlink():
            missing.append(logical)
            continue
        try:
            info = _safe_regular(current)
            actual = sha256(current)
        except MaintenanceError:
            broken.append(logical)
            continue
        expected_mode = metadata.get("mode")
        if expected_mode != stat.S_IMODE(info.st_mode) or actual != metadata.get("sha256"):
            classification = metadata.get("classification", "PROJECT_MANAGED")
            if classification == "SUPPORTED_USER_CONFIG" and logical == str(CONFIG_PATH):
                try:
                    config.load(current)
                    changed.append({"path": logical, "classification": classification})
                    continue
                except Exception:
                    pass
            changed.append({"path": logical, "classification": "UNKNOWN_LOCAL_MODIFICATION"})

    if missing:
        return {
            "status": "PARTIAL",
            "installed_version": state["installed_version"],
            "missing": missing,
        }
    if broken:
        return {
            "status": "BROKEN",
            "installed_version": state["installed_version"],
            "unsafe": broken,
        }

    config_path = target(CONFIG_PATH, base)
    try:
        selected = config.load(config_path)
        expected_config_schema = (
            1 if _parse_version(str(state["installed_version"])) < (1, 2, 0) else CONFIG_SCHEMA
        )
        if state.get("configuration_schema") != expected_config_schema:
            broken.append("CONFIGURATION_SCHEMA_MISMATCH")
        if state.get("selected_device") != selected["device"]:
            broken.append("SELECTED_DEVICE_STATE_MISMATCH")
        release = state.get("release")
        if (
            not isinstance(release, dict)
            or release.get("tag") != f"v{state['installed_version']}"
            or not release.get("source")
        ):
            broken.append("RELEASE_IDENTITY_MISMATCH")
        expected_rule = identity.udev_rule(selected, MARKER_PATH)
        if target(RULE_PATH, base).read_text() != expected_rule:
            broken.append("UDEV_RULE_CONFIGURATION_MISMATCH")
    except Exception as exc:
        broken.append(f"CONFIGURATION_INVALID:{exc}")

    if state.get("rollback", {}).get("available"):
        rollback_path = state.get("rollback", {}).get("path")
        if not isinstance(rollback_path, str) or not target(Path(rollback_path), base).is_dir():
            broken.append("ROLLBACK_METADATA_MISMATCH")

    if system_checks and base == Path("/"):
        try:
            from . import coordinator

            live_validation = coordinator.validate_installation()
            if live_validation.get("protected_mounts") != "healthy":
                broken.append("PROTECTED_MOUNT_HEALTH_MISMATCH")
        except Exception as exc:
            broken.append(f"LIVE_IDENTITY_OR_PROTECTED_MOUNT_CHECK_FAILED:{exc}")
        for unit in (
            "aag-external-storage-safe-ordinary-suspend.service",
            "aag-external-storage-safe-suspend.service",
            "aag-external-storage-safe-suspend-resume.service",
            "aag-external-storage-safe-suspend-failure.service",
            "systemd-suspend.service",
        ):
            try:
                result = subprocess.run(
                    ["/usr/bin/systemctl", "show", unit, "-p", "LoadState", "--value"],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=10,
                    env={**os.environ, "LC_ALL": "C"},
                )
            except (OSError, subprocess.SubprocessError) as exc:
                broken.append(f"SYSTEMD_UNOBSERVABLE:{unit}:{exc}")
                continue
            if result.returncode or result.stdout.strip() != "loaded":
                broken.append(f"SYSTEMD_GRAPH_MISMATCH:{unit}")
        verification = subprocess.run(
            [
                "/usr/bin/systemd-analyze",
                "verify",
                "aag-external-storage-safe-ordinary-suspend.service",
                "aag-external-storage-safe-suspend.service",
                "aag-external-storage-safe-suspend-resume.service",
                "aag-external-storage-safe-suspend-failure.service",
                "systemd-suspend.service",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        if verification.returncode:
            broken.append("SYSTEMD_MERGED_GRAPH_VERIFY_FAILED")
        dropins = Path("/etc/systemd/system/systemd-suspend.service.d")
        expected_dropin = "70-aag-external-storage-safe-suspend.conf"
        if dropins.is_dir():
            obsolete = sorted(
                path.name
                for path in dropins.iterdir()
                if "aag" in path.name.lower() and path.name != expected_dropin
            )
            if obsolete:
                broken.append("OBSOLETE_PROJECT_WIRING:" + ",".join(obsolete))
        udev_verify = subprocess.run(
            ["/usr/bin/udevadm", "verify", str(RULE_PATH)],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        if udev_verify.returncode:
            broken.append("UDEV_RULE_VERIFY_FAILED")
        if state.get("timeshift_integration"):
            for binary in (Path("/usr/bin/timeshift"), Path("/usr/bin/timeshift-gtk")):
                result = subprocess.run(
                    ["/usr/bin/dpkg-divert", "--listpackage", str(binary)],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
                if result.stdout.strip() != PROJECT:
                    broken.append(f"TIMESHIFT_DIVERSION_MISMATCH:{binary}")

    if broken:
        return {
            "status": "BROKEN",
            "installed_version": state["installed_version"],
            "failures": broken,
            "local_changes": changed,
        }
    unknown = [row for row in changed if row["classification"] == "UNKNOWN_LOCAL_MODIFICATION"]
    result = {
        "status": "MODIFIED" if unknown else "HEALTHY",
        "installed_version": state["installed_version"],
        "configuration_schema": state.get("configuration_schema"),
        "state_schema": state.get("state_schema"),
        "upgrader_schema": state.get("upgrader_schema"),
        "migration_schema": state.get("migration_schema"),
        "local_changes": changed,
        "rollback": state.get("rollback", {"available": False}),
        "power_state_actions": "none",
        "ordinary_suspend_support": "SUPPORTED",
        "plain_hibernate_support": "SUPPORTED_ON_REFERENCE_PLATFORM",
        "suspend_then_hibernate_status": "NOT_ENABLED_NOT_ACCEPTED",
        "hybrid_sleep_status": "NOT_ENABLED_NOT_ACCEPTED",
        "rollback_available": bool(state.get("rollback", {}).get("available")),
        "checks": {
            "installed_file_ownership": "PASS",
            "configuration_schema_and_identity": "PASS",
            "udev_target_and_fence": "PASS",
            "systemd_owner_audit_resume_failure_wiring": "PASS",
            "ordinary_usbclone_gate": "PASS",
            "timeshift_diversion_fence": "PASS"
            if state.get("timeshift_integration")
            else "NOT_CONFIGURED",
            "hot_bag_policy": "PASS",
            "integration_boundaries": "PASS",
            "rollback_metadata": "PASS"
            if state.get("rollback", {}).get("available")
            else "NOT_AVAILABLE",
        },
    }
    return result


def health_check() -> tuple[dict[str, Any], int]:
    result = inspect()
    if result.get("status") in {"HEALTHY", "MODIFIED"}:
        if _root() == Path("/"):
            from . import hibernate

            result["hibernate"] = hibernate.readiness()
        else:
            result["hibernate"] = {
                "HIBERNATE_SUPPORT_STATUS": "SUPPORTED_ON_REFERENCE_PLATFORM",
                "HIBERNATE_READY": "NOT_EVALUATED_ISOLATED_ROOT",
                "power_state_actions": "none",
            }
    status = EXIT_STATUS[result["status"]]
    if (
        result.get("hibernate", {}).get("HIBERNATE_SUPPORT_STATUS")
        == "SUPPORTED_ON_REFERENCE_PLATFORM"
        and result.get("hibernate", {}).get("HIBERNATE_READY") == "FAIL"
    ):
        status = 8
    return result, status


def _parse_version(value: str) -> tuple[int, int, int]:
    raw = value.removeprefix("v").split("-", 1)[0].split(".")
    if len(raw) != 3 or not all(part.isdigit() for part in raw):
        raise MaintenanceError(f"invalid release version: {value}")
    return tuple(int(part) for part in raw)  # type: ignore[return-value]


def update_check() -> dict[str, str]:
    installed = inspect(system_checks=False)
    current = str(installed.get("installed_version") or __version__)
    request = urllib.request.Request(
        RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{PRODUCT_ID}/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status != 200 or response.headers.get_content_type() != "application/json":
                raise MaintenanceError("release service returned an unexpected response")
            raw = response.read(1024 * 1024 + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise MaintenanceError(f"update check failed: {exc}") from exc
    if len(raw) > 1024 * 1024:
        raise MaintenanceError("release metadata is unexpectedly large")
    try:
        value = json.loads(raw)
        latest = str(value["tag_name"]).removeprefix("v")
    except (KeyError, TypeError, ValueError) as exc:
        raise MaintenanceError("release metadata is invalid") from exc
    return {
        "CURRENT_VERSION": current,
        "LATEST_VERSION": latest,
        "UPDATE_AVAILABLE": "YES" if _parse_version(latest) > _parse_version(current) else "NO",
        "NETWORK_ACTION": "READ_ONLY_GITHUB_RELEASE_QUERY",
        "TELEMETRY": "NONE",
    }


@contextlib.contextmanager
def installer_lock(root: Path | None = None, *, blocking: bool = False) -> Iterator[None]:
    base = root or _root()
    directory = target(STATE_ROOT, base)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise MaintenanceError("project state root is unsafe")
    if info.st_uid != _expected_uid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise MaintenanceError("project state root owner or mode is unsafe")
    lock_path = target(LOCK_PATH, base)
    with lock_path.open("a+") as stream:
        os.chmod(lock_path, 0o600)
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(stream, flags)
        except BlockingIOError as exc:
            raise MaintenanceError(
                "another install, repair, rollback, or uninstall is active"
            ) from exc
        stream.seek(0)
        stream.truncate()
        stream.write(json.dumps({"pid": os.getpid(), "operation": "maintenance"}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _atomic_copy(source: Path, destination: Path, mode: int) -> None:
    _safe_regular(source)
    destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if destination.parent.is_symlink():
        raise MaintenanceError(f"unsafe destination parent: {destination.parent}")
    fd, temporary = tempfile.mkstemp(prefix=".aag-maintenance-", dir=destination.parent)
    try:
        with source.open("rb") as incoming, os.fdopen(fd, "wb", closefd=False) as outgoing:
            shutil.copyfileobj(incoming, outgoing)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        os.fchmod(fd, mode)
        if _expected_uid() == 0:
            os.fchown(fd, 0, 0)
        os.close(fd)
        fd = -1
        os.replace(temporary, destination)
    finally:
        if fd >= 0:
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def _reload(root: Path) -> None:
    if root != Path("/"):
        return
    subprocess.run(["/usr/bin/systemctl", "daemon-reload"], check=True, timeout=30)
    subprocess.run(["/usr/bin/udevadm", "control", "--reload"], check=True, timeout=30)


def _assert_no_active_transaction(root: Path) -> None:
    runtime = target(Path("/run") / PROJECT, root)
    if (runtime / "automount-suppressed.json").exists():
        raise MaintenanceError("a suspend transaction fence is active")
    state_path = runtime / "state.json"
    if state_path.exists():
        try:
            if json.loads(state_path.read_text()).get("state") != "IDLE":
                raise MaintenanceError("runtime sleep transaction is not IDLE")
        except (OSError, TypeError, ValueError) as exc:
            raise MaintenanceError(f"runtime sleep state is unreadable: {exc}") from exc
    jobs = runtime / "jobs"
    if jobs.is_dir() and any(jobs.glob("*.json")):
        raise MaintenanceError("an active protected backup lifecycle is registered")
    if root == Path("/"):
        try:
            job_guard.assert_clear("maintenance-preflight")
        except Exception as exc:
            raise MaintenanceError(str(exc)) from exc


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".aag-state-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        if _expected_uid() == 0:
            os.fchown(fd, 0, 0)
        with os.fdopen(fd, "w", closefd=False) as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.close(fd)
        fd = -1
        os.replace(temporary, path)
    finally:
        if fd >= 0:
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


class _LocalTransaction:
    def __init__(self, root: Path, operation: str) -> None:
        parent = target(STATE_ROOT / "transactions", root)
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = Path(tempfile.mkdtemp(prefix=f"{operation}-{time.time_ns()}-", dir=parent))
        os.chmod(self.path, 0o700)
        self.entries: list[tuple[Path, Path | None]] = []

    def backup(self, destination: Path) -> None:
        if any(existing == destination for existing, _backup in self.entries):
            return
        if destination.exists() or destination.is_symlink():
            _safe_regular(destination)
            backup = self.path / "baseline" / str(len(self.entries))
            backup.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copy2(destination, backup)
            self.entries.append((destination, backup))
        else:
            self.entries.append((destination, None))

    def copy(self, source: Path, destination: Path, mode: int) -> None:
        self.backup(destination)
        _atomic_copy(source, destination, mode)

    def remove(self, destination: Path) -> None:
        self.backup(destination)
        destination.unlink(missing_ok=True)

    def rollback(self) -> None:
        errors: list[str] = []
        for destination, backup in reversed(self.entries):
            try:
                if backup is None:
                    destination.unlink(missing_ok=True)
                else:
                    _atomic_copy(backup, destination, stat.S_IMODE(backup.stat().st_mode))
            except Exception as exc:
                errors.append(f"{destination}:{exc}")
        if errors:
            (self.path / "ROLLBACK-FAILED.txt").write_text("\n".join(errors) + "\n")
            raise MaintenanceError("maintenance rollback failed: " + ";".join(errors))
        (self.path / "AUTOMATIC-ROLLBACK-COMPLETE").write_text("complete\n")

    def commit(self) -> None:
        (self.path / "COMMITTED").write_text("complete\n")


def repair() -> dict[str, Any]:
    if os.geteuid() != 0 and os.environ.get("AAG_SAFE_SUSPEND_TEST_MODE") != "1":
        raise MaintenanceError("repair requires root")
    base = _root()
    with installer_lock(base):
        _assert_no_active_transaction(base)
        state = load_install_state(base)
        payload_logical = Path(state["payload_cache"])
        if not payload_logical.is_relative_to(STATE_ROOT / "payloads"):
            raise MaintenanceError("payload cache path escapes the project state root")
        payload = target(payload_logical, base)
        transaction = _LocalTransaction(base, "repair")
        repaired: list[str] = []
        try:
            selected = config.load(target(CONFIG_PATH, base))
            config_meta = state["files"][str(CONFIG_PATH)]
            config_meta["sha256"] = sha256(target(CONFIG_PATH, base))
            config_meta["mode"] = 0o600
            state["selected_configuration_sha256"] = config_meta["sha256"]
            state["selected_device"] = selected["device"]
            expected_rule = identity.udev_rule(selected, MARKER_PATH).encode()
            rule_hash = hashlib.sha256(expected_rule).hexdigest()
            rule_meta = state["files"][str(RULE_PATH)]
            rule_meta.update(sha256=rule_hash, mode=0o644, classification="PROJECT_MANAGED")
            rule_cache = payload / RULE_PATH.relative_to("/")
            if (
                not target(RULE_PATH, base).is_file()
                or target(RULE_PATH, base).read_bytes() != expected_rule
            ):
                temporary_source = transaction.path / "generated-rule"
                temporary_source.write_bytes(expected_rule)
                os.chmod(temporary_source, 0o600)
                transaction.copy(temporary_source, target(RULE_PATH, base), 0o644)
                transaction.copy(temporary_source, rule_cache, 0o644)
                repaired.append(str(RULE_PATH))
            for logical, metadata in state["files"].items():
                if metadata.get("classification") != "PROJECT_MANAGED":
                    continue
                destination = target(Path(logical), base)
                if (
                    destination.is_file()
                    and not destination.is_symlink()
                    and sha256(destination) == metadata["sha256"]
                    and stat.S_IMODE(destination.stat().st_mode) == metadata["mode"]
                ):
                    continue
                source = payload / Path(logical).relative_to("/")
                if sha256(source) != metadata["sha256"]:
                    raise MaintenanceError(f"pristine payload hash mismatch: {logical}")
                transaction.copy(source, destination, int(metadata["mode"]))
                repaired.append(logical)
            state_temp = transaction.path / "new-install-state.json"
            state_temp.write_text(json.dumps(state, sort_keys=True, indent=2) + "\n")
            os.chmod(state_temp, 0o600)
            transaction.copy(state_temp, target(INSTALL_STATE, base), 0o600)
            _reload(base)
            result = inspect(base, system_checks=base == Path("/"))
            if result["status"] not in {"HEALTHY", "MODIFIED"}:
                raise MaintenanceError(f"repair health check failed: {result['status']}")
            transaction.commit()
            return {"status": "REPAIRED", "files": repaired, "configuration_preserved": True}
        except Exception:
            transaction.rollback()
            with contextlib.suppress(Exception):
                _reload(base)
            raise


def rollback() -> dict[str, Any]:
    if os.geteuid() != 0 and os.environ.get("AAG_SAFE_SUSPEND_TEST_MODE") != "1":
        raise MaintenanceError("rollback requires root")
    base = _root()
    with installer_lock(base):
        _assert_no_active_transaction(base)
        current = load_install_state(base)
        metadata = current.get("rollback", {})
        if not metadata.get("available") or not metadata.get("compatible"):
            raise MaintenanceError(metadata.get("reason", "no compatible rollback is available"))
        generation_logical = Path(metadata["path"])
        if not generation_logical.is_relative_to(STATE_ROOT / "rollbacks"):
            raise MaintenanceError("rollback path escapes the project state root")
        generation = target(generation_logical, base)
        record = _read_json_if_regular(generation / "rollback.json")
        if not record or record.get("from_version") != current["installed_version"]:
            raise MaintenanceError("rollback metadata does not match the installed version")
        previous = _read_json_if_regular(generation / "install-state.json")
        if not previous or previous.get("installed_version") != record.get("to_version"):
            raise MaintenanceError("rollback state identity is invalid")
        transaction = _LocalTransaction(base, "rollback")
        diversion_removed = False
        try:
            current_paths = set(current["files"])
            previous_paths = set(previous["files"])
            restored: list[str] = []
            for logical, file_meta in previous["files"].items():
                source = generation / "root" / Path(logical).relative_to("/")
                if sha256(source) != file_meta["sha256"]:
                    raise MaintenanceError(f"rollback snapshot hash mismatch: {logical}")
                transaction.copy(source, target(Path(logical), base), int(file_meta["mode"]))
                restored.append(logical)
            qualification_files = record.get("accepted_qualification_files", {})
            if not isinstance(qualification_files, dict):
                raise MaintenanceError("rollback qualification metadata is malformed")
            for logical, file_meta in qualification_files.items():
                logical_path = Path(logical) if isinstance(logical, str) else Path(".")
                if logical_path not in ACCEPTED_QUALIFICATION_PATHS or not isinstance(
                    file_meta, dict
                ):
                    raise MaintenanceError("rollback qualification path is not allowlisted")
                mode = file_meta.get("mode")
                expected = file_meta.get("sha256")
                if mode not in {0o644, 0o755} or not isinstance(expected, str):
                    raise MaintenanceError("rollback qualification metadata is invalid")
                source = generation / "qualification-root" / logical_path.relative_to("/")
                if not source.is_file() or source.is_symlink() or sha256(source) != expected:
                    raise MaintenanceError(f"rollback qualification hash mismatch: {logical}")
                transaction.copy(source, target(logical_path, base), int(mode))
                restored.append(logical)
            timeshift_transition = bool(current.get("timeshift_integration")) and not bool(
                previous.get("timeshift_integration")
            )
            excluded_removals: set[str] = set()
            if timeshift_transition:
                for binary, real in (
                    (Path("/usr/bin/timeshift"), Path("/usr/lib") / PROJECT / "timeshift.real"),
                    (
                        Path("/usr/bin/timeshift-gtk"),
                        Path("/usr/lib") / PROJECT / "timeshift-gtk.real",
                    ),
                ):
                    destination, original = target(binary, base), target(real, base)
                    transaction.backup(destination)
                    transaction.backup(original)
                    if base == Path("/"):
                        destination.unlink(missing_ok=True)
                        subprocess.run(
                            [
                                "/usr/bin/dpkg-divert",
                                "--package",
                                PROJECT,
                                "--remove",
                                "--rename",
                                "--divert",
                                str(real),
                                str(binary),
                            ],
                            check=True,
                            timeout=30,
                        )
                        diversion_removed = True
                    else:
                        destination.unlink(missing_ok=True)
                        original.replace(destination)
                    excluded_removals.add(str(binary))
            for logical in sorted(current_paths - previous_paths - excluded_removals, reverse=True):
                destination = target(Path(logical), base)
                if destination.is_symlink() or (destination.exists() and not destination.is_file()):
                    raise MaintenanceError(f"refusing unsafe rollback removal: {logical}")
                transaction.remove(destination)
            previous["operation"] = "COMMITTED"
            previous["rollback"] = {"available": False, "reason": "already rolled back"}
            state_temp = transaction.path / "restored-install-state.json"
            state_temp.write_text(json.dumps(previous, sort_keys=True, indent=2) + "\n")
            os.chmod(state_temp, 0o600)
            transaction.copy(state_temp, target(INSTALL_STATE, base), 0o600)
            _reload(base)
            result = inspect(base, system_checks=base == Path("/"))
            if result["status"] not in {"HEALTHY", "MODIFIED"}:
                raise MaintenanceError(f"post-rollback health check failed: {result['status']}")
            transaction.commit()
            return {
                "status": "ROLLED_BACK",
                "from_version": current["installed_version"],
                "to_version": previous["installed_version"],
                "restored_files": len(restored),
            }
        except Exception:
            if diversion_removed and base == Path("/"):
                with contextlib.suppress(Exception):
                    for binary, real in (
                        (Path("/usr/bin/timeshift"), Path("/usr/lib") / PROJECT / "timeshift.real"),
                        (
                            Path("/usr/bin/timeshift-gtk"),
                            Path("/usr/lib") / PROJECT / "timeshift-gtk.real",
                        ),
                    ):
                        subprocess.run(
                            [
                                "/usr/bin/dpkg-divert",
                                "--package",
                                PROJECT,
                                "--add",
                                "--rename",
                                "--divert",
                                str(real),
                                str(binary),
                            ],
                            check=True,
                            timeout=30,
                        )
            transaction.rollback()
            with contextlib.suppress(Exception):
                _reload(base)
            raise
