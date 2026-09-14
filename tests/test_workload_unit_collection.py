"""Checkpoint verification across collection of transient systemd user units."""

import subprocess
import unittest
from unittest.mock import patch

from aag_safe_suspend import production, transaction, workloads


class CollectedWorkloadTests(unittest.TestCase):
    def setUp(self):
        self.host = production.Host.__new__(production.Host)
        self.row = {
            "name": "checkpoint fixture",
            "importance": "nonessential_checkpointable",
            "protocol": "systemd-user",
            "unit": "fixture.service",
            "user": "fixture-user",
            "uid": 1234,
            "detect": ["/usr/bin/systemctl", "--user", "is-active", "fixture.service"],
            "safe_stop_command": ["/opt/fixture/checkpoint", "stop"],
            "verify_stopped": ["/usr/bin/systemctl", "--user", "is-active", "fixture.service"],
            "stop_timeout": 15,
            "restart_after_resume": "never",
            "restart_command": [],
            "storage_dependency": "compute",
        }
        self.absent = "LoadState=not-found\nActiveState=inactive\nSubState=dead\nMainPID=0\n"

    def result(self, rc, stdout="", stderr=""):
        return subprocess.CompletedProcess([], rc, stdout, stderr)

    def observe(self):
        return self.host.workload_run(self.row, self.row["detect"], 15)

    def test_collected_checkpoint_is_verified_and_left_stopped_by_policy(self):
        replies = [
            self.result(0, "active\n"),
            self.result(0, '{"durable_state_preserved":true}'),
            self.result(4, "inactive\n"),
            self.result(0, self.absent),
        ]
        receipts = []
        with patch.object(production, "run", side_effect=replies) as run:
            workloads.quiesce(
                [self.row],
                {"compute"},
                self.host.workload_run,
                lambda: None,
                receipts,
                lambda *a, **kw: None,
            )
            self.assertEqual(receipts[0]["status"], "STOPPED")
            workloads.restore([self.row], receipts, self.host.workload_run, lambda: None)
            self.assertEqual(receipts[0]["status"], "LEFT_STOPPED_BY_POLICY")
            self.assertEqual(run.call_count, 4)
            argv = run.call_args.args[0]
            self.assertEqual(argv[:4], ["/usr/sbin/runuser", "-u", "fixture-user", "--"])
            self.assertIn("XDG_RUNTIME_DIR=/run/user/1234", argv)
            self.assertIn("DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1234/bus", argv)

    def test_absent_workload_is_not_stopped_or_restarted(self):
        with patch.object(
            production,
            "run",
            side_effect=[self.result(4, "inactive\n"), self.result(0, self.absent)],
        ) as run:
            receipts = []
            workloads.quiesce(
                [self.row],
                {"compute"},
                self.host.workload_run,
                lambda: None,
                receipts,
                lambda *a, **kw: None,
            )
            self.assertEqual(receipts, [])
            self.assertEqual(run.call_count, 2)

    def test_unreachable_manager_never_confirms_stop(self):
        for rc in (1, 4):
            with (
                self.subTest(rc=rc),
                patch.object(
                    production,
                    "run",
                    side_effect=[
                        self.result(rc, "inactive\n", "Failed to connect to bus"),
                        self.result(1, "", "Failed to connect to bus"),
                    ],
                ),
            ):
                with self.assertRaises(transaction.Refusal):
                    self.observe()

    def test_recreated_or_incompletely_observed_unit_does_not_confirm_absence(self):
        cases = [
            self.absent.replace("inactive", "active"),
            self.absent.replace("not-found", "loaded"),
            self.absent.replace("MainPID=0", "MainPID=42"),
            self.absent.replace("SubState=dead\n", ""),
            "",
        ]
        for state in cases:
            with (
                self.subTest(state=state),
                patch.object(
                    production,
                    "run",
                    side_effect=[self.result(4, "inactive\n"), self.result(0, state)],
                ),
            ):
                with self.assertRaises(transaction.Refusal):
                    self.observe()

    def test_absence_cannot_replace_a_missing_durable_checkpoint(self):
        receipts = []
        with patch.object(
            production,
            "run",
            side_effect=[
                self.result(0, "active\n"),
                self.result(0, "{}"),
                self.result(4, "inactive\n"),
                self.result(0, self.absent),
            ],
        ):
            with self.assertRaises(transaction.Refusal):
                workloads.quiesce(
                    [self.row],
                    {"compute"},
                    self.host.workload_run,
                    lambda: None,
                    receipts,
                    lambda *a, **kw: None,
                )
        self.assertEqual(receipts[0]["status"], "STOP_FAILED")

    def test_existing_active_and_inactive_units_keep_original_semantics(self):
        for rc, state, expected in [(0, "active", True), (3, "inactive", False)]:
            with (
                self.subTest(state=state),
                patch.object(production, "run", return_value=self.result(rc, state + "\n")) as run,
            ):
                self.assertEqual(self.observe(), {"active": expected})
                run.assert_called_once()
