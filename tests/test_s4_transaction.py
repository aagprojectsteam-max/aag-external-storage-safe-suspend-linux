from __future__ import annotations

import contextlib
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aag_safe_suspend import power_dispatch as dispatch
from aag_safe_suspend import production as p
from aag_safe_suspend import s4_checks as checks
from aag_safe_suspend import s4_host
from aag_safe_suspend import s4_transaction as s4
from aag_safe_suspend import transaction as tx
from aag_safe_suspend.hibernate import capacity_assessment

JOURNAL = """PM: hibernation: hibernation entry
PM: hibernation: Allocated 123 pages for snapshot
ACPI: PM: Preparing to enter system sleep state S4
ACPI: PM: Waking up from system sleep state S4
efivarfs: removing variable HibernateLocation-fixture
PM: hibernation: hibernation exit
System returned from sleep operation 'hibernate'
"""


class S4RulesTests(unittest.TestCase):
    def test_locked_desktop_with_LockLock_OFF_is_safe_for_prepare_and_resume(self):
        state = {
            "daemon": "running",
            "authorized_session_active": False,
            "ignore_lid_close": False,
            "lid_is_closed": False,
            "sleep_inhibitor_active": False,
            "lid_inhibitor_active": False,
            "grabbed_devices": 0,
            "incomplete_devices": [],
            "release_errors": [],
        }
        with patch.object(p, "run", return_value=SimpleNamespace(stdout=json.dumps(state))):
            self.assertEqual(checks.locklock()["result"], "PASS")
        for field in ("ignore_lid_close", "sleep_inhibitor_active", "lid_inhibitor_active"):
            with patch.object(
                p, "run", return_value=SimpleNamespace(stdout=json.dumps({**state, field: True}))
            ):
                self.assertEqual(checks.locklock()["result"], "FAIL")

    def test_real_DATA_mount_is_distinguished_from_systemd_autofs_underlay(self):
        data = {
            "target": "/mnt/data",
            "source": "/dev/example",
            "fstype": "ext4",
            "uuid": "fixture-data",
        }
        underlay = {"target": "/mnt/data", "source": "systemd-1", "fstype": "autofs", "uuid": None}
        with patch.object(
            p,
            "run",
            return_value=SimpleNamespace(stdout=json.dumps({"filesystems": [underlay, data]})),
        ):
            self.assertEqual(checks.findmnt("/mnt/data"), data)

    def test_autofs_alone_or_two_real_mounts_remain_refusals(self):
        data = {"target": "/mnt/data", "fstype": "ext4", "uuid": "fixture-data"}
        for rows in ([{"target": "/mnt/data", "fstype": "autofs"}], [data, data]):
            with (
                patch.object(
                    p, "run", return_value=SimpleNamespace(stdout=json.dumps({"filesystems": rows}))
                ),
                self.assertRaises(tx.Refusal),
            ):
                checks.findmnt("/mnt/data")

    def value(self):
        value = tx.new("boot", "prep")
        s4.attach(value, {})
        value.update(native_invocation="native", s4_phase="IMAGE_WRITE_REQUESTED")
        return value

    def test_complete_image_resume_requires_all_independent_evidence(self):
        self.assertTrue(
            s4.image_evidence(self.value(), "boot", "native", "success", JOURNAL, True)["confirmed"]
        )
        for line in JOURNAL.splitlines():
            with self.subTest(missing=line):
                self.assertFalse(
                    s4.image_evidence(
                        self.value(), "boot", "native", "success", JOURNAL.replace(line, ""), True
                    )["confirmed"]
                )

    def test_cold_boot_cannot_pass_with_stale_journal(self):
        self.assertFalse(
            s4.image_evidence(self.value(), "new", "native", "success", JOURNAL, True)["confirmed"]
        )

    def test_wrong_invocation_failed_native_or_lost_session_cannot_pass(self):
        for invocation, result, sessions in [
            ("other", "success", True),
            ("native", "exit-code", True),
            ("native", "success", False),
        ]:
            self.assertFalse(
                s4.image_evidence(self.value(), "boot", invocation, result, JOURNAL, sessions)[
                    "confirmed"
                ]
            )

    def test_repeated_kernel_entry_or_filesystem_error_refuses(self):
        for suffix in ("PM: hibernation: hibernation entry", "EXT4-fs error", "Buffer I/O error"):
            self.assertFalse(
                s4.image_evidence(
                    self.value(), "boot", "native", "success", JOURNAL + suffix, True
                )["confirmed"]
            )

    def test_terminal_transaction_is_immutable(self):
        value = self.value()
        value["final_outcome"] = "COMPLETE"
        with self.assertRaises(tx.Refusal):
            s4.observe(value, "BOOT_RESUME_DETECTED")

    def test_memory_policy_preserved_and_measurement_required(self):
        for available in (20 * s4.GIB, 46 * s4.GIB):
            memory = {"MemTotal": 61 * s4.GIB, "MemAvailable": available, "Unevictable": s4.GIB}
            expected = capacity_assessment(memory, 25 * s4.GIB, 72 * s4.GIB)
            actual = s4.memory_gate(memory, 25 * s4.GIB, 72 * s4.GIB)
            self.assertEqual(actual["result"], expected["result"])
        self.assertEqual(s4.memory_gate({}, 25 * s4.GIB, 72 * s4.GIB)["result"], "FAIL")

    def test_insufficient_swap_is_independent_of_memory(self):
        value = s4.memory_gate(
            {"MemTotal": 61 * s4.GIB, "MemAvailable": 46 * s4.GIB}, 25 * s4.GIB, s4.GIB
        )
        self.assertFalse(value["checks"]["swap_free_covers_ram_plus_ten_percent"])

    def test_dispatch_preserves_all_ordinary_suspend_actions(self):
        ordinary = tx.new("boot", "prep")
        for action in (
            "begin",
            "verify-release",
            "v2-teardown",
            "native-pre",
            "native-post",
            "resume-verify-device",
            "failure-recover",
            "retry-suspend",
            "boot-reconcile",
        ):
            self.assertEqual(dispatch.route(action, ordinary), "SUSPEND")

    def test_s4_failure_never_uses_suspend_retry_or_poweroff_monitor(self):
        for action in ("failure-recover", "retry-suspend"):
            self.assertEqual(dispatch.route(action, self.value()), "S4")

    def test_old_s4_must_reconcile_before_new_suspend(self):
        self.assertEqual(dispatch.route("begin", self.value()), "RECONCILE_THEN_SUSPEND")
        self.assertEqual(dispatch.route("begin", self.value(), True), "S4")


class ConsumerRestoreTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "name": "worker",
            "restart_after_resume": "if_was_running",
            "detect": ["/check"],
            "restart_command": ["/start"],
            "stop_timeout": 10,
        }
        self.receipt = {
            "name": "worker",
            "was_running": True,
            "restart_after_resume": "if_was_running",
            "status": "STOPPED",
        }

    def test_restart_and_second_restore_are_idempotent(self):
        run = Mock(side_effect=[{"active": False}, {}, {"active": True}])
        s4.restore_consumers([self.row], [self.receipt], run, Mock())
        s4.restore_consumers([self.row], [self.receipt], run, Mock())
        self.assertEqual(run.call_count, 3)
        self.assertEqual(self.receipt["status"], "RESTORED")

    def test_crash_after_start_does_not_repeat_start(self):
        self.receipt["status"] = "RESTORE_REQUESTED"
        run = Mock(return_value={"active": True})
        s4.restore_consumers([self.row], [self.receipt], run, Mock())
        run.assert_called_once_with(self.row, ["/check"], 15)

    def test_crash_during_stop_reconciles_actual_inactive_state(self):
        self.receipt["status"] = "STOP_REQUESTED"
        run = Mock(side_effect=[{"active": False}, {}, {"active": True}])
        s4.restore_consumers([self.row], [self.receipt], run, Mock())
        self.assertEqual(self.receipt["status"], "RESTORED")

    def test_never_restart_policy_is_preserved_on_abort(self):
        self.receipt.update(restart_after_resume="never", status="STOP_FAILED")
        run = Mock()
        s4.restore_consumers([self.row], [self.receipt], run, Mock())
        run.assert_not_called()
        self.assertEqual(self.receipt["status"], "LEFT_STOPPED_BY_POLICY")

    def test_unknown_detection_or_changed_policy_refuses(self):
        with self.assertRaises(tx.Refusal):
            s4.restore_consumers([self.row], [self.receipt], Mock(return_value={}), Mock())
        with self.assertRaises(tx.Refusal):
            s4.restore_consumers([], [self.receipt], Mock(), Mock())


class WWANOwnerTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.receipt = {}
        self.before = {"healthy": True, "connection_uuid": "profile"}
        self.current = {
            "hardware_ok": True,
            "generation": "gen-a",
            "modem_present": True,
            "healthy": True,
            "connection_uuid": "profile",
        }

    def sleep(self, seconds):
        self.now += seconds

    def run_recovery(self, sample, recover=None, timeout=150):
        recover = Mock() if recover is None else recover
        s4.recover_wwan(
            self.before,
            sample,
            recover,
            Mock(),
            self.receipt,
            clock=lambda: self.now,
            sleep=self.sleep,
            timeout=timeout,
        )
        return recover

    def test_no_recovery_before_settle_and_stable_generation(self):
        recover = self.run_recovery(lambda: self.current)
        self.assertEqual(self.now, 110)
        recover.assert_not_called()

    def test_port_generation_churn_restarts_stability_window(self):
        def sample():
            return {**self.current, "generation": "old" if self.now < 108 else "new"}

        self.run_recovery(sample)
        self.assertEqual(self.now, 118)

    def test_failed_recovery_consumes_one_durable_attempt(self):
        recover = Mock()
        with self.assertRaises(tx.Refusal):
            self.run_recovery(lambda: {**self.current, "healthy": False}, recover, 125)
        recover.assert_called_once()
        self.assertTrue(self.receipt["attempt_consumed"])
        self.now = 0
        with self.assertRaises(tx.Refusal):
            self.run_recovery(lambda: {**self.current, "healthy": False}, recover, 125)
        recover.assert_called_once()

    def test_no_modem_generation_never_authorizes_reset_or_recovery(self):
        recover = Mock()
        with self.assertRaises(tx.Refusal):
            self.run_recovery(lambda: {"hardware_ok": False}, recover)
        recover.assert_not_called()

    def test_previously_disconnected_profile_is_not_forced_connected(self):
        self.before["healthy"] = False
        recover = self.run_recovery(lambda: {**self.current, "healthy": False})
        recover.assert_not_called()


class HostLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.host = s4_host.Host.__new__(s4_host.Host)
        self.host.cfg = {"workloads": []}
        self.host.s4_cfg = {}
        self.host.before = None
        self.state = {"state": "IDLE", "boot_id": "boot"}
        self.boot = "boot"
        self.env = patch.dict(os.environ, INVOCATION_ID="prep", SERVICE_RESULT="success")
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(patch.stopall)
        patch.object(p, "STATE", self.root).start()
        self.prop = patch.object(
            p,
            "properties",
            return_value={"ActiveState": "inactive", "InvocationID": "native", "Job": "0"},
        ).start()
        self.run = patch.object(
            p,
            "run",
            return_value=SimpleNamespace(stdout="-- cursor: fixture", stderr="", returncode=0),
        ).start()
        patch.object(
            checks,
            "findmnt",
            return_value={"target": "/mnt/data", "uuid": "data", "fstype": "ext4"},
        ).start()
        patch.object(checks, "locklock", return_value={"result": "PASS"}).start()
        patch.object(checks, "same_sessions", return_value=True).start()
        self.before = {
            "data": {"target": "/mnt/data", "uuid": "data", "fstype": "ext4"},
            "network": {"healthy": True, "connection_uuid": "profile"},
            "sessions": [{"pid": 10}],
            "locklock": {"result": "PASS"},
            "modem_policy": {"control": "auto"},
        }

        def atomic(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))

        def save(value):
            self.state = copy.deepcopy(value)
            self.state["boot_id"] = self.boot

        self.host.r = SimpleNamespace(
            state_lock=contextlib.nullcontext,
            load_state=lambda: copy.deepcopy(self.state),
            save_state=save,
            atomic_json=atomic,
            boot_id=lambda: self.boot,
            log=Mock(),
            arm_automount_suppression=Mock(),
            validate_automount_marker_for_release=Mock(),
            internal_data_healthy=lambda: (True, "healthy"),
            live_registered_jobs=lambda: [],
            timeshift_processes=lambda: [],
            lid_state=lambda: "open",
            terminal_ugreen_reaudit=Mock(return_value={"ok": True}),
            clear_to_idle=lambda value, end: save(
                {"state": "IDLE", "boot_id": self.boot, "last_terminal": end}
            ),
        )
        self.host.t = SimpleNamespace(
            PENDING=self.root / "pending",
            QUEUE=self.root / "queue",
            BLOCKED=self.root / "blocked",
            policy=lambda: {"control": "auto"},
        )
        self.host.value = None
        self.host.live_owner = Mock(return_value=False)
        self.host.snapshot = Mock(return_value=copy.deepcopy(self.before))
        self.host.permit = Mock(
            return_value={"consumed": True, "before_dispatch": copy.deepcopy(self.before)}
        )
        self.host.quiesce_clones = Mock()
        self.host.restore_clones = Mock()
        self.host.resolve_process_blockers = Mock()
        self.host.audit_storage = Mock(
            return_value={"AUDIT_COMPLETE": True, "EXTERNAL_OWNER": "NONE"}
        )
        self.network = Mock()
        self.host.restore_network = self.network

    def prepared(self):
        self.host.begin()
        self.host.verify_release()
        os.environ["INVOCATION_ID"] = "native"
        with patch.object(checks, "readiness", return_value={"result": "PASS"}):
            self.host.native_pre()

    def resumed(self):
        self.prepared()
        self.host.native_post()
        self.host.journal = Mock(return_value=JOURNAL)
        os.environ["INVOCATION_ID"] = "finish"

    def archive(self):
        rows = list((self.root / "transactions").glob("*.json"))
        self.assertEqual(len(rows), 1)
        return json.loads(rows[0].read_text())

    def test_full_s4_uses_common_preparation_and_completes_durable_ledger(self):
        self.resumed()
        self.host.finish()
        final = self.archive()
        self.assertEqual(final["final_outcome"], "COMPLETE")
        self.assertEqual([x["phase"] for x in final["s4_history"]], list(s4.PHASES))
        self.assertTrue(final["image_resume_confirmed"])
        self.assertEqual(self.state["state"], "IDLE")
        self.assertEqual(json.loads((self.root / "current.json").read_text())["state"], "IDLE")
        self.host.quiesce_clones.assert_called()
        self.host.restore_clones.assert_called_once_with()
        self.host.resolve_process_blockers.assert_called_once()

    def test_NM_prepare_for_sleep_does_not_erase_original_network_obligation(self):
        self.host.snapshot.return_value["network"] = {"healthy": False}
        self.host.begin()
        self.assertTrue(self.state["s4_before"]["network"]["healthy"])
        self.assertEqual(self.state["s4_before"]["network"]["connection_uuid"], "profile")

    def test_reconciling_an_old_suspend_does_not_relabel_it_as_Hibernate(self):
        self.host.before = self.before
        self.host.value = tx.new("boot", "old-suspend-invocation")
        self.host.save()
        self.assertNotIn("transaction_type", self.state)

    def test_usbclone_restore_runs_after_storage_before_other_restores(self):
        self.resumed()
        order = []
        self.host.restore_storage = Mock(side_effect=lambda cold=False: order.append("storage"))
        self.host.restore_clones = Mock(side_effect=lambda: order.append("usbclone"))
        self.host.restore_network = Mock(side_effect=lambda image, cold: order.append("network"))
        self.host.restore_consumers = Mock(side_effect=lambda: order.append("consumers"))
        self.host.finish()
        self.assertEqual(order, ["storage", "usbclone", "network", "consumers"])

    def test_required_consumer_restored_through_common_durable_receipt(self):
        self.resumed()
        row = {
            "name": "managed",
            "storage_dependency": "DATA",
            "restart_after_resume": "if_was_running",
            "detect": ["/check"],
            "restart_command": ["/start"],
            "stop_timeout": 10,
        }
        self.host.cfg["workloads"] = [row]
        self.state["stopped_workloads"] = [
            {
                "name": "managed",
                "was_running": True,
                "restart_after_resume": "if_was_running",
                "status": "STOPPED",
            }
        ]
        self.host.workload_run = Mock(side_effect=[{"active": False}, {}, {"active": True}])
        self.host.finish()
        self.assertEqual(self.archive()["stopped_workloads"][0]["status"], "RESTORED")

    def test_duplicate_finish_does_not_restore_twice(self):
        self.resumed()
        self.host.finish()
        self.host.finish()
        self.network.assert_called_once()

    def test_image_write_failure_restores_without_claiming_resume(self):
        self.prepared()
        os.environ["SERVICE_RESULT"] = "exit-code"
        self.host.native_post()
        self.host.journal = Mock(return_value="PM: hibernation: hibernation entry")
        self.host.finish()
        self.assertEqual(self.archive()["final_outcome"], "IMAGE_WRITE_FAILED")
        self.assertFalse(self.archive()["image_resume_confirmed"])
        self.assertEqual(self.state["state"], "IDLE")

    def test_final_memory_gate_failure_aborts_preparation_without_poweroff(self):
        self.host.begin()
        self.host.verify_release()
        os.environ["INVOCATION_ID"] = "native"
        with (
            patch.object(
                checks, "readiness", return_value={"result": "FAIL", "MEMORY_GATE": "FAIL"}
            ),
            self.assertRaises(tx.Refusal),
        ):
            self.host.native_pre()
        self.host.finish()
        self.assertEqual(self.archive()["final_outcome"], "PREP_FAILED")
        self.assertEqual(self.state["state"], "IDLE")
        self.assertFalse(any("poweroff" in str(call) for call in self.run.call_args_list))

    def test_resume_device_mismatch_uses_same_clean_abort_path(self):
        self.host.begin()
        self.host.verify_release()
        os.environ["INVOCATION_ID"] = "native"
        with (
            patch.object(
                checks, "readiness", return_value={"result": "FAIL", "RESUME_DEVICE": "FAIL"}
            ),
            self.assertRaises(tx.Refusal),
        ):
            self.host.native_pre()
        self.host.finish()
        self.assertEqual(self.archive()["final_outcome"], "PREP_FAILED")

    def test_preparation_blocker_failure_does_not_poison_later_suspend(self):
        self.host.resolve_process_blockers.side_effect = tx.Refusal("unknown privileged owner")
        with self.assertRaises(tx.Refusal):
            self.host.begin()
        self.host.finish()
        self.assertEqual(self.archive()["final_outcome"], "PREP_FAILED")
        self.assertEqual(dispatch.route("begin", self.state), "SUSPEND")

    def test_cold_boot_marks_failure_and_clears_only_its_transaction(self):
        self.prepared()
        old = copy.deepcopy(self.state)
        self.boot = "next-boot"
        self.state = {"state": "IDLE", "boot_id": self.boot}
        self.host.boot()
        self.network.assert_not_called()
        self.host.finish()  # the queued finish service, after the boot unit exits
        final = self.archive()
        self.assertEqual(final["origin_boot_id"], old["origin_boot_id"])
        self.assertEqual(final["final_outcome"], "COLD_BOOT_FALLBACK")
        self.assertFalse(final["image_resume_confirmed"])
        self.assertEqual(self.state["state"], "IDLE")

    def test_reboot_after_WWAN_failure_preserves_image_evidence_and_delegates(self):
        self.resumed()
        self.network.side_effect = tx.Refusal("WWAN not restored")
        with self.assertRaises(tx.Refusal):
            self.host.finish()
        old = copy.deepcopy(self.state)
        self.boot = "reboot-after-resume"
        self.state = {"state": "IDLE", "boot_id": self.boot}
        self.network.reset_mock(side_effect=True)
        self.host.boot()
        self.network.assert_not_called()
        prior = self.root / "s4-evidence" / (old["episode"] + "-before-boot-" + self.boot + ".json")
        self.assertEqual(json.loads(prior.read_text()), old)
        self.host.finish()
        final = self.archive()
        self.assertEqual(final["final_outcome"], "REBOOT_AFTER_RESUME_FAILURE")
        self.assertTrue(final["image_resume_confirmed"])
        self.assertEqual(final["native_invocation"], old["native_invocation"])
        self.assertEqual(self.state["state"], "IDLE")

    def test_evidence_read_failure_is_durable_and_can_be_reassessed(self):
        self.resumed()
        self.host.journal.side_effect = tx.Refusal("journal unavailable")
        with self.assertRaises(tx.Refusal):
            self.host.finish()
        self.assertEqual(self.state["s4_phase"], "RESTORE_FAILED")
        self.host.journal.side_effect = None
        self.host.finish()
        self.assertEqual(self.archive()["final_outcome"], "COMPLETE")

    def test_cold_boot_never_overwrites_another_live_transaction(self):
        self.prepared()
        self.boot = "next-boot"
        self.state = tx.new(self.boot, "other")
        with self.assertRaises(tx.Refusal):
            self.host.boot()
        self.assertEqual(self.state["prep_invocation"], "other")

    def test_cold_boot_can_reconcile_before_desktop_login(self):
        self.prepared()
        self.boot = "next-boot"
        self.state = {"state": "IDLE", "boot_id": self.boot}
        with patch.object(checks, "locklock", side_effect=tx.Refusal("desktop not logged in")):
            self.host.boot()
            self.network.assert_not_called()
            self.host.finish()
        self.assertEqual(self.archive()["final_outcome"], "COLD_BOOT_FALLBACK")
        self.assertEqual(self.state["state"], "IDLE")

    def test_stale_native_callback_cannot_relabel_transaction(self):
        self.prepared()
        before = copy.deepcopy(self.state)
        os.environ["INVOCATION_ID"] = "stale"
        self.host.native_post()
        self.assertEqual(self.state, before)

    def test_DATA_restore_failure_can_be_reconciled_without_another_cycle(self):
        self.resumed()
        self.host.r.internal_data_healthy = lambda: (False, "not healthy")
        with self.assertRaises(tx.Refusal):
            self.host.finish()
        self.assertEqual(self.state["s4_phase"], "RESTORE_FAILED")
        self.assertEqual(self.state["state"], "FAILURE_PENDING")
        self.host.r.internal_data_healthy = lambda: (True, "healthy")
        self.host.finish()
        self.assertEqual(self.archive()["final_outcome"], "COMPLETE")

    def test_UGREEN_identity_failure_holds_fence(self):
        self.resumed()
        self.host.r.terminal_ugreen_reaudit.return_value = {
            "ok": False,
            "reason": "generation changed",
        }
        with self.assertRaises(tx.Refusal):
            self.host.finish()
        self.assertEqual(self.state["state"], "FAILURE_PENDING")

    def test_FM350_failure_is_not_hidden_by_storage_success(self):
        self.resumed()
        self.network.side_effect = tx.Refusal("modem disappeared")
        with self.assertRaises(tx.Refusal):
            self.host.finish()
        self.assertEqual(self.state["s4_phase"], "RESTORE_FAILED")
        self.assertTrue(self.state["image_resume_confirmed"])
        self.assertIsNone(self.state["final_outcome"])

    def test_LockLock_interoperability_requires_safe_return(self):
        self.resumed()
        with (
            patch.object(checks, "locklock", return_value={"result": "FAIL"}),
            self.assertRaises(tx.Refusal),
        ):
            self.host.finish()
        self.assertEqual(self.state["s4_phase"], "RESTORE_FAILED")


if __name__ == "__main__":
    unittest.main()
