from __future__ import annotations

import argparse
import errno
import fcntl
import importlib.util
import json
import os
import shutil
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aag_safe_suspend import config, identity, maintenance

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("upgrade_installer", ROOT / "scripts/install.py")
assert SPEC and SPEC.loader
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


class UpgradeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "usr/bin").mkdir(parents=True)
        shutil.copy2("/bin/true", self.root / "usr/bin/timeshift")
        shutil.copy2("/bin/true", self.root / "usr/bin/timeshift-gtk")
        self.args = argparse.Namespace(
            config=None, device=None, protect_mount=[], timeshift=False, repair=False
        )
        self.fresh_args = argparse.Namespace(
            config=str(ROOT / "tests/fixtures/config.json"),
            device=None,
            protect_mount=[],
            timeshift=True,
            repair=False,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def logical(self, value: str | Path) -> Path:
        return self.root / Path(value).relative_to("/")

    def legacy_source(self, logical: str) -> Path:
        target = Path(logical)
        if logical == "/usr/local/libexec/aag-safe-suspend":
            return ROOT / "tests/fixtures/v1.0.0/aag-safe-suspend"
        if logical == "/usr/local/libexec/aag-systemd-job-guard":
            return ROOT / "tests/fixtures/v1.0.0/aag-systemd-job-guard"
        if logical.endswith("/__init__.py"):
            return ROOT / "tests/fixtures/v1.0.0/__init__.py"
        if logical.endswith("/cli.py"):
            return ROOT / "tests/fixtures/v1.0.0/cli.py"
        if logical.endswith("/job_guard.py"):
            return ROOT / "tests/fixtures/v1.0.0/job_guard.py"
        for destination, (source, _mode) in INSTALLER.FILES.items():
            if destination == target:
                return source
        wrappers = {
            "/usr/bin/timeshift": ROOT / "scripts/timeshift-wrapper",
            "/usr/bin/timeshift-gtk": ROOT / "scripts/timeshift-gtk-wrapper",
        }
        return wrappers[logical]

    def make_legacy(self, *, timeshift: bool = True) -> dict[str, object]:
        files: dict[str, dict[str, object]] = {}
        static = dict(INSTALLER.LEGACY_V1_STATIC_HASHES)
        if timeshift:
            for binary, real in (
                ("/usr/bin/timeshift", "/usr/lib/aag-external-storage-safe-suspend/timeshift.real"),
                (
                    "/usr/bin/timeshift-gtk",
                    "/usr/lib/aag-external-storage-safe-suspend/timeshift-gtk.real",
                ),
            ):
                real_path = self.logical(real)
                real_path.parent.mkdir(parents=True, exist_ok=True)
                self.logical(binary).replace(real_path)
            static.update(INSTALLER.LEGACY_V1_TIMESHIFT_HASHES)
        for logical, expected in static.items():
            destination = self.logical(logical)
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = self.legacy_source(logical)
            shutil.copy2(source, destination)
            mode = (
                0o755
                if logical.startswith("/usr/local/libexec/") or logical.startswith("/usr/bin/")
                else 0o644
            )
            destination.chmod(mode)
            self.assertEqual(INSTALLER.sha256(destination), expected)
            files[logical] = {"sha256": expected, "mode": mode}
        configuration = config.load(ROOT / "tests/fixtures/config.json")
        config_path = self.logical(INSTALLER.CONFIG_PATH)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(config.dump(configuration))
        config_path.chmod(0o600)
        rule_path = self.logical(INSTALLER.RULE_PATH)
        rule_path.parent.mkdir(parents=True, exist_ok=True)
        rule_path.write_text(identity.udev_rule(configuration, INSTALLER.MARKER_PATH))
        rule_path.chmod(0o644)
        for logical, path in (
            (str(INSTALLER.CONFIG_PATH), config_path),
            (str(INSTALLER.RULE_PATH), rule_path),
        ):
            files[logical] = {
                "sha256": INSTALLER.sha256(path),
                "mode": path.stat().st_mode & 0o777,
            }
        state_dir = self.logical(INSTALLER.STATE_ROOT)
        state_dir.mkdir(mode=0o700, parents=True)
        legacy = {
            "project": INSTALLER.PROJECT,
            "version": "1.0.0",
            "installed": "2026-09-08T00:00:00+00:00",
            "files": files,
            "timeshift_integration": timeshift,
        }
        active = self.logical(INSTALLER.LEGACY_STATE)
        active.write_text(json.dumps(legacy, sort_keys=True, indent=2) + "\n")
        active.chmod(0o600)
        return legacy

    def install_fresh(self) -> dict[str, object]:
        return INSTALLER.Installer(self.root, True).install(self.fresh_args)

    def environment(self):
        return patch.dict(
            os.environ,
            {"AAG_SAFE_SUSPEND_ROOT": str(self.root), "AAG_SAFE_SUSPEND_TEST_MODE": "1"},
        )

    def test_public_v1_bootstrap_upgrade_and_rollback(self) -> None:
        self.make_legacy()
        old_hash = INSTALLER.sha256(
            self.logical("/usr/lib/aag-external-storage-safe-suspend/aag_safe_suspend/cli.py")
        )
        result = INSTALLER.Installer(self.root, True).install(self.args)
        self.assertEqual(result["installed_version"], "1.1.0")
        self.assertIn("bootstrap-public-v1.0.0-to-installed-state-v1", result["migrations_applied"])
        self.assertFalse(self.logical(INSTALLER.LEGACY_STATE).exists())
        with self.environment():
            rolled_back = maintenance.rollback()
        self.assertEqual(rolled_back["to_version"], "1.0.0")
        self.assertEqual(
            INSTALLER.sha256(
                self.logical("/usr/lib/aag-external-storage-safe-suspend/aag_safe_suspend/cli.py")
            ),
            old_hash,
        )

    def test_upgrade_never_touches_user_backup_or_project_data(self) -> None:
        self.make_legacy()
        sentinels = [
            "/mnt/data/user-project.txt",
            "/timeshift/snapshots/snapshot.txt",
            "/media/backup/Macrium/image.mrimg",
            "/media/ventoy/ventoy.json",
            "/srv/vm/guest.qcow2",
            "/opt/gnss-t700/data.log",
        ]
        expected: dict[str, bytes] = {}
        for index, logical in enumerate(sentinels):
            path = self.logical(logical)
            path.parent.mkdir(parents=True, exist_ok=True)
            content = f"sentinel-{index}\n".encode()
            path.write_bytes(content)
            expected[logical] = content
        INSTALLER.Installer(self.root, True).install(self.args)
        for logical, content in expected.items():
            self.assertEqual(self.logical(logical).read_bytes(), content)

    def test_bootstrap_failure_automatically_restores_exact_legacy_state(self) -> None:
        self.make_legacy()
        before = self.logical(INSTALLER.LEGACY_STATE).read_bytes()
        installer = INSTALLER.Installer(self.root, True)
        with (
            patch.object(
                installer, "validate_installed", side_effect=INSTALLER.InstallError("injected")
            ),
            self.assertRaises(INSTALLER.InstallError),
        ):
            installer.install(self.args)
        self.assertEqual(self.logical(INSTALLER.LEGACY_STATE).read_bytes(), before)
        self.assertFalse(self.logical(INSTALLER.INSTALL_STATE).exists())
        self.assertTrue((installer.transaction / "INSTALL-ABORTED.txt").is_file())

    def test_migration_failure_occurs_before_file_mutation(self) -> None:
        self.make_legacy()
        before = INSTALLER.sha256(
            self.logical("/usr/lib/aag-external-storage-safe-suspend/aag_safe_suspend/cli.py")
        )
        installer = INSTALLER.Installer(self.root, True)
        with (
            patch.object(
                INSTALLER.migrations,
                "apply",
                side_effect=INSTALLER.migrations.MigrationError("injected"),
            ),
            self.assertRaises(INSTALLER.migrations.MigrationError),
        ):
            installer.install(self.args)
        self.assertEqual(
            INSTALLER.sha256(
                self.logical("/usr/lib/aag-external-storage-safe-suspend/aag_safe_suspend/cli.py")
            ),
            before,
        )

    def test_unknown_legacy_modification_is_refused_before_mutation(self) -> None:
        self.make_legacy()
        unit = self.logical("/etc/systemd/system/aag-external-storage-safe-suspend.service")
        unit.write_text(unit.read_text() + "# local edit\n")
        with self.assertRaisesRegex(INSTALLER.InstallError, "identity mismatch"):
            INSTALLER.Installer(self.root, True).install(self.args)
        self.assertTrue(self.logical(INSTALLER.LEGACY_STATE).exists())

    def test_supported_configuration_is_preserved_and_rule_is_reconciled(self) -> None:
        self.make_legacy()
        path = self.logical(INSTALLER.CONFIG_PATH)
        value = json.loads(path.read_text())
        value["thermal"]["warning_grace_seconds"] = 61
        custom = json.dumps(value, indent=4, sort_keys=False) + "\n"
        path.write_text(custom)
        result = INSTALLER.Installer(self.root, True).install(self.args)
        self.assertEqual(path.read_text(), custom)
        self.assertEqual(
            self.logical(INSTALLER.RULE_PATH).read_text(),
            identity.udev_rule(config.load(path), INSTALLER.MARKER_PATH),
        )
        self.assertEqual(
            result["files"][str(INSTALLER.CONFIG_PATH)]["classification"], "SUPPORTED_USER_CONFIG"
        )

    def test_same_version_repair_only_restores_project_managed_files(self) -> None:
        self.install_fresh()
        config_path = self.logical(INSTALLER.CONFIG_PATH)
        value = json.loads(config_path.read_text())
        value["thermal"]["warning_grace_seconds"] = 62
        custom = json.dumps(value, indent=4) + "\n"
        config_path.write_text(custom)
        unit = self.logical("/etc/systemd/system/aag-external-storage-safe-suspend.service")
        unit.write_text("corrupt\n")
        repair_args = argparse.Namespace(**{**vars(self.args), "repair": True})
        result = INSTALLER.Installer(self.root, True).install(repair_args)
        self.assertEqual(result["installed_version"], "1.1.0")
        self.assertEqual(config_path.read_text(), custom)
        self.assertNotEqual(unit.read_text(), "corrupt\n")

    def test_managed_status_matrix(self) -> None:
        with self.environment():
            self.assertEqual(maintenance.inspect(self.root)["status"], "NOT_INSTALLED")
        self.install_fresh()
        with self.environment():
            self.assertEqual(
                maintenance.inspect(self.root, system_checks=False)["status"], "HEALTHY"
            )
        unit = self.logical("/etc/systemd/system/aag-external-storage-safe-suspend.service")
        unit.write_text("modified\n")
        with self.environment():
            self.assertEqual(
                maintenance.inspect(self.root, system_checks=False)["status"], "MODIFIED"
            )
        unit.unlink()
        with self.environment():
            self.assertEqual(
                maintenance.inspect(self.root, system_checks=False)["status"], "PARTIAL"
            )
        state_path = self.logical(INSTALLER.INSTALL_STATE)
        state_path.write_text("not json\n")
        with self.environment():
            self.assertEqual(
                maintenance.inspect(self.root, system_checks=False)["status"], "BROKEN"
            )

    def test_pending_states_are_reported(self) -> None:
        state = self.install_fresh()
        state_path = self.logical(INSTALLER.INSTALL_STATE)
        for pending in ("UPGRADE_PENDING", "ROLLBACK_PENDING"):
            state["operation"] = pending
            state_path.write_text(json.dumps(state) + "\n")
            state_path.chmod(0o600)
            with self.environment():
                self.assertEqual(
                    maintenance.inspect(self.root, system_checks=False)["status"], pending
                )

    def test_unsupported_downgrade_is_explicit(self) -> None:
        state = self.install_fresh()
        state["installed_version"] = "2.0.0"
        state_path = self.logical(INSTALLER.INSTALL_STATE)
        state_path.write_text(json.dumps(state) + "\n")
        state_path.chmod(0o600)
        with self.assertRaisesRegex(INSTALLER.InstallError, "DOWNGRADE_REFUSED_WITH_REASON"):
            INSTALLER.Installer(self.root, True).install(self.args)

    def test_version_classifier_covers_patch_and_feature_upgrades(self) -> None:
        installer = INSTALLER.Installer(self.root, True)
        with patch.object(INSTALLER, "__version__", "1.0.1"):
            self.assertEqual(
                installer.classify_install({"installed_version": "1.0.0"}, False), "UPGRADE"
            )
        with patch.object(INSTALLER, "__version__", "1.1.0"):
            self.assertEqual(
                installer.classify_install({"installed_version": "1.0.1"}, False), "UPGRADE"
            )

    def test_managed_unknown_modification_blocks_upgrade(self) -> None:
        state = self.install_fresh()
        state["installed_version"] = "1.0.1"
        state_path = self.logical(INSTALLER.INSTALL_STATE)
        state_path.write_text(json.dumps(state) + "\n")
        state_path.chmod(0o600)
        self.logical("/etc/systemd/system/aag-external-storage-safe-suspend.service").write_text(
            "unknown local edit\n"
        )
        with self.assertRaisesRegex(INSTALLER.InstallError, "UNKNOWN_LOCAL_MODIFICATION"):
            INSTALLER.Installer(self.root, True).install(self.args)

    def test_active_sleep_and_backup_transactions_refuse(self) -> None:
        runtime = self.logical(Path("/run") / INSTALLER.PROJECT)
        runtime.mkdir(parents=True)
        (runtime / "state.json").write_text('{"state":"PREPARING_SLEEP"}\n')
        with self.assertRaisesRegex(INSTALLER.InstallError, "not IDLE"):
            INSTALLER.Installer(self.root, True).install(self.fresh_args)
        (runtime / "state.json").write_text('{"state":"IDLE"}\n')
        jobs = runtime / "jobs"
        jobs.mkdir()
        (jobs / "backup.json").write_text("{}\n")
        with self.assertRaisesRegex(INSTALLER.InstallError, "backup lifecycle"):
            INSTALLER.Installer(self.root, True).install(self.fresh_args)

    def test_stale_lock_file_does_not_block_but_live_lock_does(self) -> None:
        state = self.logical(INSTALLER.STATE_ROOT)
        state.mkdir(mode=0o700, parents=True)
        lock = state / "installer.lock"
        lock.write_text('{"pid":999999}\n')
        with INSTALLER.Installer(self.root, True).project_lock():
            pass
        stream = lock.open("a+")
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with (
                self.assertRaisesRegex(INSTALLER.InstallError, "another install"),
                INSTALLER.Installer(self.root, True).project_lock(),
            ):
                pass
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
            stream.close()

    def test_post_install_failure_and_rollback_failure_are_evident(self) -> None:
        self.make_legacy()
        installer = INSTALLER.Installer(self.root, True)
        with (
            patch.object(
                installer, "validate_installed", side_effect=INSTALLER.InstallError("verify")
            ),
            patch.object(
                installer, "rollback_mutation", side_effect=INSTALLER.InstallError("rollback")
            ),
            self.assertRaisesRegex(INSTALLER.InstallError, "automatic rollback also failed"),
        ):
            installer.install(self.args)
        self.assertTrue((installer.transaction / "ROLLBACK-FAILED.txt").is_file())

    def assert_write_failure_leaves_legacy_accepted(self, failure: OSError) -> None:
        self.make_legacy()
        before = self.logical(INSTALLER.LEGACY_STATE).read_bytes()
        installer = INSTALLER.Installer(self.root, True)
        with (
            patch.object(installer, "install_file", side_effect=failure),
            self.assertRaises(type(failure)),
        ):
            installer.install(self.args)
        self.assertEqual(self.logical(INSTALLER.LEGACY_STATE).read_bytes(), before)
        self.assertFalse(self.logical(INSTALLER.INSTALL_STATE).exists())

    def test_disk_full_failure_leaves_legacy_accepted(self) -> None:
        self.assert_write_failure_leaves_legacy_accepted(OSError(errno.ENOSPC, "disk full"))

    def test_read_only_failure_leaves_legacy_accepted(self) -> None:
        self.assert_write_failure_leaves_legacy_accepted(PermissionError("read only"))

    def test_sigterm_uses_exception_path_and_automatic_rollback(self) -> None:
        self.make_legacy()
        before = self.logical(INSTALLER.LEGACY_STATE).read_bytes()
        installer = INSTALLER.Installer(self.root, True)
        original_install = installer.install_file
        sent = False

        def terminate_after_first_file(logical: Path, source: Path, mode: int) -> None:
            nonlocal sent
            original_install(logical, source, mode)
            if not sent:
                sent = True
                os.kill(os.getpid(), signal.SIGTERM)

        prior_handler = signal.getsignal(signal.SIGTERM)

        def termination_error(_signum: int, _frame: object) -> None:
            raise INSTALLER.InstallError("termination signal")

        signal.signal(signal.SIGTERM, termination_error)
        try:
            with (
                patch.object(installer, "install_file", side_effect=terminate_after_first_file),
                self.assertRaisesRegex(INSTALLER.InstallError, "termination signal"),
            ):
                installer.install(self.args)
        finally:
            signal.signal(signal.SIGTERM, prior_handler)
        self.assertEqual(self.logical(INSTALLER.LEGACY_STATE).read_bytes(), before)
        self.assertTrue((installer.transaction / "INSTALL-ABORTED.txt").is_file())

    def test_path_traversal_and_rollback_poisoning_are_refused(self) -> None:
        with self.assertRaises(INSTALLER.InstallError):
            INSTALLER.Installer(self.root, True).target(Path("/etc/../outside"))
        self.make_legacy()
        INSTALLER.Installer(self.root, True).install(self.args)
        state_path = self.logical(INSTALLER.INSTALL_STATE)
        state = json.loads(state_path.read_text())
        state["rollback"]["path"] = "/etc"
        state_path.write_text(json.dumps(state) + "\n")
        state_path.chmod(0o600)
        with (
            self.environment(),
            self.assertRaisesRegex(maintenance.MaintenanceError, "escapes the project state root"),
        ):
            maintenance.rollback()

    def test_udev_graph_mismatch_is_broken(self) -> None:
        self.install_fresh()
        self.logical(INSTALLER.RULE_PATH).write_text("wrong\n")
        with self.environment():
            result = maintenance.inspect(self.root, system_checks=False)
        self.assertEqual(result["status"], "BROKEN")
        self.assertIn("UDEV_RULE_CONFIGURATION_MISMATCH", result["failures"])


if __name__ == "__main__":
    unittest.main()
