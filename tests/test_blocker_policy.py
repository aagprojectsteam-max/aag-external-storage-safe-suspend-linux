from __future__ import annotations

import copy
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aag_safe_suspend import locklock_compat as ll
from aag_safe_suspend import transaction as tx
from aag_safe_suspend import workloads as w


class ClassificationTests(unittest.TestCase):
    def info(self, **changes):
        return dict(
            pid=555,
            start="42",
            ppid=1,
            state="S",
            flags=0,
            uids=[1000] * 4,
            caps=0,
            exe="/tmp/unknown-future-app",
            cmdline="unknown-future-app",
            cgroup="",
            **changes,
        )

    def test_unknown_normal_program_needs_no_application_allowlist(self):
        self.assertEqual(w.classify_process(self.info(), {1}), "ordinary-userspace")

    def test_critical_components_excluded_even_when_user_owned(self):
        for name in (
            "postgres",
            "qemu-system-x86_64",
            "dpkg",
            "mkfs.ext4",
            "mount.ntfs",
            "mysqld",
            "redis-server",
            "borg",
            "systemd",
            "gnome-shell",
            "containerd-shim-runc-v2",
        ):
            info = self.info()
            info.update(exe="/usr/bin/" + name, cmdline=name)
            self.assertNotEqual(w.classify_process(info, {1}), "ordinary-userspace", name)

    def test_pid_one_ancestors_kernel_and_uninterruptible_io_are_protected(self):
        for change in ({"pid": 1}, {"pid": 999}, {"flags": 0x200000}, {"state": "D"}):
            info = self.info()
            info.update(change)
            self.assertNotEqual(w.classify_process(info, {1, 999}), "ordinary-userspace")

    def test_privileged_services_require_supported_stop(self):
        info = self.info()
        info["uids"] = [0] * 4
        self.assertNotEqual(w.classify_process(info, {1}), "ordinary-userspace")
        self.assertEqual(w.classify_process(info, {1}, container_safe=True), "ordinary-userspace")

    def test_capabilities_are_not_treated_as_an_ordinary_user(self):
        info = self.info()
        info["caps"] = 1
        self.assertNotEqual(w.classify_process(info, {1}), "ordinary-userspace")


