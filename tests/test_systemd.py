from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text()


class SystemdGraphTests(unittest.TestCase):
    def test_hibernate_wiring_is_absent_from_ordinary_suspend_contract(self) -> None:
        ordinary = "\n".join(
            text(path)
            for path in (
                "systemd/aag-external-storage-safe-suspend.service",
                "systemd/aag-external-storage-safe-suspend-resume.service",
                "systemd/aag-external-storage-safe-suspend-failure.service",
                "systemd/systemd-suspend.service.d/70-aag-external-storage-safe-suspend.conf",
            )
        )
        self.assertNotIn("systemd-hibernate.service", ordinary)
        self.assertNotIn("safe-hibernate", ordinary)

    def test_plain_hibernate_orders_wwan_before_terminal_storage_audit(self) -> None:
        dropin = text(
            "systemd/systemd-hibernate.service.d/70-aag-external-storage-safe-hibernate.conf"
        )
        resume = text("systemd/aag-external-storage-safe-hibernate-resume.service")
        recovery = text("systemd/aag-external-storage-safe-hibernate-wwan.service")
        self.assertIn("Requires=aag-external-storage-safe-suspend.service", dropin)
        self.assertIn("OnSuccess=aag-external-storage-safe-hibernate-resume.service", dropin)
        self.assertIn("Requires=aag-external-storage-safe-hibernate-wwan.service", resume)
        self.assertIn("After=systemd-hibernate.service", recovery)
        self.assertNotIn("systemd-suspend.service", dropin + resume + recovery)

    def test_native_suspend_requires_preparation(self) -> None:
        dropin = text("systemd/systemd-suspend.service.d/70-aag-external-storage-safe-suspend.conf")
        self.assertIn("Requires=aag-external-storage-safe-suspend.service", dropin)
        self.assertIn("After=aag-external-storage-safe-suspend.service", dropin)

    def test_resume_is_ordered_after_native_suspend(self) -> None:
        dropin = text("systemd/systemd-suspend.service.d/70-aag-external-storage-safe-suspend.conf")
        resume = text("systemd/aag-external-storage-safe-suspend-resume.service")
        self.assertIn("Wants=aag-external-storage-safe-suspend-resume.service", dropin)
        self.assertIn("Before=aag-external-storage-safe-suspend-resume.service", dropin)
        self.assertIn("After=systemd-suspend.service", resume)
        self.assertIn(
            "ConditionPathExists=/run/aag-external-storage-safe-suspend/automount-suppressed.json",
            resume,
        )

    def test_failures_route_to_fail_closed_recovery(self) -> None:
        for path in (
            "systemd/aag-external-storage-safe-suspend.service",
            "systemd/aag-external-storage-safe-suspend-resume.service",
            "systemd/systemd-suspend.service.d/70-aag-external-storage-safe-suspend.conf",
        ):
            self.assertIn("OnFailure=aag-external-storage-safe-suspend-failure.service", text(path))
        self.assertIn(
            "--what=sleep", text("systemd/aag-external-storage-safe-suspend-failure.service")
        )

    def test_installed_units_do_not_initiate_power_actions(self) -> None:
        for path in (ROOT / "systemd").rglob("*"):
            if not path.is_file():
                continue
            value = path.read_text()
            for line in value.splitlines():
                if line.startswith("Exec"):
                    self.assertNotIn("systemctl suspend", line)
                    self.assertNotIn("systemctl reboot", line)
                    self.assertNotIn("systemctl poweroff", line)

    def test_systemd_analyze_accepts_isolated_merged_graph(self) -> None:
        analyzer = shutil.which("systemd-analyze")
        if not analyzer:
            self.skipTest("systemd-analyze is unavailable")
        with tempfile.TemporaryDirectory(prefix="aag-systemd-fixture-") as temporary:
            root = Path(temporary)
            unit_dir = root / "etc/systemd/system"
            vendor_dir = root / "usr/lib/systemd/system"
            dropin_dir = unit_dir / "systemd-suspend.service.d"
            hibernate_dropin_dir = unit_dir / "systemd-hibernate.service.d"
            executable_dir = root / "usr/local/libexec"
            for directory in (
                unit_dir,
                vendor_dir,
                dropin_dir,
                hibernate_dropin_dir,
                executable_dir,
                root / "usr/bin",
            ):
                directory.mkdir(parents=True, exist_ok=True)
            for name in (
                "aag-external-storage-safe-suspend.service",
                "aag-external-storage-safe-suspend-resume.service",
                "aag-external-storage-safe-suspend-failure.service",
                "aag-external-storage-safe-hibernate-resume.service",
                "aag-external-storage-safe-hibernate-wwan.service",
                "aag-external-storage-safe-hibernate-abort.service",
                "aag-external-storage-safe-hibernate-boot-check.service",
            ):
                shutil.copy2(ROOT / "systemd" / name, unit_dir / name)
            shutil.copy2(
                ROOT
                / "systemd/systemd-suspend.service.d/70-aag-external-storage-safe-suspend.conf",
                dropin_dir / "70-aag-external-storage-safe-suspend.conf",
            )
            shutil.copy2(
                ROOT
                / "systemd/systemd-hibernate.service.d/70-aag-external-storage-safe-hibernate.conf",
                hibernate_dropin_dir / "70-aag-external-storage-safe-hibernate.conf",
            )
            (vendor_dir / "systemd-suspend.service").write_text(
                "[Unit]\nDescription=Synthetic suspend\n[Service]\nType=oneshot\n"
                "ExecStart=/usr/lib/systemd/systemd-sleep suspend\n"
            )
            (vendor_dir / "systemd-hibernate.service").write_text(
                "[Unit]\nDescription=Synthetic Hibernate\n[Service]\nType=oneshot\n"
                "ExecStart=/usr/lib/systemd/systemd-sleep hibernate\n"
            )
            for name in ("ModemManager.service", "NetworkManager.service"):
                (vendor_dir / name).write_text(
                    "[Unit]\nDescription=Synthetic network service\n[Service]\nType=oneshot\n"
                    "ExecStart=/bin/true\n"
                )
            for name in (
                "sysinit.target",
                "basic.target",
                "shutdown.target",
                "sleep.target",
                "suspend.target",
                "local-fs.target",
            ):
                (vendor_dir / name).write_text(
                    "[Unit]\nDescription=Synthetic fixture target\nDefaultDependencies=no\n"
                )
            (vendor_dir / "systemd-journald.socket").write_text(
                "[Unit]\nDescription=Synthetic journal socket\nDefaultDependencies=no\n"
                "[Socket]\nListenStream=/run/aag-synthetic-journal.socket\n"
            )
            for path in (
                executable_dir / "aag-safe-suspend",
                root / "usr/bin/systemd-inhibit",
                root / "usr/lib/systemd/systemd-sleep",
                root / "bin/true",
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("#!/bin/sh\nexit 0\n")
                path.chmod(0o755)
            result = subprocess.run(
                [
                    analyzer,
                    f"--root={root}",
                    "verify",
                    "aag-external-storage-safe-suspend.service",
                    "aag-external-storage-safe-suspend-resume.service",
                    "aag-external-storage-safe-suspend-failure.service",
                    "aag-external-storage-safe-hibernate-resume.service",
                    "aag-external-storage-safe-hibernate-wwan.service",
                    "aag-external-storage-safe-hibernate-abort.service",
                    "aag-external-storage-safe-hibernate-boot-check.service",
                    "systemd-suspend.service",
                    "systemd-hibernate.service",
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
