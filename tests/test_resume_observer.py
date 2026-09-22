from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aag_safe_suspend import resume_observer as observer


class ResumeObserverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_dir = Path(self.tmp.name) / "logs"
        self.patch = patch.object(observer, "LOG_DIR", self.log_dir)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def rows(self, kind="suspend", episode="episode"):
        path = observer.log_path(kind, episode)
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_start_stage_success_create_one_structured_log(self):
        cfg = {"notification_user": "user", "notification_uid": 1000}
        with patch.object(observer, "_notify", return_value=True) as notify:
            marker = observer.start(cfg, "suspend", "episode", source="test")
            self.assertEqual(notify.call_args.args[1], "AAG — Returning from Sleep")
            self.assertIn("Restoring devices, services", notify.call_args.args[2])
            observer.stage("suspend", "episode", "STORAGE_RESTORE", "START")
            observer.stage("suspend", "episode", "STORAGE_RESTORE", "PASS")
            observer.success(cfg, "suspend", "episode", started_at=marker["started_at"])
            self.assertEqual(notify.call_args.args[1], "AAG — Return from Sleep Complete")
            self.assertIn("All resume checks passed", notify.call_args.args[2])
        rows = self.rows()
        self.assertEqual(
            [row["event"] for row in rows],
            ["RESUME_STARTED", "RESUME_STAGE", "RESUME_STAGE", "RESUME_COMPLETE"],
        )
        self.assertEqual(rows[-1]["result"], "PASS")
        self.assertEqual(rows[1]["phase"], "STORAGE_RESTORE")
        self.assertEqual(rows[2]["status"], "PASS")
        self.assertIsNotNone(rows[-1]["duration_seconds"])
        self.assertEqual(os.stat(observer.log_path("suspend", "episode")).st_mode & 0o777, 0o640)

    def test_failure_records_phase_reason_and_critical_notification(self):
        cfg = {}
        with patch.object(observer, "_notify", return_value=False) as notify:
            observer.failure(
                cfg, "hibernate", "episode",
                phase="WWAN_RESTORE", reason="timeout",
            )
        row = self.rows("hibernate")[0]
        self.assertEqual(row["event"], "RESUME_FAILED")
        self.assertEqual(row["phase"], "WWAN_RESTORE")
        self.assertEqual(row["reason"], "timeout")
        self.assertEqual(row["result"], "FAIL")
        self.assertEqual(notify.call_args.kwargs["urgency"], "critical")

    def test_notify_forces_utf8_locale_for_unicode_text(self):
        cfg = {"notification_user": "user", "notification_uid": 1000}
        completed = type("Completed", (), {"returncode": 0})()
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(observer.subprocess, "run", return_value=completed) as run,
        ):
            self.assertTrue(observer._notify(cfg, "AAG — Returning from Sleep", "Resume complete — verified"))
        argv = run.call_args.args[0]
        self.assertIn("LANG=C.UTF-8", argv)
        self.assertIn("LC_ALL=C.UTF-8", argv)
        self.assertIn("AAG — Returning from Sleep", argv)
        self.assertIn("Resume complete — verified", argv)

    def test_failed_items_name_only_real_unrestored_components(self):
        value = {
            "stopped_workloads": [
                {"name": "AnythingLLM", "was_running": True, "restart_after_resume": "if_was_running", "status": "RESTORE_REQUESTED"},
                {"name": "Never", "was_running": True, "restart_after_resume": "never", "status": "LEFT_STOPPED_BY_POLICY"},
                {"name": "Healthy", "was_running": True, "restart_after_resume": "if_was_running", "status": "RESTORED"},
            ],
            "usbclone_stopped": [
                {"name": "usbclone_kingston", "was_running": True, "restart_after_resume": "if_was_running", "status": "RESTORE_FAILED"},
            ],
            "s4_wwan_receipt": {"status": "RESTORE_FAILED"},
        }
        self.assertEqual(
            observer.failed_items(value, "S4_RESTORE"),
            ["AnythingLLM", "USB Clone: kingston", "Modem / WWAN"],
        )

    def test_failure_notification_includes_failed_component_names(self):
        cfg = {}
        with patch.object(observer, "_notify", return_value=True) as notify:
            observer.failure(
                cfg, "hibernate", "episode",
                phase="S4_RESTORE", reason="timeout",
                failed_items=["AnythingLLM", "USB Clone: kingston"],
            )
        self.assertEqual(notify.call_args.args[1], "AAG — Return from Hibernate Failed")
        body = notify.call_args.args[2]
        self.assertIn("Return from Hibernate failed during S4_RESTORE.", body)
        self.assertIn("AnythingLLM", body)
        self.assertIn("USB Clone: kingston", body)
        self.assertIn("Details were saved to", body)
        row = self.rows("hibernate")[0]
        self.assertEqual(row["failed_items"], ["AnythingLLM", "USB Clone: kingston"])

    def test_logging_failure_is_best_effort(self):
        impossible = Path(self.tmp.name) / "not-a-directory"
        impossible.write_text("x")
        with patch.object(observer, "LOG_DIR", impossible):
            self.assertIsNone(observer.stage("suspend", "episode", "X", "PASS"))

    def test_prune_keeps_bounded_history(self):
        with patch.object(observer, "KEEP_LOGS", 2):
            for index in range(4):
                observer.stage("suspend", f"episode-{index}", "X", "PASS")
                os.utime(observer.log_path("suspend", f"episode-{index}"), (index + 1, index + 1))
            observer.prune()
        self.assertEqual(len(list(self.log_dir.glob("*.jsonl"))), 2)


if __name__ == "__main__":
    unittest.main()