class TerminationTests(unittest.TestCase):
    def setUp(self):
        self.info = ClassificationTests().info()
        self.record = {"kind": "fd", "pid": 555, "start": "42", "path": "/sample/file"}
        self.holds = True
        self.receipts = []
        self.events = []
        self.snapshot = Mock(side_effect=lambda pid: copy.deepcopy(self.info))
        self.safe = Mock(return_value=False)
        self.save = Mock()
        self.now = 0
        self.patches = [
            patch.object(w.os, "pidfd_open", return_value=50),
            patch.object(w.os, "close"),
            patch.object(w.signal, "pidfd_send_signal"),
        ]
        self.open, self.close, self.send = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)

    def sleep(self, seconds):
        self.now += seconds

    def execute(self):
        return w.terminate_blocker(
            self.record,
            snapshot=self.snapshot,
            protected={1},
            still_holds=lambda pid, start: self.holds,
            safe_stop=self.safe,
            container_safe=lambda info: False,
            save=self.save,
            receipts=self.receipts,
            emit=lambda *a, **k: self.events.append((a, k)),
            clock=lambda: self.now,
            sleep=self.sleep,
            term_seconds=0.5,
        )

    def test_graceful_release_skips_forced_termination_and_records_before_signal(self):
        def release(*args):
            self.assertTrue(self.save.called)
            self.assertEqual(self.receipts[0]["status"], "SIGTERM_REQUESTED")
            self.holds = False

        self.send.side_effect = release
        self.execute()
        self.send.assert_called_once_with(50, signal.SIGTERM)
        self.assertEqual(self.receipts[0]["status"], "RELEASED")

    def test_unknown_ordinary_escalates_after_bounded_grace_and_verifies(self):
        def release(fd, sig):
            if sig == signal.SIGKILL:
                self.holds = False

        self.send.side_effect = release
        self.execute()
        self.assertEqual(
            [x.args[1] for x in self.send.call_args_list], [signal.SIGTERM, signal.SIGKILL]
        )
        self.assertGreaterEqual(self.now, 0.5)

    def test_reused_pid_receives_no_signal(self):
        self.info["start"] = "99"
        self.execute()
        self.send.assert_not_called()
        self.assertFalse(self.receipts)

    def test_released_reference_does_not_kill_living_process(self):
        self.holds = False
        self.execute()
        self.send.assert_not_called()

    def test_exec_into_critical_program_before_force_is_refused(self):
        self.send.side_effect = lambda *a: self.info.update(
            exe="/usr/bin/postgres", cmdline="postgres"
        )
        with self.assertRaises(tx.Refusal):
            self.execute()
        self.send.assert_called_once_with(50, signal.SIGTERM)

    def test_critical_supported_stop_never_sends_signals(self):
        self.info.update(exe="/usr/bin/qemu-system-x86_64", cmdline="qemu-system-x86_64")

        def shutdown(*a):
            self.holds = False
            return True

        self.safe.side_effect = shutdown
        self.execute()
        self.send.assert_not_called()
        self.safe.assert_called_once()

    def test_critical_safe_stop_timeout_never_escalates(self):
        self.info.update(exe="/usr/bin/qemu-system-x86_64", cmdline="qemu-system-x86_64")
        self.safe.return_value = True
        with self.assertRaises(tx.Refusal):
            self.execute()
        self.send.assert_not_called()
        self.assertEqual(self.receipts[0]["status"], "CRITICAL_STOP_INCOMPLETE")

    def test_missing_identity_cannot_authorize_termination(self):
        del self.record["start"]
        with self.assertRaises(tx.Refusal):
            self.execute()
        self.open.assert_not_called()

    def test_control_process_cannot_use_even_safe_stop_callback(self):
        self.record["pid"] = 1
        self.info["pid"] = 1
        with self.assertRaises(tx.Refusal):
            self.execute()
        self.safe.assert_not_called()
        self.send.assert_not_called()

    def test_failed_force_release_does_not_report_success(self):
        with self.assertRaises(tx.Refusal):
            self.execute()
        self.assertEqual(self.receipts[0]["status"], "RELEASE_FAILED")


class LiveOwnedProcessTests(unittest.TestCase):
    def test_real_unknown_blocker_term_then_force_only_owned_fixture(self):
        # The child owns only this temporary fixture. Exercise actual pidfd and
        # kernel reference release, not a mocked os.kill implementation.
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "fixture"
            file.write_text("test")
            code = "import signal,time,sys; f=open(sys.argv[1]); signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(30)"
            child = subprocess.Popen(
                [sys.executable, "-c", code, str(file)], stdout=subprocess.PIPE, text=True
            )
            try:
                self.assertEqual(child.stdout.readline().strip(), "ready")
                info = w.process_snapshot(child.pid)
                receipts = []

                def holds(pid, start):
                    item = w.process_snapshot(pid)
                    return item is not None and item["start"] == start

                w.terminate_blocker(
                    {"kind": "fd", "pid": child.pid, "start": info["start"], "path": str(file)},
                    protected={1, os.getpid()},
                    still_holds=holds,
                    safe_stop=lambda *a: False,
                    container_safe=lambda i: False,
                    save=lambda: None,
                    receipts=receipts,
                    emit=lambda *a, **k: None,
                    term_seconds=0.05,
                )
                self.assertEqual(child.wait(timeout=3), -signal.SIGKILL)
                self.assertEqual(receipts[0]["status"], "RELEASED")
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
                child.stdout.close()


