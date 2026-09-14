"""Regressions for the failed S4 owner handoff; no physical power operations."""

from __future__ import annotations

import fcntl
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aag_safe_suspend import power_dispatch as dispatch
from aag_safe_suspend import production as p
from aag_safe_suspend import s4_checks as checks
from aag_safe_suspend import s4_host
from aag_safe_suspend import s4_transaction as s4
from aag_safe_suspend.transaction import Refusal


class S4IncidentTests(unittest.TestCase):
    def test_finish_waits_for_storage_teardown_without_owning_its_lock(self):
        # Native becomes inactive before StopWhenUnneeded queues V2 teardown.
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "operation.lock"
            fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            done = threading.Event()
            host = Mock()
            value = {"transaction_type": "HIBERNATE", "state": "SUSPEND_REQUESTED"}

            def teardown():
                time.sleep(0.15)
                os.close(fd)
                done.set()

            def properties(unit):
                return {
                    "ActiveState": "deactivating"
                    if unit == p.V2 and not done.is_set()
                    else "inactive",
                    "Job": "0",
                }

            thread = threading.Thread(target=teardown)
            thread.start()
            try:
                with (
                    patch.object(dispatch.os, "geteuid", return_value=0),
                    patch.object(dispatch, "LOCK", lock),
                    patch.object(dispatch, "current", return_value=value),
                    patch.object(dispatch, "Host", return_value=host),
                    patch.object(p, "properties", side_effect=properties),
                ):
                    self.assertEqual(dispatch.main(["hibernate-finish"]), 0)
                host.finish.assert_called_once()
                self.assertTrue(done.is_set())
            finally:
                thread.join()

    def test_journal_boot_selector_is_canonical_32_hex_not_sysfs_uuid(self):
        host = s4_host.Host.__new__(s4_host.Host)
        host.value = {
            "s4_cursor": "cursor",
            "origin_boot_id": "00000000-" + "0000-4000-8000-000000000001",
        }

        def run(argv, **kwargs):
            self.assertEqual(argv[argv.index("-b") + 1], "00000000000040008000000000000001")
            return SimpleNamespace(stdout="evidence")

        with patch.object(p, "run", side_effect=run):
            self.assertEqual(host.journal(), "evidence")

    def test_effective_native_dependencies_must_exclude_retired_owners(self):
        host = s4_host.Host.__new__(s4_host.Host)
        with tempfile.TemporaryDirectory() as directory:
            host.t = SimpleNamespace(
                **{x: Path(directory) / x for x in ("PENDING", "QUEUE", "BLOCKED")}
            )

            def run(argv, **kwargs):
                if argv[2] == s4_host.NATIVE:
                    return SimpleNamespace(
                        stdout="OnSuccess="
                        + s4.OWNER
                        + " aag-hibernate-resume-check.service\nOnFailure="
                        + s4.OWNER
                        + "\nExecStartPre=hibernate-native-pre\nExecStopPost=hibernate-native-post\n"
                    )
                return SimpleNamespace(
                    stdout="ExecStart=/usr/bin/false\nActiveState=inactive\nJob=0\n"
                )

            with (
                patch.object(p, "run", side_effect=run),
                patch.object(p, "properties", return_value={"ActiveState": "inactive", "Job": "0"}),
            ):
                self.assertEqual(host.owner_graph()["result"], "FAIL")

    def test_MM_restart_waits_for_modem_enumeration_before_profile_activation(self):
        host = s4_host.Host.__new__(s4_host.Host)
        host.s4_cfg = {}
        host.value = {"s4_wwan_receipt": {"attempt_consumed": True}}
        host.save = Mock()
        now = [0.0]
        restarted = [False]
        commands = []

        def sample():
            return {
                "hardware_ok": True,
                "generation": "g",
                "healthy": False,
                "modem_present": restarted[0] and now[0] >= 3,
            }

        host.network_sample = sample

        def run(argv, **kwargs):
            commands.append(argv)
            if "restart" in argv:
                restarted[0] = True
            if "connection" in argv:
                self.assertGreaterEqual(now[0], 3, "NM activation raced MM D-Bus enumeration")
            return SimpleNamespace(returncode=0, stdout="")

        with (
            patch.object(
                checks, "modem_generation", return_value={"hardware_ok": True, "generation": "g"}
            ),
            patch.object(p, "run", side_effect=run),
            patch.object(s4_host.time, "monotonic", side_effect=lambda: now[0]),
            patch.object(
                s4_host.time, "sleep", side_effect=lambda n: now.__setitem__(0, now[0] + n)
            ),
        ):
            host.recover_modem({"healthy": True, "connection_uuid": "profile"}, "g")
        self.assertEqual(sum("restart" in x for x in commands), 1)
        self.assertEqual(sum("connection" in x for x in commands), 1)

    def test_wwan_failure_records_callback_error_without_repeating_action(self):
        receipt = {}
        now = [0.0]
        recover = Mock(side_effect=Refusal("enumeration timed out"))

        def sample():
            return {
                "hardware_ok": True,
                "generation": "g",
                "modem_present": False,
                "healthy": False,
            }

        with self.assertRaises(Refusal):
            s4.recover_wwan(
                {"healthy": True},
                sample,
                recover,
                Mock(),
                receipt,
                clock=lambda: now[0],
                sleep=lambda n: now.__setitem__(0, now[0] + n),
                settle=0,
                timeout=20,
            )
        self.assertTrue(receipt["attempt_consumed"])
        self.assertEqual(receipt["status"], "RESTORE_FAILED")
        self.assertIn("enumeration timed out", receipt["error"])
        recover.assert_called_once()

    def test_reboot_after_native_return_is_not_image_write_cold_boot_fallback(self):
        value = {
            "image_resume_confirmed": False,
            "s4_native_result": "success",
            "s4_native_returned": 100,
            "native_invocation": "native",
            "s4_failure_outcome": None,
        }
        self.assertEqual(s4.reboot_outcome(value), "REBOOT_AFTER_RESUME_FAILURE")
        value.pop("s4_native_returned")
        self.assertEqual(s4.reboot_outcome(value), "COLD_BOOT_FALLBACK")
        value["image_resume_confirmed"] = True
        self.assertEqual(s4.reboot_outcome(value), "REBOOT_AFTER_RESUME_FAILURE")

    def test_busy_teardown_has_bounded_deadline_and_no_recovery(self):
        now = [0.0]
        with (
            patch.object(p, "properties", return_value={"ActiveState": "deactivating", "Job": "2"}),
            self.assertRaisesRegex(Refusal, "teardown did not finish"),
        ):
            dispatch.wait_for_teardown(
                timeout=1, clock=lambda: now[0], sleep=lambda n: now.__setitem__(0, now[0] + n)
            )
        self.assertLess(now[0], 1.2)

    def test_boot_handoff_waits_for_boot_callback_to_release_ownership(self):
        now = [0.0]

        def properties(unit):
            return {
                "ActiveState": "activating"
                if "boot.service" in unit and now[0] < 0.5
                else "inactive",
                "Job": "0",
            }

        with patch.object(p, "properties", side_effect=properties):
            dispatch.wait_for_teardown(
                timeout=2, clock=lambda: now[0], sleep=lambda n: now.__setitem__(0, now[0] + n)
            )
        self.assertGreaterEqual(now[0], 0.5)

    def test_generation_loss_during_MM_start_refuses_NM_and_second_restart(self):
        host = s4_host.Host.__new__(s4_host.Host)
        host.s4_cfg = {}
        host.network_sample = Mock(return_value={"modem_present": False, "healthy": False})
        samples = [{"hardware_ok": True, "generation": "g"}, {"hardware_ok": False}]
        with (
            patch.object(checks, "modem_generation", side_effect=samples),
            patch.object(p, "run") as run,
            self.assertRaisesRegex(Refusal, "generation changed"),
        ):
            host.recover_modem({"healthy": True, "connection_uuid": "profile"}, "g")
        run.assert_called_once_with(
            ["/usr/bin/systemctl", "restart", "ModemManager.service"], timeout=40
        )

    def test_MM_enumeration_timeout_does_not_activate_profile_or_restart_again(self):
        host = s4_host.Host.__new__(s4_host.Host)
        host.s4_cfg = {}
        host.network_sample = Mock(return_value={"modem_present": False, "healthy": False})
        now = [0.0]
        with (
            patch.object(
                checks, "modem_generation", return_value={"hardware_ok": True, "generation": "g"}
            ),
            patch.object(p, "run") as run,
            patch.object(s4_host.time, "monotonic", side_effect=lambda: now[0]),
            patch.object(
                s4_host.time, "sleep", side_effect=lambda n: now.__setitem__(0, now[0] + n)
            ),
            self.assertRaisesRegex(Refusal, "enumeration timed out"),
        ):
            host.recover_modem({"healthy": True, "connection_uuid": "profile"}, "g")
        self.assertEqual(now[0], 45)
        run.assert_called_once_with(
            ["/usr/bin/systemctl", "restart", "ModemManager.service"], timeout=40
        )

    def test_wwan_nonowner_is_rejected_before_any_network_action(self):
        host = s4_host.Host.__new__(s4_host.Host)
        host.network_sample = Mock()
        with (
            patch.dict(os.environ, INVOCATION_ID="boot-not-finish"),
            patch.object(p, "properties", return_value={"InvocationID": "finish"}),
            self.assertRaisesRegex(Refusal, "selected finish service"),
        ):
            host.restore_network(True, False)
        host.network_sample.assert_not_called()
