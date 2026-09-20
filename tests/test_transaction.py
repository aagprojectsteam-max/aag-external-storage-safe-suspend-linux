from __future__ import annotations

import contextlib
import copy
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aag_safe_suspend import production, workloads
from aag_safe_suspend import transaction as tx


class RulesTests(unittest.TestCase):
    def test_fresh_transactions_never_share_retry_tokens(self):
        a = tx.new("boot", "request-a", now=1)
        tx.fail(a, "PREPARE", "busy")
        tx.queue_retry(a, 2)
        b = tx.new("boot", "request-b", now=3)
        self.assertNotEqual(a["episode"], b["episode"])
        self.assertEqual(b["retry_count"], 0)
        self.assertEqual(b["retry_state"], "RETRY_AVAILABLE")

    def test_stale_failure_and_consumed_retry_require_reconciliation(self):
        a = tx.new("boot", "old")
        tx.fail(a, "PREPARE", "original process is gone")
        a["retry_count"] = 1
        self.assertEqual(tx.request_disposition(a, "boot", "new", False), "RECONCILE")

    def test_live_request_coalesces_even_if_deadline_has_expired(self):
        a = tx.new("boot", "a", now=-10000)
        self.assertEqual(tx.request_disposition(a, "boot", "b", True), "COALESCE")

    def test_repeated_lid_does_not_reset_retry_budget(self):
        a = tx.new("boot", "a")
        tx.fail(a, "KERNEL_ENTRY", "device failed")
        tx.queue_retry(a, 3)
        self.assertEqual(tx.request_disposition(a, "boot", "b", False), "RETRY")
        tx.fail(a, "KERNEL_ENTRY", "still failed")
        with self.assertRaises(tx.Refusal):
            tx.queue_retry(a, 4)

    def test_previous_boot_is_not_replayed(self):
        self.assertEqual(
            tx.request_disposition(tx.new("old", "a"), "new", "b", False), "BOOT_RECONCILE"
        )

    def test_command_success_is_not_sleep(self):
        evidence = tx.sleep_evidence({"success": 2}, {"success": 2}, "success")
        self.assertFalse(evidence["kernel_cycle"])
        self.assertFalse(evidence["actual_sleep"])
        self.assertEqual(evidence["sleep_class"], "NO_KERNEL_SLEEP")

    def test_kernel_success_without_residency_is_not_low_power(self):
        evidence = tx.sleep_evidence(
            {"success": 2, "total_hw_sleep": 9}, {"success": 3, "total_hw_sleep": 9}, "success"
        )
        self.assertTrue(evidence["kernel_cycle"])
        self.assertFalse(evidence["actual_sleep"])

    def test_actual_sleep_and_resume(self):
        evidence = tx.sleep_evidence(
            {"success": 2, "total_hw_sleep": 9, "boottime": 2, "monotonic": 2},
            {"success": 3, "total_hw_sleep": 10000009, "boottime": 14, "monotonic": 4},
            "success",
        )
        self.assertTrue(evidence["actual_sleep"])
        self.assertFalse(evidence["immediate_wake"])
        self.assertEqual(evidence["suspended_seconds"], 10)

    def test_immediate_wake_is_distinct(self):
        e = tx.sleep_evidence(
            {"success": 0, "total_hw_sleep": 0}, {"success": 1, "total_hw_sleep": 20}, "success"
        )
        self.assertTrue(e["immediate_wake"])

    def test_missing_telemetry_never_claims_sleep(self):
        self.assertFalse(tx.sleep_evidence({}, {}, "success")["actual_sleep"])

    def test_normal_temperature_never_powers_off_for_elapsed_retry_budget(self):
        for elapsed in (0, 900, 9000, 90000):
            action, _ = tx.safety_action(
                {"state": "NORMAL"},
                {},
                warning_seconds=elapsed,
                missing_seconds=elapsed,
                lid="closed",
            )
            self.assertEqual(action, "monitor")

    def test_critical_battery_retains_safe_shutdown(self):
        self.assertEqual(tx.safety_action({"state": "NORMAL"}, {"critical": True})[0], "poweroff")

    def test_actual_thermal_emergency_still_has_failsafe(self):
        self.assertEqual(tx.safety_action({"state": "THERMAL_EMERGENCY"}, {})[0], "poweroff")
        self.assertEqual(
            tx.safety_action({"state": "THERMAL_WARNING"}, {}, warning_seconds=59)[0], "monitor"
        )
        self.assertEqual(
            tx.safety_action({"state": "THERMAL_WARNING"}, {}, warning_seconds=60)[0], "poweroff"
        )

    def test_missing_temperature_bounded_closed_lid_safety(self):
        self.assertEqual(
            tx.safety_action({"state": "UNKNOWN"}, {}, missing_seconds=299)[0], "monitor"
        )
        self.assertEqual(
            tx.safety_action({"state": "UNKNOWN"}, {}, missing_seconds=300)[0], "poweroff"
        )
        self.assertEqual(
            tx.safety_action({"state": "UNKNOWN"}, {}, missing_seconds=999, lid="open")[0],
            "monitor",
        )


class WorkloadTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(
            name="checkpoint worker",
            importance="nonessential_checkpointable",
            detect=["/usr/bin/check"],
            safe_stop_command=["/usr/bin/checkpoint"],
            verify_stopped=["/usr/bin/check"],
            stop_timeout=20,
            restart_after_resume="never",
            storage_dependency="DATA",
        )

    def test_checkpoint_before_suspend_and_never_auto_resume(self):
        receipts = []
        run = Mock(
            side_effect=[{"active": True}, {"durable_state_preserved": True}, {"active": False}]
        )
        save = Mock()
        workloads.quiesce([self.row], {"DATA"}, run, save, receipts, Mock())
        self.assertEqual(receipts[0]["status"], "STOPPED")
        self.assertGreaterEqual(save.call_count, 2)
        workloads.restore([self.row], receipts, run, save)
        self.assertEqual(receipts[0]["status"], "LEFT_STOPPED_BY_POLICY")
        self.assertEqual(run.call_count, 3)

    def test_graceful_stop_failure_never_escalates_to_kill(self):
        run = Mock(
            side_effect=[{"active": True}, {"durable_state_preserved": False}, {"active": True}]
        )
        receipts = []
        with self.assertRaises(tx.Refusal):
            workloads.quiesce([self.row], {"DATA"}, run, Mock(), receipts, Mock())
        self.assertEqual(receipts[0]["status"], "STOP_FAILED")
        self.assertEqual(run.call_count, 3)

    def test_unrelated_mount_and_critical_workload_untouched(self):
        run = Mock()
        workloads.quiesce([self.row], {"UGREEN"}, run, Mock(), [], Mock())
        critical = {**self.row, "importance": "critical"}
        workloads.quiesce([critical], {"DATA"}, run, Mock(), [], Mock())
        run.assert_not_called()

    def test_inactive_workload_not_restarted(self):
        run = Mock(return_value={"active": False})
        receipts = []
        workloads.quiesce([self.row], {"DATA"}, run, Mock(), receipts, Mock())
        self.assertEqual(receipts, [])

    def test_explicit_restore_only_stopped_workload(self):
        row = {
            **self.row,
            "restart_after_resume": "if_was_running",
            "restart_command": ["/usr/bin/resume"],
        }
        receipts = [
            {
                "name": row["name"],
                "status": "STOPPED",
                "was_running": True,
                "restart_after_resume": "if_was_running",
            }
        ]
        run = Mock(side_effect=[{}, {"active": True}])
        workloads.restore([row], receipts, run, Mock())
        self.assertEqual(receipts[0]["status"], "RESTORED")

    def test_failed_restore_stays_explicit(self):
        row = {
            **self.row,
            "restart_after_resume": "if_was_running",
            "restart_command": ["/usr/bin/resume"],
        }
        receipts = [
            {
                "name": row["name"],
                "status": "STOPPED",
                "was_running": True,
                "restart_after_resume": "if_was_running",
            }
        ]
        with self.assertRaises(tx.Refusal):
            workloads.restore([row], receipts, Mock(side_effect=[{}, {"active": False}]), Mock())
        self.assertEqual(receipts[0]["status"], "RESTORE_REQUESTED")

    def test_unknown_detection_is_not_treated_as_inactive(self):
        with self.assertRaises(tx.Refusal):
            workloads.quiesce([self.row], {"DATA"}, Mock(return_value={}), Mock(), [], Mock())


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = production.Host.__new__(production.Host)
        self.host.cfg = {"workloads": []}
        self.state = tx.new("boot", "prep")
        self.host.value = copy.deepcopy(self.state)

        def save(value):
            self.state = copy.deepcopy(value)

        self.r = SimpleNamespace(
            state_lock=contextlib.nullcontext,
            load_state=lambda: copy.deepcopy(self.state),
            boot_id=lambda: "boot",
            save_state=save,
            arm_automount_suppression=Mock(),
            validate_automount_marker_for_release=Mock(),
            log=Mock(),
            atomic_json=Mock(),
            live_registered_jobs=lambda: [],
            timeshift_processes=lambda: [],
            internal_data_healthy=lambda: (True, "healthy"),
            incident=Mock(),
            audit_three=lambda: {"AUDIT_COMPLETE": True, "EXTERNAL_OWNER": "NONE", "records": []},
            terminal_ugreen_reaudit=lambda v: {"ok": True},
            clear_to_idle=lambda v, end: save(
                {"state": "IDLE", "boot_id": "boot", "last_terminal": end}
            ),
            thermal_sample=lambda: {"state": "NORMAL"},
            lid_state=lambda: "open",
            unsafe_mount_topology=lambda r: [],
        )
        self.host.r = self.r
        self.host.t = SimpleNamespace(
            PENDING=self.root / "pending",
            QUEUE=self.root / "queue",
            BLOCKED=self.root / "blocked",
            LAST=self.root / "last",
            pre=Mock(),
            post=Mock(return_value=0),
            restore_transaction=Mock(),
            read_json=lambda p: {"before": {"boot_id": "boot"}},
        )
        self.host.t.core = SimpleNamespace(last_cursor=lambda: "cursor", kernel_since=lambda c: [])
        self.host.save = lambda value=None: save(self.host.value if value is None else value)
        self.host.quiesce_clones = Mock()
        self.host.device_post = Mock()
        self.host.live_owner = Mock(return_value=False)
        self.host.notify = Mock()
        self.env = patch.dict(os.environ, INVOCATION_ID="prep", SERVICE_RESULT="success")
        self.env.start()
        self.addCleanup(self.env.stop)
        self.root_patch = patch.object(production, "STATE", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def test_successful_preparation_to_real_resume(self):
        self.state = {"state": "IDLE", "boot_id": "boot"}
        self.host.begin()
        episode = self.state["episode"]
        self.host.verify_release()
        with patch.object(production, "stats", return_value={"success": 0, "total_hw_sleep": 0}):
            self.host.native_pre()
        with patch.object(
            production, "stats", return_value={"success": 1, "total_hw_sleep": 2000000}
        ):
            self.host.native_post()
        self.assertTrue(self.state["actual_sleep"])
        self.assertEqual(self.state["stage"], "RESUME_DETECTED")
        self.state["verify_invocation"] = "prep"
        self.host.resume_terminal()
        self.assertEqual(self.state["state"], "IDLE")
        self.assertEqual(self.state["last_terminal"], "COMPLETE")
        self.assertTrue(episode)

    def test_kernel_never_entered_restores_policy_without_false_resume(self):
        self.state["native_invocation"] = "prep"
        self.state["kernel_before"] = {"success": 0, "total_hw_sleep": 0}
        self.host.t.PENDING.touch()
        with patch.object(production, "stats", return_value={"success": 0, "total_hw_sleep": 0}):
            with self.assertRaises(tx.Refusal):
                self.host.native_post()
        self.host.t.restore_transaction.assert_called_once()
        self.host.t.post.assert_not_called()
        self.assertEqual(self.state["state"], "FAILURE_PENDING")
        self.assertFalse(self.state["actual_sleep"])

    def test_new_request_reconciles_old_consumed_fence(self):
        self.state.update(state="FAILURE_PENDING", retry_count=1)
        old = self.state["episode"]
        self.host.begin()
        self.assertNotEqual(self.state["episode"], old)
        self.assertEqual(self.state["retry_count"], 0)

    def test_crashed_preparation_is_reconciled_by_next_invocation(self):
        self.state.update(stage="QUIESCING", prep_invocation="dead")
        self.host.begin()
        self.assertEqual(self.state["prep_invocation"], "prep")
        self.assertEqual(self.state["retry_count"], 0)

    def test_overlap_does_not_replace_state_or_run_workloads(self):
        self.host.live_owner.return_value = True
        old = copy.deepcopy(self.state)
        with self.assertRaises(tx.Refusal):
            self.host.begin()
        self.assertEqual(self.state, old)
        self.host.quiesce_clones.assert_not_called()

    def test_unknown_cwd_fd_namespace_and_docker_blockers_are_preserved(self):
        for kind in ("cwd", "fd", "mmap", "root", "mount", "raw-fd"):
            self.r.audit_three = lambda kind=kind: {
                "AUDIT_COMPLETE": True,
                "EXTERNAL_OWNER": "HOST",
                "records": [{"kind": kind, "owner": "CONTAINER"}],
            }
            with self.assertRaises(tx.Refusal):
                self.host.verify_release()
            self.assertEqual(self.state["blockers"][0]["kind"], kind)

    def test_incomplete_or_disconnected_storage_refused(self):
        for complete, owner in ((False, "UNKNOWN"), (True, "HOST")):
            self.r.audit_three = lambda complete=complete, owner=owner: {
                "AUDIT_COMPLETE": complete,
                "EXTERNAL_OWNER": owner,
                "records": [],
            }
            with self.assertRaises(tx.Refusal):
                self.host.verify_release()
            self.assertEqual(self.state["state"], "FAILURE_PENDING")

    def test_ugreen_absent_is_safe_when_audit_complete(self):
        self.host.verify_release()
        self.assertEqual(self.state["storage_state"], "UNMOUNTED_SAFE")

    def test_storage_unmount_failure_is_owned_by_current_transaction(self):
        with patch.dict(os.environ, SERVICE_RESULT="exit-code"):
            self.host.teardown()
        self.assertEqual(self.state["state"], "FAILURE_PENDING")
        self.assertEqual(self.state["failure_origin"], "PREPARE_STORAGE")

    def test_stale_teardown_cannot_poison_new_transaction(self):
        self.state["prep_invocation"] = "fresh"
        old = copy.deepcopy(self.state)
        with patch.dict(os.environ, SERVICE_RESULT="exit-code"):
            self.host.teardown()
        self.assertEqual(self.state, old)

    def test_resume_failure_retains_recoverable_evidence(self):
        self.state["verify_invocation"] = "prep"
        with patch.dict(os.environ, SERVICE_RESULT="exit-code"):
            with self.assertRaises(tx.Refusal):
                self.host.resume_terminal()
        self.assertEqual(self.state["state"], "FAILURE_PENDING")

    def test_reenumerated_generation_audited_before_completion(self):
        self.state.update(verify_invocation="prep", kernel_cycle=True, actual_sleep=True)
        self.r.terminal_ugreen_reaudit = Mock(return_value={"ok": True, "diskseq": 42})
        self.host.resume_terminal()
        self.r.terminal_ugreen_reaudit.assert_called_once()
        self.assertEqual(self.state["state"], "IDLE")

    def test_terminal_storage_recovery_failure_not_complete(self):
        self.state["verify_invocation"] = "prep"
        self.r.terminal_ugreen_reaudit = Mock(
            return_value={"ok": False, "reason": "generation unstable"}
        )
        with self.assertRaises(tx.Refusal):
            self.host.resume_terminal()
        self.assertEqual(self.state["state"], "FAILURE_PENDING")

    def test_lid_open_clears_failed_transaction_without_suspending(self):
        self.state["state"] = "FAILURE_PENDING"
        with (
            patch.object(production, "battery", return_value={}),
            patch.object(production, "run") as run,
        ):
            self.host.monitor()
        self.assertEqual(self.state["state"], "IDLE")
        run.assert_not_called()

    def test_locklock_on_cancels_automatic_retry(self):
        self.state.update(state="RETRY_QUEUED", retry_count=1)
        self.r.lid_state = lambda: "closed"
        with (
            patch.object(production, "lid_ignored", return_value=True),
            patch.object(production, "run") as run,
        ):
            self.host.retry()
        run.assert_not_called()
        self.assertEqual(self.state["state"], "IDLE")

    def test_locklock_off_uses_normal_suspend_dispatch(self):
        self.state.update(state="RETRY_QUEUED", retry_count=1)
        self.r.lid_state = lambda: "closed"

        def accepted(*a, **k):
            self.state["prep_invocation"] = "retry-invocation"

        with (
            patch.object(production, "lid_ignored", return_value=False),
            patch.object(production, "run", side_effect=accepted) as run,
        ):
            self.host.retry()
        self.assertEqual(run.call_args.args[0], ["/usr/bin/systemctl", "--no-block", "suspend"])
        self.assertFalse(self.state["actual_sleep"])

    def test_boot_archives_old_policy_without_replaying_sysfs(self):
        self.state = {"state": "IDLE", "boot_id": "boot"}
        self.host.t.PENDING.touch()
        self.host.t.read_json = lambda p: {"before": {"boot_id": "previous"}}
        self.host.boot()
        self.host.t.restore_transaction.assert_not_called()
        self.assertFalse(self.host.t.PENDING.exists())

    def test_unknown_t700_latch_is_not_cleared_by_expiry(self):
        self.host.t.BLOCKED.touch()
        with self.assertRaises(tx.Refusal):
            self.host.begin()
        self.assertTrue(self.host.t.BLOCKED.exists())

    def test_direct_resume_without_kernel_cycle_is_refused(self):
        with self.assertRaises(tx.Refusal):
            self.host.resume_begin()

    def test_manual_reconcile_never_dispatches_sleep(self):
        self.state["state"] = "FAILURE_PENDING"
        self.host.reconcile = Mock()
        with patch.object(production, "run") as run:
            self.host.manual_reconcile()
        self.host.reconcile.assert_called_once_with()
        run.assert_not_called()


class FailureBoundaryTests(unittest.TestCase):
    setUp = HostTests.setUp

    def test_first_failure_survives_later_teardown(self):
        tx.fail(self.state, "USB_GATE", "guest has the clone open", [{"pid": 123}])
        tx.fail(self.state, "PREPARE_STORAGE", "dependency refused")
        self.assertEqual(self.state["first_failure"]["reason"], "guest has the clone open")

    def test_missing_kernel_confirmation_cannot_leave_successful_unit(self):
        self.state.update(native_invocation="prep", kernel_before={"success": 5})
        with patch.object(production, "stats", return_value={"success": 5}):
            with self.assertRaises(tx.Refusal):
                self.host.native_post()
        self.assertFalse(self.state["kernel_cycle"])

    def test_failed_ugreen_terminal_propagates_to_failure_owner(self):
        self.state["verify_invocation"] = "prep"
        self.r.terminal_ugreen_reaudit = lambda v: {"ok": False, "reason": "independent namespace"}
        with self.assertRaises(tx.Refusal):
            self.host.resume_terminal()
        self.assertEqual(self.state["reason"], "independent namespace")

    def test_failed_modem_undo_does_not_call_legacy_poweroff(self):
        self.host.abort_policy = Mock(side_effect=tx.Refusal("policy endpoint missing"))
        self.r.lid_state = lambda: "open"
        with (
            patch.object(production, "battery", return_value={}),
            patch.object(production, "run") as run,
        ):
            self.host.monitor()
        run.assert_not_called()
        self.assertEqual(self.state["state"], "FAILURE_PENDING")

    def test_t700_pre_exception_uses_only_saved_policy_undo(self):
        self.state["state"] = "SLEEP_COMMITTED"
        self.host.t.pre.side_effect = RuntimeError("precondition rejected")
        self.host.abort_policy = Mock()
        with patch.object(production, "stats", return_value={"success": 0}):
            with self.assertRaises(RuntimeError):
                self.host.native_pre()
        self.host.abort_policy.assert_called_once()
        self.host.t.post.assert_not_called()

    def test_live_recovery_prevents_a_queued_token_from_overlapping(self):
        self.state.update(state="RETRY_QUEUED", retry_count=1)
        self.host.live_owner.return_value = True
        with self.assertRaises(tx.Refusal):
            self.host.begin()
        self.assertEqual(self.state["state"], "RETRY_QUEUED")


class ClonePolicyTests(unittest.TestCase):
    setUp = HostTests.setUp

    def setup_clone(self):
        import hashlib

        command = self.root / "usbclone"
        command.write_text("test fixture; never executed")
        repository = self.root / "USB-CLONES"
        profile = repository / "profiles" / "test"
        profile.mkdir(parents=True)
        backing = profile / "disk.img"
        backing.write_bytes(b"fixture")
        self.clone = {
            "name": "usbclone_test",
            "udc": "dummy_udc.0",
            "functions": ["mass_storage.0"],
            "backings": [str(backing)],
        }
        self.host.cfg["usbclone"] = {
            "command": str(command),
            "sha256": hashlib.sha256(command.read_bytes()).hexdigest(),
            "repository": str(repository),
            "profiles": {"test": str(backing)},
        }
        self.host.clone_inventory = Mock(side_effect=[[self.clone], [self.clone], []])
        self.host.clone_audit = Mock(return_value={"AUDIT_COMPLETE": True, "records": []})
        return command

    def test_unused_known_clone_stops_only_after_audits(self):
        command = self.setup_clone()
        with patch.object(production, "trusted"), patch.object(production, "run") as run:
            production.Host.quiesce_clones(self.host)
        self.assertEqual(run.call_args_list[0].args[0], [str(command), "stop", "test"])
        self.assertEqual(self.host.clone_audit.call_count, 3)
        self.assertEqual(self.state["usbclone_stopped"][0]["status"], "STOPPED")
        self.assertTrue(self.state["usbclone_stopped"][0]["was_running"])
        self.assertEqual(
            self.state["usbclone_stopped"][0]["restart_after_resume"], "if_was_running"
        )

    def test_stopped_clone_is_restored_after_resume_or_reconciliation(self):
        command = self.setup_clone()
        receipt = {
            **self.clone,
            "status": "STOPPED",
            "was_running": True,
            "restart_after_resume": "if_was_running",
        }
        self.host.value["usbclone_stopped"] = [receipt]
        self.host.save()
        self.host.clone_inventory = Mock(side_effect=[[], [self.clone]])
        with patch.object(production, "trusted"), patch.object(production, "run") as run:
            production.Host.restore_clones(self.host)
        self.assertEqual(
            run.call_args_list[0].args[0],
            [
                "/usr/bin/env",
                f"USB_CLONE_HOME={self.host.cfg['usbclone']['repository']}",
                str(command),
                "start",
                "test",
                "dummy_udc.0",
            ],
        )
        self.assertEqual(self.state["usbclone_stopped"][0]["status"], "RESTORED")

    def test_clone_repository_falls_back_to_notification_users_home_not_root_home(self):
        command = self.setup_clone()
        repository = Path(self.host.cfg["usbclone"].pop("repository"))
        self.host.cfg["notification_user"] = "reference-user"
        self.host.cfg["notification_uid"] = repository.stat().st_uid
        receipt = {
            **self.clone,
            "status": "STOPPED",
            "was_running": True,
            "restart_after_resume": "if_was_running",
        }
        self.host.value["usbclone_stopped"] = [receipt]
        self.host.save()
        self.host.clone_inventory = Mock(side_effect=[[], [self.clone]])
        with (
            patch.object(production, "trusted"),
            patch.object(production.pwd, "getpwnam", return_value=SimpleNamespace(pw_dir=str(repository.parent))),
            patch.object(production, "run") as run,
            patch.dict(os.environ, {"USER": "root", "HOME": "/root"}, clear=False),
        ):
            production.Host.restore_clones(self.host)
        self.assertEqual(run.call_args_list[0].args[0][1], f"USB_CLONE_HOME={repository}")
        self.assertEqual(run.call_args_list[0].args[0][-1], "dummy_udc.0")

    def test_clone_restore_refuses_changed_profile_without_starting_anything(self):
        self.setup_clone()
        receipt = {
            **self.clone,
            "backings": ["/wrong/image"],
            "status": "STOPPED",
            "was_running": True,
            "restart_after_resume": "if_was_running",
        }
        self.host.value["usbclone_stopped"] = [receipt]
        self.host.save()
        with patch.object(production, "trusted"), patch.object(production, "run") as run:
            with self.assertRaises(tx.Refusal):
                production.Host.restore_clones(self.host)
        run.assert_not_called()

    def test_live_guest_fd_prohibits_usb_stop(self):
        self.setup_clone()
        self.host.clone_audit.return_value = {
            "AUDIT_COMPLETE": True,
            "records": [{"kind": "usb-or-backing-fd"}],
        }
        with patch.object(production, "trusted"), patch.object(production, "run") as run:
            with self.assertRaises(tx.Refusal):
                production.Host.quiesce_clones(self.host)
        run.assert_not_called()
        self.assertEqual(self.state["state"], "FAILURE_PENDING")

    def test_incomplete_namespace_audit_prohibits_usb_stop(self):
        self.setup_clone()
        self.host.clone_audit.return_value = {"AUDIT_COMPLETE": False, "records": []}
        with patch.object(production, "trusted"), patch.object(production, "run") as run:
            with self.assertRaises(tx.Refusal):
                production.Host.quiesce_clones(self.host)
        run.assert_not_called()

    def test_unknown_profile_never_stopped(self):
        self.setup_clone()
        self.host.cfg["usbclone"]["profiles"] = {}
        with patch.object(production, "trusted"), patch.object(production, "run") as run:
            with self.assertRaises(tx.Refusal):
                production.Host.quiesce_clones(self.host)
        run.assert_not_called()

    def test_guest_attaching_after_clean_unmount_stops_detach(self):
        self.setup_clone()
        self.host.clone_audit.side_effect = [
            {"AUDIT_COMPLETE": True, "records": []},
            {"AUDIT_COMPLETE": True, "records": [{"kind": "usb-or-backing-fd"}]},
        ]
        with patch.object(production, "trusted"), patch.object(production, "run") as run:
            with self.assertRaises(tx.Refusal):
                production.Host.quiesce_clones(self.host)
        run.assert_not_called()

    def test_clean_unmount_is_never_forced_and_failure_aborts(self):
        self.setup_clone()
        self.host.clone_audit.return_value = {
            "AUDIT_COMPLETE": True,
            "records": [{"kind": "mount", "owner": "HOST", "target": "/sample/mount"}],
        }
        with (
            patch.object(production, "trusted"),
            patch.object(production, "run", side_effect=[None, tx.Refusal("busy")]) as run,
        ):
            with self.assertRaises(tx.Refusal):
                production.Host.quiesce_clones(self.host)
        self.assertEqual(run.call_args_list[-1].args[0], ["/usr/bin/umount", "--", "/sample/mount"])
        self.assertEqual(run.call_count, 2)

    def test_raw_backing_hardlink_fd_and_mmap_are_detected(self):
        # Namespace/block auditing is independently covered by the existing
        # suite. This exercises the new backing-file identity boundary itself.
        proc = self.root / "proc"
        sysroot = self.root / "sys"
        (proc / "42/fd").mkdir(parents=True)
        (sysroot / "class/block").mkdir(parents=True)
        (sysroot / "bus/usb/devices").mkdir(parents=True)
        backing = self.root / "disk.img"
        backing.write_bytes(b"fixture")
        alias = self.root / "alias.img"
        os.link(backing, alias)
        (proc / "42/fd/7").symlink_to(alias)
        st = backing.stat()
        (proc / "42/maps").write_text(
            f"1000-2000 rw-s 00000000 {os.major(st.st_dev):x}:{os.minor(st.st_dev):x} {st.st_ino} {backing}\n"
        )
        real_path = Path

        def path(value):
            if str(value) == "/proc":
                return proc
            if str(value).startswith("/sys/"):
                return sysroot / str(value)[5:]
            return real_path(value)

        self.r.ugreen_identity = lambda: {}
        self.r.proc_stat = lambda pid: {"state": "S", "start": "100"}
        with patch.object(production, "Path", side_effect=path):
            result = self.host.clone_audit([{"backings": [str(backing)]}])
        self.assertEqual(
            {r["kind"] for r in result["records"]}, {"usb-or-backing-fd", "backing-mmap"}
        )


if __name__ == "__main__":
    unittest.main()