class LockLockContractTests(unittest.TestCase):
    def test_legacy_or_missing_required_transaction_stage_is_not_ready(self):
        run = Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, "LoadState=loaded\naag-sleep-recovery failure-recover", ""
            )
        )
        self.assertFalse(ll.stack_ready(run))

    def test_resume_requires_this_boot_actual_sleep_and_fresh_transaction(self):
        d = {
            "boot_id": "boot",
            "state": "COMPLETE",
            "actual_sleep": True,
            "kernel_cycle": True,
            "started_monotonic": 20,
        }

        def read(p):
            return "boot" if str(p).endswith("boot_id") else json.dumps(d)

        with patch.object(Path, "read_text", read):
            self.assertTrue(ll.resume_confirmed(19))
            self.assertFalse(ll.resume_confirmed(21))
            d["actual_sleep"] = False
            self.assertFalse(ll.resume_confirmed(19))
            d["actual_sleep"] = True
            d["state"] = "RECOVERED"
            self.assertFalse(ll.resume_confirmed(19))


class LegacyLabelTests(unittest.TestCase):
    def test_invalid_volume_label_bytes_preserved_without_global_subprocess_change(self):
        from aag_safe_suspend import production

        original = production.subprocess
        with patch.object(production.subprocess, "run") as run:
            production.RecoveryCommands().run(
                ["/usr/bin/lsblk", "-J"], text=True, capture_output=True
            )
            self.assertEqual(run.call_args.kwargs["errors"], "surrogateescape")
            production.RecoveryCommands().run(["/usr/bin/systemctl", "status"], text=True)
            self.assertNotIn("errors", run.call_args.kwargs)
        self.assertIs(production.subprocess, original)
        raw = b'{"uuid":"expected-exact-uuid","label":"legacy-\x8e"}'
        data = json.loads(raw.decode("utf-8", "surrogateescape"))
        self.assertEqual(data["uuid"], "expected-exact-uuid")
        self.assertEqual(data["label"].encode("utf-8", "surrogateescape"), b"legacy-\x8e")


class StableAuditTests(unittest.TestCase):
    def test_changed_namespace_is_fully_reaudited_before_success(self):
        from aag_safe_suspend import production

        host = production.Host.__new__(production.Host)
        host.r = Mock()
        host.r.audit_three.side_effect = [
            {"AUDIT_COMPLETE": False, "gaps": [{"probe": "semantic-stability"}]},
            {"AUDIT_COMPLETE": True, "records": []},
        ]
        with patch.object(production.time, "sleep"):
            self.assertTrue(host.audit_storage()["AUDIT_COMPLETE"])
        self.assertEqual(host.r.audit_three.call_count, 2)

    def test_permission_gap_is_never_promoted_to_complete(self):
        from aag_safe_suspend import production

        host = production.Host.__new__(production.Host)
        host.r = Mock()
        host.r.audit_three.return_value = {
            "AUDIT_COMPLETE": False,
            "gaps": [{"probe": "permission"}],
        }
        self.assertFalse(host.audit_storage()["AUDIT_COMPLETE"])
        self.assertEqual(host.r.audit_three.call_count, 1)


class ResumeHealthTests(unittest.TestCase):
    def result(self):
        return {
            "checks": {k: True for k in tx.HEALTH_CHECKS},
            "outcome": "NOT_ACCEPTED_REVIEW_REQUIRED",
        }

    def test_short_sleep_or_closed_lid_is_not_a_device_health_failure(self):
        result = self.result()
        result["checks"].update(
            sustained_hardware_sleep=False,
            residency_matches_cycle=False,
            lid_open_after_cycle=False,
        )
        self.assertTrue(tx.device_health(result))

    def test_pmc_counter_disagreement_is_qualification_not_device_health(self):
        result = self.result()
        result["checks"]["pmc_agrees_with_hardware_counter"] = False
        self.assertTrue(tx.device_health(result))

    def test_every_actual_health_failure_remains_a_failure(self):
        for key in tx.HEALTH_CHECKS:
            result = self.result()
            result["checks"][key] = False
            self.assertFalse(tx.device_health(result), key)

    def test_missing_or_new_failed_check_cannot_be_hidden(self):
        self.assertFalse(tx.device_health({"checks": {}}))
        result = self.result()
        result["checks"]["new_integrity_check"] = False
        self.assertFalse(tx.device_health(result))


