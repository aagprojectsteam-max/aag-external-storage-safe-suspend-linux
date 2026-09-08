from __future__ import annotations

import unittest

from aag_safe_suspend import job_guard


class JobGuardTests(unittest.TestCase):
    def classify(self, unit: str, operation: str = "start") -> tuple[str, bool, str]:
        return job_guard.classify({"id": "1", "unit": unit, "type": operation, "state": "waiting"})

    def test_real_power_jobs_are_blocked(self) -> None:
        for unit in (
            "systemd-suspend.service",
            "systemd-hibernate.service",
            "poweroff.target",
            "shutdown.target",
            "reboot.target",
            "suspend-then-hibernate.target",
        ):
            with self.subTest(unit=unit):
                self.assertTrue(self.classify(unit)[1])

    def test_storage_transactions_are_blocked(self) -> None:
        for unit in (
            "media-backup.mount",
            "udisks2.service",
            "cryptsetup.target",
            "timeshift.service",
        ):
            with self.subTest(unit=unit):
                self.assertTrue(self.classify(unit)[1])

    def test_project_r1_and_t700_examples_fail_closed(self) -> None:
        for unit in (
            "aag-external-storage-safe-suspend.service",
            "aag-storage-sleep-v2.service",
            "aag-t700-resume-check.service",
        ):
            with self.subTest(unit=unit):
                self.assertTrue(self.classify(unit)[1])

    def test_routine_unrelated_jobs_are_allowed(self) -> None:
        for unit in (
            "cups.service",
            "apt-daily.service",
            "sysstat-collect.service",
            "fwupd-refresh.service",
        ):
            with self.subTest(unit=unit):
                category, conflict, _reason = self.classify(unit)
                self.assertEqual(category, "ROUTINE_UNRELATED")
                self.assertFalse(conflict)

    def test_unknown_job_fails_closed(self) -> None:
        category, conflict, _reason = self.classify("unexpected-agent.service")
        self.assertEqual(category, "UNKNOWN")
        self.assertTrue(conflict)

    def test_malformed_job_fails_closed(self) -> None:
        parsed = job_guard.parse_jobs("not-a-valid-job")
        self.assertTrue(job_guard.classify(parsed[0])[1])


if __name__ == "__main__":
    unittest.main()
