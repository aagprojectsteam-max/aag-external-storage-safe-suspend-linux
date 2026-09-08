from __future__ import annotations

import argparse
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("public_installer", ROOT / "scripts/install.py")
assert SPEC and SPEC.loader
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "usr/bin").mkdir(parents=True)
        shutil.copy2("/bin/true", self.root / "usr/bin/timeshift")
        shutil.copy2("/bin/true", self.root / "usr/bin/timeshift-gtk")
        self.original = INSTALLER.sha256(self.root / "usr/bin/timeshift")
        self.args = argparse.Namespace(
            config=str(ROOT / "tests/fixtures/config.json"),
            device=None,
            protect_mount=[],
            timeshift=True,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_install_verify_uninstall_round_trip(self) -> None:
        installer = INSTALLER.Installer(self.root, True)
        manifest = installer.install(self.args)
        self.assertEqual(manifest["version"], "1.0.0")
        self.assertTrue(
            (self.root / "etc/udev/rules.d/99-aag-external-storage-safe-suspend.rules").is_file()
        )
        self.assertNotEqual(INSTALLER.sha256(self.root / "usr/bin/timeshift"), self.original)
        result = INSTALLER.Installer(self.root, True).uninstall()
        self.assertEqual(result["status"], "uninstalled")
        self.assertEqual(INSTALLER.sha256(self.root / "usr/bin/timeshift"), self.original)
        self.assertFalse((self.root / "usr/local/libexec/aag-safe-suspend").exists())
        self.assertFalse(
            (self.root / "etc/udev/rules.d/99-aag-external-storage-safe-suspend.rules").exists()
        )
        self.assertFalse((self.root / "etc/aag-external-storage-safe-suspend").exists())

    def test_idempotent_reinstall(self) -> None:
        INSTALLER.Installer(self.root, True).install(self.args)
        second = INSTALLER.Installer(self.root, True).install(self.args)
        self.assertEqual(second["version"], "1.0.0")
        INSTALLER.Installer(self.root, True).uninstall()
        self.assertEqual(INSTALLER.sha256(self.root / "usr/bin/timeshift"), self.original)

    def test_abort_restores_original_state(self) -> None:
        installer = INSTALLER.Installer(self.root, True)
        with (
            patch.object(
                installer, "validate_installed", side_effect=INSTALLER.InstallError("injected")
            ),
            self.assertRaises(INSTALLER.InstallError),
        ):
            installer.install(self.args)
        self.assertEqual(INSTALLER.sha256(self.root / "usr/bin/timeshift"), self.original)
        self.assertFalse((self.root / "usr/local/libexec/aag-safe-suspend").exists())
        self.assertFalse(
            (self.root / "etc/udev/rules.d/99-aag-external-storage-safe-suspend.rules").exists()
        )
        self.assertTrue((installer.transaction / "INSTALL-ABORTED.txt").is_file())

    def test_uninstall_abort_restores_diversion_and_installation(self) -> None:
        INSTALLER.Installer(self.root, True).install(self.args)
        wrapper_hash = INSTALLER.sha256(self.root / "usr/bin/timeshift")
        installer = INSTALLER.Installer(self.root, True)
        with (
            patch.object(
                installer, "system_reload", side_effect=INSTALLER.InstallError("injected")
            ),
            self.assertRaises(INSTALLER.InstallError),
        ):
            installer.uninstall()
        self.assertEqual(INSTALLER.sha256(self.root / "usr/bin/timeshift"), wrapper_hash)
        self.assertEqual(
            INSTALLER.sha256(
                self.root / "usr/lib/aag-external-storage-safe-suspend/timeshift.real"
            ),
            self.original,
        )
        self.assertTrue((self.root / "usr/local/libexec/aag-safe-suspend").is_file())
        self.assertTrue((installer.transaction / "UNINSTALL-ABORTED.txt").is_file())
        INSTALLER.Installer(self.root, True).uninstall()
        self.assertEqual(INSTALLER.sha256(self.root / "usr/bin/timeshift"), self.original)

    def test_installer_has_no_power_state_command(self) -> None:
        source = (ROOT / "scripts/install.py").read_text()
        self.assertNotIn('"suspend"', source)
        self.assertNotIn('"reboot"', source)
        self.assertNotIn('"poweroff"', source)

    def test_active_runtime_fence_refuses_before_mutation(self) -> None:
        runtime = self.root / "run/aag-external-storage-safe-suspend"
        runtime.mkdir(parents=True)
        (runtime / "automount-suppressed.json").write_text("{}\n")
        with self.assertRaises(INSTALLER.InstallError):
            INSTALLER.Installer(self.root, True).install(self.args)
        self.assertFalse((self.root / "usr/local/libexec/aag-safe-suspend").exists())

    def test_test_mode_refuses_the_real_root(self) -> None:
        with self.assertRaises(INSTALLER.InstallError):
            INSTALLER.Installer(Path("/"), True).install(self.args)

    def test_symlinked_target_parent_refuses_before_escape(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "usr/local").mkdir(parents=True)
        (self.root / "usr/local/libexec").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(INSTALLER.InstallError):
            INSTALLER.Installer(self.root, True).install(self.args)
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