class DeviceAdapterTests(unittest.TestCase):
    def setUp(self):
        from test_transaction import HostTests

        HostTests.setUp(self)
        self.out = self.root / "cycle-fixture"
        self.out.mkdir()
        self.device_tx = {
            "directory": str(self.out),
            "invocation": "native",
            "cursor": "cursor",
            "before": {"boot_id": "boot", "policy": {}, "data": {"healthy": True}},
            "cellular_before": {"healthy": True, "connection_uuid": "connection"},
        }
        self.job = {
            "transaction": self.device_tx,
            "after": {},
            "invocation": "native",
            "service_result": "success",
        }
        t = self.host.t
        t.STATE = self.root
        t.stale_pending = lambda: self.device_tx
        t.PENDING.touch()
        t.QUEUE.touch()
        self.state["native_invocation"] = "native"
        self.host.value = copy.deepcopy(self.state)
        t.read_json = lambda p: self.job
        t.write_json = Mock()
        t.verify_files = Mock()
        t.config = lambda: {}
        t.policy = lambda: {}
        t.core.snapshot = lambda: {}
        t.core.cellular = lambda: {"healthy": True, "connection_uuid": "connection"}
        t.core.data_state = lambda: {"healthy": True}
        t.core.BASE_SRC = "baseline"
        t.core.loaded = lambda: "baseline"
        t.publish_result = Mock()
        t.block = Mock()
        self.result = ResumeHealthTests().result()
        self.result["checks"]["sustained_hardware_sleep"] = False
        t.assess = lambda *a: copy.deepcopy(self.result)
        self.run_patch = patch(
            "aag_safe_suspend.production.run",
            return_value=subprocess.CompletedProcess([], 0, "b false\n", ""),
        )
        self.run_patch.start()
        self.addCleanup(self.run_patch.stop)

    def test_device_policy_restore_and_queue_even_when_lid_still_closed(self):
        from aag_safe_suspend import production

        self.r.lid_state = lambda: "closed"
        production.Host.device_post(self.host)
        self.host.t.restore_transaction.assert_called_once_with(self.device_tx)
        self.assertFalse(self.host.t.PENDING.exists())
        self.assertTrue((self.out / "transaction-restored.json").exists())
        self.host.t.post.assert_not_called()

    def test_capture_failure_still_restores_policy_and_keeps_undo_evidence(self):
        from aag_safe_suspend import production

        self.host.t.core.snapshot = Mock(side_effect=RuntimeError("capture failed"))
        with self.assertRaises(RuntimeError):
            production.Host.device_post(self.host)
        self.host.t.restore_transaction.assert_called_once()
        self.assertTrue(self.host.t.PENDING.exists())

    def test_short_real_cycle_completes_health_verification_without_latch(self):
        self.host.verify_device()
        self.assertTrue(self.state["device_health"])
        self.assertEqual(self.state["device_result"]["outcome"], "HEALTHY_RESUME")
        self.assertFalse(self.state["device_result"]["checks"]["sustained_hardware_sleep"])
        self.host.t.block.assert_not_called()
        self.assertFalse(self.host.t.QUEUE.exists())

    def test_pmc_mismatch_preserves_qualification_failure_without_health_latch(self):
        self.result["checks"]["pmc_agrees_with_hardware_counter"] = False
        self.host.verify_device()
        self.assertTrue(self.state["device_health"])
        self.assertEqual(self.state["device_result"]["outcome"], "HEALTHY_RESUME")
        self.assertEqual(
            self.state["device_result"]["qualification_outcome"],
            "NOT_ACCEPTED_REVIEW_REQUIRED",
        )
        self.host.t.block.assert_not_called()

    def test_data_health_failure_remains_a_real_failure(self):
        self.host.t.core.data_state = lambda: {"healthy": False}
        with self.assertRaises(tx.Refusal):
            self.host.verify_device()
        self.assertFalse(self.state["device_health"])
        self.host.t.block.assert_called_once()

    def test_replaced_invocation_does_not_verify_another_transaction(self):
        self.job["invocation"] = "someone-else"
        with self.assertRaises(tx.Refusal):
            self.host.verify_device()
        self.host.t.publish_result.assert_not_called()

    def test_healthy_closed_lid_wake_enters_single_owned_retry_path(self):
        from aag_safe_suspend import production

        self.state.update(verify_invocation="prep", actual_sleep=True, kernel_cycle=True)
        self.r.lid_state = lambda: "closed"
        with patch.object(production, "lid_ignored", return_value=False):
            with self.assertRaises(tx.Refusal):
                self.host.resume_terminal()
        self.assertEqual(self.state["stage"], "WAKE_WHILE_LID_CLOSED")
        self.assertEqual(self.state["retry_count"], 0)


