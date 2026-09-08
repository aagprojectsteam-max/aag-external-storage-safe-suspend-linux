#!/usr/bin/python3
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "src"))

from aag_safe_suspend import __version__, identity, job_guard
from aag_safe_suspend import config as configuration

PROJECT = "aag-external-storage-safe-suspend"
STATE_ROOT = Path("/var/lib") / PROJECT
CONFIG_PATH = Path("/etc") / PROJECT / "config.json"
RULE_PATH = Path("/etc/udev/rules.d/99-aag-external-storage-safe-suspend.rules")
MARKER_PATH = "/run/aag-external-storage-safe-suspend/automount-suppressed.json"
TIMESHIFT_REAL = Path("/usr/lib") / PROJECT / "timeshift.real"
TIMESHIFT_GTK_REAL = Path("/usr/lib") / PROJECT / "timeshift-gtk.real"

FILES = {
    Path("/usr/local/libexec/aag-safe-suspend"): (SOURCE / "src/aag-safe-suspend", 0o755),
    Path("/usr/local/libexec/aag-systemd-job-guard"): (SOURCE / "src/aag-systemd-job-guard", 0o755),
    Path("/etc/systemd/system/aag-external-storage-safe-suspend.service"): (
        SOURCE / "systemd/aag-external-storage-safe-suspend.service",
        0o644,
    ),
    Path("/etc/systemd/system/aag-external-storage-safe-suspend-resume.service"): (
        SOURCE / "systemd/aag-external-storage-safe-suspend-resume.service",
        0o644,
    ),
    Path("/etc/systemd/system/aag-external-storage-safe-suspend-failure.service"): (
        SOURCE / "systemd/aag-external-storage-safe-suspend-failure.service",
        0o644,
    ),
    Path(
        "/etc/systemd/system/systemd-suspend.service.d/70-aag-external-storage-safe-suspend.conf"
    ): (
        SOURCE / "systemd/systemd-suspend.service.d/70-aag-external-storage-safe-suspend.conf",
        0o644,
    ),
}
for module in sorted((SOURCE / "src/aag_safe_suspend").glob("*.py")):
    FILES[Path("/usr/lib") / PROJECT / "aag_safe_suspend" / module.name] = (module, 0o644)


class InstallError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def command(
    argv: list[str], *, check: bool = True, timeout: float = 30
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "LC_ALL": "C", "PYTHONPATH": str(SOURCE / "src")},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallError(f"command failed to execute: {argv[0]}: {exc}") from exc
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise InstallError(f"command failed ({result.returncode}): {' '.join(argv)}: {detail}")
    return result