class HistoricalQualificationLatchTests(unittest.TestCase):
    def test_old_device_health_wording_can_clear_only_when_new_health_is_clean(self):
        from test_transaction import HostTests

        fixture = HostTests(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        host, state, root = fixture.host, fixture.state, fixture.root
        out = root / "cycle-historical"
        out.mkdir()
        host.t.STATE = root
        host.t.BLOCKED.touch()
        state["native_invocation"] = "native"
        host.value = copy.deepcopy(state)
        clean = {"checks": {k: True for k in tx.HEALTH_CHECKS}}
        clean["checks"]["pmc_agrees_with_hardware_counter"] = False
        result = {
            **clean,
            "directory": str(out),
            "boot_id": "boot",
        }
        latch = {
            "reason": "Device health verification failed",
            "boot_id": "boot",
            "extra": str(out),
        }
        restored = {
            "invocation": "native",
            "before": {"policy": {}, "data": {"healthy": True}},
            "cellular_before": {"healthy": True, "connection_uuid": "connection"},
        }

        def read_json(path):
            if path == host.t.BLOCKED:
                return latch
            if path == host.t.LAST:
                return result
            if path == out / "transaction-restored.json":
                return restored
            raise AssertionError(path)

        host.t.read_json = read_json
        host.t.check_host = Mock()
        host.t.config = lambda: {}
        host.t.policy = lambda: {}
        host.t.core = SimpleNamespace(
            data_state=lambda: {"healthy": True},
            cellular=lambda: {"healthy": True, "connection_uuid": "connection"},
        )
        host.reconcile_qualification_latch()
        self.assertFalse(host.t.BLOCKED.exists())


class QueueRecoveryTests(unittest.TestCase):
    def setUp(self):
        from test_transaction import HostTests

        HostTests.setUp(self)

    def test_crashed_verifier_queue_uses_full_verification_then_reconciles(self):
        self.host.t.QUEUE.touch()
        self.state.update(state="FAILURE_PENDING", kernel_cycle=True)
        self.host.value = copy.deepcopy(self.state)
        self.host.verify_device = Mock(side_effect=self.host.t.QUEUE.unlink)
        self.host.reconcile()
        self.host.verify_device.assert_called_once()
        self.assertEqual(self.state["state"], "IDLE")

    def test_failed_queue_health_cannot_clear_transaction(self):
        self.host.t.QUEUE.touch()
        self.host.verify_device = Mock(side_effect=tx.Refusal("device recovery unresolved"))
        with self.assertRaises(tx.Refusal):
            self.host.reconcile()
        self.assertNotEqual(self.state["state"], "IDLE")

    def test_device_post_exception_preserves_the_actual_first_error(self):
        from aag_safe_suspend import production

        self.state.update(
            native_invocation="prep", kernel_before={"success": 0, "total_hw_sleep": 0}
        )
        self.host.device_post = Mock(side_effect=RuntimeError("policy endpoint disappeared"))
        with patch.object(
            production, "stats", return_value={"success": 1, "total_hw_sleep": 10000}
        ):
            with self.assertRaises(RuntimeError):
                self.host.native_post()
        self.assertEqual(self.state["first_failure"]["reason"], "policy endpoint disappeared")
        self.assertEqual(self.state["stage"], "DEVICE_POST")