class Installer:
    def __init__(self, root: Path, test_mode: bool) -> None:
        self.root = root
        self.test_mode = test_mode
        self.transaction: Path | None = None
        self.baseline: dict[str, dict[str, Any]] = {}
        self.mutated: list[Path] = []
        self.guard: subprocess.Popen[str] | None = None
        self.guard_dir: Path | None = None
        self.guard_stop: Path | None = None
        self.timeshift_diversion_added = False

    def target(self, path: Path) -> Path:
        if not path.is_absolute():
            raise InstallError(f"target is not absolute: {path}")
        return path if self.root == Path("/") else self.root / path.relative_to("/")

    def ensure_directory(self, directory: Path, final_mode: int = 0o755) -> None:
        base = Path("/") if self.root == Path("/") else self.root
        try:
            relative = directory.relative_to(base)
        except ValueError as exc:
            raise InstallError(f"directory escapes installation root: {directory}") from exc
        current = base
        for index, part in enumerate(relative.parts):
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                current.mkdir(mode=final_mode if index == len(relative.parts) - 1 else 0o755)
                continue
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise InstallError(f"unsafe directory component: {current}")

    def ensure_environment(self) -> None:
        if self.test_mode == (self.root == Path("/")):
            raise InstallError(
                "isolated test mode and a non-root fixture path must be used together"
            )
        if not self.test_mode and os.geteuid() != 0:
            raise InstallError("installation requires root")
        if self.root == Path("/"):
            os_release = Path("/etc/os-release").read_text()
            if "ID=ubuntu" not in os_release and "ID_LIKE=ubuntu" not in os_release:
                raise InstallError("v1.0.0 supports Ubuntu-family systemd desktops only")
            for executable in (
                "/usr/bin/python3",
                "/usr/bin/lsblk",
                "/usr/bin/findmnt",
                "/usr/bin/udevadm",
                "/usr/bin/systemctl",
                "/usr/bin/systemd-analyze",
                "/usr/bin/systemd-inhibit",
                "/usr/bin/umount",
            ):
                if not Path(executable).is_file():
                    raise InstallError(f"required executable is missing: {executable}")
            for unit in (
                "systemd-suspend.service",
                "aag-external-storage-safe-suspend.service",
                "aag-external-storage-safe-suspend-resume.service",
                "aag-external-storage-safe-suspend-failure.service",
            ):
                state = command(
                    ["/usr/bin/systemctl", "show", unit, "-p", "ActiveState", "--value"],
                    check=False,
                )
                if state.stdout.strip() not in {"", "inactive", "failed"}:
                    raise InstallError(f"power transaction unit is active: {unit}")
            job_guard.assert_clear("preflight")
        runtime = self.target(Path("/run") / PROJECT)
        if (runtime / "automount-suppressed.json").exists():
            raise InstallError("a suspend transaction fence is active")
        state_file = runtime / "state.json"
        if state_file.exists():
            try:
                runtime_state = json.loads(state_file.read_text()).get("state")
            except (OSError, TypeError, ValueError) as exc:
                raise InstallError(f"runtime transaction state is unreadable: {exc}") from exc
            if runtime_state != "IDLE":
                raise InstallError(f"runtime transaction is not IDLE: {runtime_state}")

    def new_transaction(self, operation: str) -> Path:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        state_directory = self.target(STATE_ROOT)
        self.ensure_directory(state_directory, 0o700)
        state_info = state_directory.stat()
        expected_uid = os.getuid() if self.test_mode else 0
        if state_info.st_uid != expected_uid or stat.S_IMODE(state_info.st_mode) != 0o700:
            raise InstallError("project state directory owner or mode is unsafe")
        transaction_parent = state_directory / "transactions"
        self.ensure_directory(transaction_parent, 0o700)
        transaction_info = transaction_parent.stat()
        if (
            transaction_info.st_uid != expected_uid
            or stat.S_IMODE(transaction_info.st_mode) != 0o700
        ):
            raise InstallError("transaction directory owner or mode is unsafe")
        transaction = transaction_parent / f"{stamp}-{operation}"
        transaction.mkdir(mode=0o700, exist_ok=False)
        self.transaction = transaction
        return transaction

    def start_guard(self) -> None:
        if self.test_mode:
            return
        assert self.transaction is not None
        self.guard_dir = self.transaction / "systemd-job-guard"
        self.guard_stop = self.transaction / "STOP"
        self.guard = subprocess.Popen(
            [
                sys.executable,
                str(SOURCE / "src/aag-systemd-job-guard"),
                "watch",
                "--output",
                str(self.guard_dir),
                "--parent",
                str(os.getpid()),
                "--stop-file",
                str(self.guard_stop),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": str(SOURCE / "src")},
        )
        deadline = time.monotonic() + 5
        while not (self.guard_dir / "READY.json").is_file():
            if self.guard.poll() is not None or time.monotonic() >= deadline:
                raise InstallError("continuous systemd job guard failed to start")
            time.sleep(0.02)

    def guard_clear(self, stage: str) -> None:
        if self.test_mode:
            return
        assert self.guard and self.guard_dir
        if self.guard.poll() is not None:
            raise InstallError(f"systemd job guard exited at {stage}")
        violation = self.guard_dir / "VIOLATION.json"
        if violation.exists():
            raise InstallError(f"systemd job guard observed a conflict at {stage}")
        job_guard.assert_clear(stage)
        with (self.guard_dir / "CHECKPOINTS.txt").open("a") as stream:
            stream.write(f"{stage}\t{dt.datetime.now(dt.timezone.utc).isoformat()}\n")

    def stop_guard(self) -> None:
        if not self.guard:
            return
        assert self.guard_stop and self.guard_dir
        self.guard_stop.write_text("stop\n")
        try:
            self.guard.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.guard.terminate()
            self.guard.wait(timeout=5)
        if self.guard.returncode != 0 or (self.guard_dir / "VIOLATION.json").exists():
            raise InstallError("systemd job guard did not close cleanly")

    def load_active(self) -> dict[str, Any] | None:
        path = self.target(STATE_ROOT) / "active.json"
        if not path.exists() and not path.is_symlink():
            return None
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise InstallError("active installation manifest is not a regular file")
        expected_uid = os.getuid() if self.test_mode else 0
        if info.st_uid != expected_uid or stat.S_IMODE(info.st_mode) != 0o600:
            raise InstallError("active installation manifest owner or mode is unsafe")
        try:
            value = json.loads(path.read_text())
        except (OSError, TypeError, ValueError) as exc:
            raise InstallError(f"active installation manifest is unreadable: {exc}") from exc
        if value.get("project") != PROJECT:
            raise InstallError("active installation manifest is not project-owned")
        return value

    def verify_existing(self, active: dict[str, Any] | None, paths: list[Path]) -> None:
        known = (active or {}).get("files", {})
        for logical in paths:
            target = self.target(logical)
            if not target.exists() and not target.is_symlink():
                continue
            expected = known.get(str(logical))
            if not expected:
                raise InstallError(f"refusing to overwrite non-project path: {logical}")
            info = target.lstat()
            expected_uid = os.getuid() if self.test_mode else 0
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_uid != expected_uid
                or stat.S_IMODE(info.st_mode) != expected["mode"]
                or sha256(target) != expected["sha256"]
            ):
                raise InstallError(
                    f"installed project file changed outside the installer: {logical}"
                )

    def backup(self, logical: Path) -> None:
        assert self.transaction is not None
        target = self.target(logical)
        key = str(logical)
        if key in self.baseline:
            return
        if target.exists() or target.is_symlink():
            if not target.is_file() or target.is_symlink():
                raise InstallError(f"unsafe existing target: {logical}")
            destination = self.transaction / "baseline" / logical.relative_to("/")
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copy2(target, destination)
            self.baseline[key] = {
                "existed": True,
                "sha256": sha256(target),
                "mode": target.stat().st_mode & 0o777,
            }
        else:
            self.baseline[key] = {"existed": False}

    def install_bytes(self, logical: Path, content: bytes, mode: int) -> None:
        self.backup(logical)
        target = self.target(logical)
        self.ensure_directory(target.parent)
        fd, temporary = tempfile.mkstemp(prefix=".aag-install-", dir=target.parent)
        try:
            remaining = memoryview(content)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise InstallError(f"short write while installing {logical}")
                remaining = remaining[written:]
            os.fsync(fd)
            os.fchmod(fd, mode)
            if not self.test_mode:
                os.fchown(fd, 0, 0)
            os.close(fd)
            fd = -1
            os.replace(temporary, target)
            directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if fd >= 0:
                os.close(fd)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary)
        self.mutated.append(logical)

    def install_file(self, logical: Path, source: Path, mode: int) -> None:
        if not source.is_file() or source.is_symlink():
            raise InstallError(f"unsafe payload: {source.relative_to(SOURCE)}")
        self.install_bytes(logical, source.read_bytes(), mode)

    def rollback_mutation(self) -> None:
        if not self.transaction:
            return
        for logical in reversed(self.mutated):
            target = self.target(logical)
            baseline = self.baseline.get(str(logical), {"existed": False})
            if baseline.get("existed"):
                source = self.transaction / "baseline" / logical.relative_to("/")
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                shutil.copy2(source, target)
            else:
                with contextlib.suppress(FileNotFoundError):
                    target.unlink()
        if self.timeshift_diversion_added:
            self.remove_timeshift_diversion(best_effort=True)
        if not self.test_mode:
            command(["/usr/bin/systemctl", "daemon-reload"], check=False)
            command(["/usr/bin/udevadm", "control", "--reload"], check=False)

    def add_timeshift_diversion(self, active: dict[str, Any] | None) -> None:
        pairs = [
            (Path("/usr/bin/timeshift"), TIMESHIFT_REAL, SOURCE / "scripts/timeshift-wrapper"),
            (
                Path("/usr/bin/timeshift-gtk"),
                TIMESHIFT_GTK_REAL,
                SOURCE / "scripts/timeshift-gtk-wrapper",
            ),
        ]
        if self.test_mode:
            for binary, real, wrapper in pairs:
                target_binary, target_real = self.target(binary), self.target(real)
                if not target_real.exists():
                    if not target_binary.is_file():
                        raise InstallError(f"test Timeshift fixture missing: {binary}")
                    target_real.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                    target_binary.replace(target_real)
                    self.timeshift_diversion_added = True
                self.install_file(binary, wrapper, 0o755)
            return
        for binary, real, wrapper in pairs:
            owner = command(
                ["/usr/bin/dpkg-divert", "--listpackage", str(binary)], check=False
            ).stdout.strip()
            if owner and owner != PROJECT:
                raise InstallError(f"Timeshift path already diverted by {owner}: {binary}")
            if not owner:
                if not binary.is_file() or binary.is_symlink():
                    raise InstallError(f"Timeshift binary is missing or unsafe: {binary}")
                command(
                    [
                        "/usr/bin/dpkg-divert",
                        "--package",
                        PROJECT,
                        "--add",
                        "--rename",
                        "--divert",
                        str(real),
                        str(binary),
                    ]
                )
                self.timeshift_diversion_added = True
            if not real.is_file() or real.is_symlink():
                raise InstallError(f"Timeshift diverted binary is missing or unsafe: {real}")
            self.install_file(binary, wrapper, 0o755)

    def remove_timeshift_diversion(self, best_effort: bool = False) -> None:
        pairs = [
            (Path("/usr/bin/timeshift"), TIMESHIFT_REAL),
            (Path("/usr/bin/timeshift-gtk"), TIMESHIFT_GTK_REAL),
        ]
        for binary, real in pairs:
            target_binary, target_real = self.target(binary), self.target(real)
            if self.test_mode:
                with contextlib.suppress(FileNotFoundError):
                    target_binary.unlink()
                if target_real.exists():
                    target_binary.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                    target_real.replace(target_binary)
                continue
            owner = command(
                ["/usr/bin/dpkg-divert", "--listpackage", str(binary)], check=False
            ).stdout.strip()
            if owner == PROJECT:
                with contextlib.suppress(FileNotFoundError):
                    binary.unlink()
                result = command(
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
                    check=False,
                )
                if result.returncode and not best_effort:
                    raise InstallError(f"failed to restore Timeshift diversion: {binary}")

    def system_reload(self) -> None:
        if self.test_mode:
            return
        command(["/usr/bin/systemctl", "daemon-reload"])
        self.guard_clear("post-systemd-daemon-reload")
        command(["/usr/bin/udevadm", "control", "--reload"])
        self.guard_clear("post-udev-reload")

    def validate_installed(self, config: dict[str, Any], manifest_files: dict[str, Any]) -> None:
        for logical, metadata in manifest_files.items():
            path = self.target(Path(logical))
            info = path.lstat()
            expected_uid = os.getuid() if self.test_mode else 0
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_uid != expected_uid
                or stat.S_IMODE(info.st_mode) != metadata["mode"]
                or sha256(path) != metadata["sha256"]
            ):
                raise InstallError(f"post-install hash mismatch: {logical}")
        if self.test_mode:
            package = self.target(Path("/usr/lib") / PROJECT / "aag_safe_suspend")
            for module in package.glob("*.py"):
                compile(module.read_text(), str(module), "exec")
            return
        command(["/usr/bin/udevadm", "verify", str(RULE_PATH)])
        for unit in (
            "aag-external-storage-safe-suspend.service",
            "aag-external-storage-safe-suspend-resume.service",
            "aag-external-storage-safe-suspend-failure.service",
            "systemd-suspend.service",
        ):
            state = command(["/usr/bin/systemctl", "show", unit, "-p", "LoadState", "--value"])
            if state.stdout.strip() != "loaded":
                raise InstallError(f"installed unit did not load: {unit}")
        command(
            [
                "/usr/bin/systemd-analyze",
                "verify",
                "aag-external-storage-safe-suspend.service",
                "aag-external-storage-safe-suspend-resume.service",
                "aag-external-storage-safe-suspend-failure.service",
                "systemd-suspend.service",
            ]
        )
        command(["/usr/local/libexec/aag-safe-suspend", "validate"])
        self.guard_clear("post-installed-validation")

    def install(self, args: argparse.Namespace) -> dict[str, Any]:
        self.ensure_environment()
        active = self.load_active()
        config_path = self.target(CONFIG_PATH)
        if args.config:
            selected_config = configuration.load(Path(args.config))
        elif args.device:
            if self.test_mode:
                raise InstallError(
                    "--device discovery is unavailable in isolated test mode; use --config"
                )
            selected_config = configuration.validate(
                identity.discover(args.device, list(dict.fromkeys(["/", *args.protect_mount])))
            )
        elif config_path.is_file() and active:
            selected_config = configuration.load(config_path)
        else:
            if not sys.stdin.isatty():
                raise InstallError(
                    "specify --device or --config when standard input is not interactive"
                )
            candidates = [
                row
                for row in identity.lsblk_values()
                if row.get("type") == "disk" and row.get("tran") == "usb" and row.get("path")
            ]
            if not candidates:
                raise InstallError("no USB-attached whole-disk candidate was found")
            for index, row in enumerate(candidates, 1):
                print(f"[{index}] {row.get('path')}  {row.get('model', '')}  {row.get('size', '')}")
            choice = int(input("Select the external backup disk number: "))
            if choice < 1 or choice > len(candidates):
                raise InstallError("invalid device selection")
            selected_config = configuration.validate(
                identity.discover(
                    candidates[choice - 1]["path"], list(dict.fromkeys(["/", *args.protect_mount]))
                )
            )

        rule = identity.udev_rule(selected_config, MARKER_PATH).encode()
        paths = list(FILES) + [CONFIG_PATH, RULE_PATH]
        timeshift_requested = bool(
            args.timeshift or (active and active.get("timeshift_integration"))
        )
        self.verify_existing(active, paths)
        if active and active.get("timeshift_integration"):
            self.verify_existing(
                active, [Path("/usr/bin/timeshift"), Path("/usr/bin/timeshift-gtk")]
            )
        transaction = self.new_transaction("install")
        self.start_guard()
        try:
            self.guard_clear("pre-mutation")
            for logical, (source, mode) in FILES.items():
                self.install_file(logical, source, mode)
            self.install_bytes(CONFIG_PATH, configuration.dump(selected_config).encode(), 0o600)
            self.install_bytes(RULE_PATH, rule, 0o644)
            timeshift = timeshift_requested
            if timeshift:
                self.add_timeshift_diversion(active)
                paths.extend([Path("/usr/bin/timeshift"), Path("/usr/bin/timeshift-gtk")])
            self.guard_clear("post-file-install")
            self.system_reload()
            manifest_files: dict[str, Any] = {}
            for logical in paths:
                target = self.target(logical)
                if target.is_file() and not target.is_symlink():
                    manifest_files[str(logical)] = {
                        "sha256": sha256(target),
                        "mode": target.stat().st_mode & 0o777,
                    }
            manifest = {
                "project": PROJECT,
                "version": __version__,
                "installed": dt.datetime.now(dt.timezone.utc).isoformat(),
                "transaction": str(transaction),
                "files": manifest_files,
                "timeshift_integration": timeshift,
                "baseline": self.baseline,
            }
            self.validate_installed(selected_config, manifest_files)
            self.install_bytes(
                STATE_ROOT / "active.json",
                (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode(),
                0o600,
            )
            self.guard_clear("post-publication-marker")
            self.stop_guard()
            (transaction / "INSTALL-COMPLETE.json").write_text(
                json.dumps({"version": __version__, "files": len(manifest_files)}, sort_keys=True)
                + "\n"
            )
            return manifest
        except Exception:
            self.rollback_mutation()
            with contextlib.suppress(Exception):
                self.stop_guard()
            (transaction / "INSTALL-ABORTED.txt").write_text("rollback attempted\n")
            raise

    def remove_empty_project_directories(self) -> None:
        for logical in (
            Path("/usr/lib") / PROJECT / "aag_safe_suspend",
            Path("/usr/lib") / PROJECT,
            Path("/etc") / PROJECT,
            Path("/etc/systemd/system/systemd-suspend.service.d"),
        ):
            with contextlib.suppress(OSError):
                self.target(logical).rmdir()

    def uninstall(self) -> dict[str, Any]:
        self.ensure_environment()
        active = self.load_active()
        if not active:
            return {"status": "not-installed", "power_state_actions": "none"}
        paths = [Path(value) for value in active["files"]]
        self.verify_existing(active, paths)
        runtime = self.target(Path("/run") / PROJECT)
        if (runtime / "automount-suppressed.json").exists():
            raise InstallError("refusing uninstall while a suspend fence is active")
        state_file = runtime / "state.json"
        if state_file.exists() and json.loads(state_file.read_text()).get("state") != "IDLE":
            raise InstallError("refusing uninstall while project state is not IDLE")
        transaction = self.new_transaction("uninstall")
        self.start_guard()
        timeshift_removed = False
        try:
            self.guard_clear("pre-uninstall")
            active_logical = STATE_ROOT / "active.json"
            for logical in [*paths, active_logical]:
                self.backup(logical)
            if active.get("timeshift_integration"):
                self.remove_timeshift_diversion()
                timeshift_removed = True
            for logical in sorted(paths, key=lambda path: len(path.parts), reverse=True):
                if active.get("timeshift_integration") and logical in {
                    Path("/usr/bin/timeshift"),
                    Path("/usr/bin/timeshift-gtk"),
                }:
                    continue
                target = self.target(logical)
                with contextlib.suppress(FileNotFoundError):
                    target.unlink()
                self.mutated.append(logical)
            self.system_reload()
            active_path = self.target(STATE_ROOT) / "active.json"
            with contextlib.suppress(FileNotFoundError):
                active_path.unlink()
            self.mutated.append(active_logical)
            self.guard_clear("post-uninstall")
            self.stop_guard()
            self.remove_empty_project_directories()
            (transaction / "UNINSTALL-COMPLETE.json").write_text(
                json.dumps(
                    {
                        "removed_files": len(paths),
                        "config_preserved": False,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            return {"status": "uninstalled", "power_state_actions": "none"}
        except Exception:
            if timeshift_removed:
                with contextlib.suppress(Exception):
                    for binary, real in (
                        (Path("/usr/bin/timeshift"), TIMESHIFT_REAL),
                        (Path("/usr/bin/timeshift-gtk"), TIMESHIFT_GTK_REAL),
                    ):
                        if self.test_mode:
                            target_binary, target_real = self.target(binary), self.target(real)
                            if target_binary.exists() and not target_real.exists():
                                target_real.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                                target_binary.replace(target_real)
                        else:
                            command(
                                [
                                    "/usr/bin/dpkg-divert",
                                    "--package",
                                    PROJECT,
                                    "--add",
                                    "--rename",
                                    "--divert",
                                    str(real),
                                    str(binary),
                                ]
                            )
                        self.mutated.append(binary)
            with contextlib.suppress(Exception):
                self.rollback_mutation()
            with contextlib.suppress(Exception):
                self.stop_guard()
            (transaction / "UNINSTALL-ABORTED.txt").write_text("rollback attempted\n")
            raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="install.py")
    result.add_argument("--root", type=Path, default=Path("/"), help=argparse.SUPPRESS)
    result.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)
    commands = result.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install")
    install.add_argument(
        "--device", help="current path or /dev/disk/by-id link used only for discovery"
    )
    install.add_argument("--config", help="pre-reviewed configuration JSON")
    install.add_argument("--protect-mount", action="append", default=[])
    install.add_argument("--timeshift", action="store_true")
    commands.add_parser("uninstall")
    commands.add_parser("verify-source")
    return result


def verify_source() -> dict[str, Any]:
    for source, _mode in FILES.values():
        if not source.is_file() or source.is_symlink():
            raise InstallError(f"missing or unsafe source payload: {source}")
    for module in (SOURCE / "src/aag_safe_suspend").glob("*.py"):
        compile(module.read_text(), str(module), "exec")
    for script in (SOURCE / "scripts/timeshift-wrapper", SOURCE / "scripts/timeshift-gtk-wrapper"):
        command(["/bin/sh", "-n", str(script)])
    return {"source": "valid", "version": __version__, "power_state_actions": "none"}


def main() -> int:
    os.umask(0o077)
    args = parser().parse_args()
    if args.command == "verify-source":
        print(json.dumps(verify_source(), sort_keys=True, indent=2))
        return 0
    installer = Installer(args.root.resolve(), bool(args.test_mode))
    try:
        value = installer.install(args) if args.command == "install" else installer.uninstall()
    except Exception as exc:
        print(f"INSTALLER_REFUSED: {exc}", file=sys.stderr)
        if installer.transaction:
            print(f"TRANSACTION={installer.transaction}", file=sys.stderr)
            print("ROLLBACK_ATTEMPTED=YES", file=sys.stderr)
        print("POWER_STATE_ACTIONS=NONE", file=sys.stderr)
        return 1
    print(json.dumps(value, sort_keys=True, indent=2))
    print("POWER_STATE_ACTIONS=NONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
