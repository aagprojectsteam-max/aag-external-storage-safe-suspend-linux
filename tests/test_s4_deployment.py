from __future__ import annotations

import fcntl
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import deploy_hibernate as deploy  # noqa: E402

from aag_safe_suspend import locklock_compat, power_dispatch, production, s4_host  # noqa: E402
from aag_safe_suspend.transaction import Refusal  # noqa: E402


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "root"
        self.root.mkdir()
        self.config = self.base / "config.json"
        self.gates = {key: "PASS" for key in deploy.REQUIRED_GATES}
        self.gates["ROLLBACK_READY"] = "YES"
        cfg = {
            "simulated_gates": {key: "PASS" for key in deploy.REQUIRED_GATES},
            "pinned_files": {
                str(dst): deploy.digest(src)
                for src, dst in deploy.mapping(self.config)
                if src != self.config
            },
        }
        self.config.write_text(json.dumps(cfg))
        prior = self.root / "usr/local/libexec/aag-sleep-transaction"
        prior.parent.mkdir(parents=True)
        prior.write_text("accepted v1.3.1 fixture\n")
        self.baseline = [
            {
                "installed": "/usr/local/libexec/aag-sleep-transaction",
                "expected": deploy.digest(prior),
            }
        ]
        self.old = self.root / "etc/systemd/system/aag-hibernate-wwan-recovery.service"
        self.old.parent.mkdir(parents=True)
        self.old.write_text("legacy exact rollback\n")
        self.old.chmod(0o640)

    def install(self):
        return deploy.install(
            self.root, self.config, self.base / "report", self.gates, self.baseline
        )

    def test_installation_preserves_baseline_and_rollback_exact_bytes_modes(self):
        backup = self.install()
        for source, target in deploy.mapping(self.config):
            self.assertEqual(
                (self.root / target.relative_to("/")).read_bytes(), source.read_bytes()
            )
        deploy.rollback(self.root, backup)
        self.assertEqual(self.old.read_text(), "legacy exact rollback\n")
        self.assertEqual(self.old.stat().st_mode & 0o777, 0o640)
        self.assertFalse((self.root / "usr/local/libexec/aag-power-transaction").exists())
        self.assertEqual(
            (self.root / "usr/local/libexec/aag-sleep-transaction").read_text(),
            "accepted v1.3.1 fixture\n",
        )

    def test_upgrade_rollback_preserves_prior_candidate(self):
        self.install()
        backup = self.install()
        deploy.rollback(self.root, backup)
        self.assertEqual(
            (self.root / "usr/local/libexec/aag-power-transaction").read_bytes(),
            (ROOT / "src/aag-power-transaction").read_bytes(),
        )

    def test_any_missing_or_failed_static_gate_blocks_all_mutations(self):
        for key in deploy.REQUIRED_GATES:
            original = self.gates[key]
            self.gates[key] = "FAIL"
            with self.assertRaisesRegex(RuntimeError, "gates"):
                self.install()
            self.gates[key] = original
        self.assertEqual(self.old.read_text(), "legacy exact rollback\n")

    def test_changed_baseline_or_payload_refuses_before_mutations(self):
        self.baseline[0]["expected"] = "wrong"
        with self.assertRaisesRegex(RuntimeError, "baseline"):
            self.install()
        self.baseline = []
        cfg = json.loads(self.config.read_text())
        cfg["pinned_files"]["/usr/local/libexec/aag-power-transaction"] = "wrong"
        self.config.write_text(json.dumps(cfg))
        with self.assertRaisesRegex(RuntimeError, "pinned"):
            self.install()
        self.assertEqual(self.old.read_text(), "legacy exact rollback\n")

    def test_corrupt_rollback_is_rejected_before_restoration(self):
        backup = self.install()
        (backup / self.old.relative_to(self.root)).write_text("corrupted")
        with self.assertRaisesRegex(RuntimeError, "corrupted"):
            deploy.rollback(self.root, backup)
        self.assertEqual(
            self.old.read_bytes(), (ROOT / "systemd/hibernate/retired.service").read_bytes()
        )

    def test_destination_symlink_is_refused(self):
        (self.root / "usr/local/lib").symlink_to(self.base)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            self.install()


class BoundaryTests(unittest.TestCase):
    def test_dispatch_releases_operation_lock_before_systemd_can_start_V2(self):
        self.dispatch_lock_fixture()

    def test_dispatch_refusal_invalidates_unused_permit(self):
        self.dispatch_lock_fixture(refuse=True)

    def dispatch_lock_fixture(self, refuse=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / "operation.lock"
            permit = root / "permit.json"
            cfg = root / "config.json"
            cfg.write_text("{}")
            host = Mock()
            host.r.boot_id.return_value = "boot"
            host.r.lid_state.return_value = "open"
            host.r.thermal_sample.return_value = {"state": "NORMAL"}
            host.snapshot.return_value = {}

            def persist(path, value):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value))

            host.persist.side_effect = persist
            observed = []

            def run(argv, **kwargs):
                if argv[-1] == "hibernate":
                    with lock.open("r+") as handle:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    observed.append("operation lock released before native dispatch")
                    if refuse:
                        raise Refusal("logind refused")
                return SimpleNamespace(returncode=0, stdout="")

            with (
                patch.object(power_dispatch.os, "geteuid", return_value=0),
                patch.object(power_dispatch, "current", return_value={"state": "IDLE"}),
                patch.object(power_dispatch, "hibernate_job", return_value=False),
                patch.object(power_dispatch, "LOCK", lock),
                patch.object(power_dispatch, "PERMIT", permit),
                patch.object(production, "STATE", root),
                patch.object(power_dispatch.checks, "CONFIG", cfg),
                patch.object(power_dispatch, "Host", return_value=host),
                patch.object(power_dispatch.checks, "readiness", return_value={"result": "PASS"}),
                patch.object(production, "properties", return_value={"Job": "0"}),
                patch.object(production, "battery", return_value={"critical": False}),
                patch.object(production, "run", side_effect=run),
            ):
                if refuse:
                    with self.assertRaisesRegex(Refusal, "logind refused"):
                        power_dispatch.main(["hibernate-once"])
                    self.assertEqual(json.loads(permit.read_text())["go"], "DISPATCH_FAILED")
                else:
                    power_dispatch.main(["hibernate-once"])
                    with self.assertRaisesRegex(Refusal, "already reserved"):
                        power_dispatch.main(["hibernate-once"])
            self.assertEqual(len(observed), 1)

    def test_failure_dispatch_coalesces_without_contending_with_active_owner(self):
        with (
            patch.object(power_dispatch.os, "geteuid", return_value=0),
            patch.object(
                power_dispatch,
                "current",
                return_value={"transaction_type": "HIBERNATE", "state": "FAILURE_PENDING"},
            ),
            patch.object(
                power_dispatch.os,
                "open",
                side_effect=AssertionError("must not acquire active recovery lock"),
            ),
            patch.object(production, "run") as run,
        ):
            self.assertEqual(power_dispatch.main(["failure-recover"]), 0)
            run.assert_called_once_with(
                ["/usr/bin/systemctl", "--no-block", "start", s4_host.FINISH], timeout=5
            )

    def test_retired_controller_preserves_ordinary_failsafe_but_cannot_dispatch_power(self):
        shim = ROOT / "systemd/hibernate/retired-qualifier"
        for command, expected in [
            ("allow-suspend-failsafe", 0),
            ("arm-and-hibernate", 1),
            ("systemd-pre", 1),
        ]:
            result = subprocess.run(
                [sys.executable, str(shim), command], capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, expected)

    def test_original_locklock_contract_accepts_explicit_delegate(self):
        fixture = {
            "systemd-suspend.service": "aag-storage-sleep-v2.service aag-suspend-failure-failsafe.service /usr/lib/systemd/systemd-sleep suspend "
            + (ROOT / "systemd/production/native.conf").read_text(),
            "aag-storage-sleep-v2.service": "aag-ugreen-sleep-guard-v2 pre "
            + (ROOT / "systemd/hibernate/zz-storage.conf").read_text(),
            "aag-suspend-failure-failsafe.service": (
                ROOT / "systemd/hibernate/zz-failure.conf"
            ).read_text(),
            "aag-t700-resume-check.service": (ROOT / "systemd/production/resume.conf").read_text(),
            "input-lock-protect.service": "input_lock_safety.py",
        }

        def run(argv, **kwargs):
            return SimpleNamespace(returncode=0, stdout="LoadState=loaded\n" + fixture[argv[2]])

        self.assertTrue(locklock_compat.stack_ready(run))

    def test_ordinary_dispatch_executes_exact_accepted_argv_without_host(self):
        with (
            patch.object(power_dispatch.os, "geteuid", return_value=0),
            patch.object(power_dispatch, "current", return_value={"state": "IDLE"}),
            patch.object(power_dispatch, "hibernate_job", return_value=False),
            patch.object(power_dispatch, "Host") as host,
            patch.object(power_dispatch.os, "execv", side_effect=SystemExit) as execute,
        ):
            with self.assertRaises(SystemExit):
                power_dispatch.main([production.HELPER, "begin"])
            execute.assert_called_once_with(production.HELPER, [production.HELPER, "begin"])
            host.assert_not_called()

    def test_no_competing_legacy_hook_or_helper_command_remains_in_payload(self):
        hook = (ROOT / "systemd/hibernate/99-aag-wwan-hibernate").read_text()
        self.assertNotIn("systemd-run", hook)
        self.assertNotIn("restart", hook)
        self.assertIn(
            "ExecStart=/usr/bin/false", (ROOT / "systemd/hibernate/retired.service").read_text()
        )
        native = (ROOT / "systemd/hibernate/zz-native.conf").read_text()
        self.assertIn("OnSuccess=\nOnSuccess=" + s4_host.FINISH, native)
        self.assertIn("OnFailure=\nOnFailure=" + s4_host.FINISH, native)
        self.assertNotIn("aag-hibernate-qualifier", native)

    def test_live_owner_graph_rejects_a_second_legacy_recovery_owner(self):
        host = s4_host.Host.__new__(s4_host.Host)
        with tempfile.TemporaryDirectory() as directory:
            host.t = SimpleNamespace(
                **{name: Path(directory) / name for name in ("PENDING", "QUEUE", "BLOCKED")}
            )

            def run(argv, **kwargs):
                if argv[2] == s4_host.NATIVE:
                    return SimpleNamespace(
                        stdout="OnSuccess="
                        + s4_host.FINISH
                        + "\nOnFailure="
                        + s4_host.FINISH
                        + "\nExecStartPre=hibernate-native-pre\nExecStopPost=hibernate-native-post\n"
                    )
                return SimpleNamespace(
                    stdout="ExecStart=/usr/bin/false\nActiveState=inactive\nJob=0\n"
                )

            with (
                patch.object(production, "run", side_effect=run),
                patch.object(
                    production, "properties", return_value={"ActiveState": "inactive", "Job": "0"}
                ),
            ):
                self.assertEqual(host.owner_graph()["result"], "PASS")

            def competing(argv, **kwargs):
                if argv[2] == "aag-hibernate-wwan-recovery.service":
                    return SimpleNamespace(
                        stdout="ExecStart=/old-recover\nActiveState=inactive\nJob=0\n"
                    )
                return run(argv, **kwargs)

            with (
                patch.object(production, "run", side_effect=competing),
                patch.object(
                    production, "properties", return_value={"ActiveState": "inactive", "Job": "0"}
                ),
            ):
                self.assertEqual(host.owner_graph()["result"], "FAIL")

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze unavailable")
    def test_merged_reference_S4_unit_graph_has_no_ordering_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            units = root / "etc/systemd/system"
            units.mkdir(parents=True)
            fixtures = {
                "systemd-hibernate.service": "[Unit]\nDefaultDependencies=no\nRequires=sleep.target aag-storage-sleep-v2.service\nAfter=sleep.target aag-storage-sleep-v2.service\n[Service]\nType=oneshot\nExecStart=/usr/lib/systemd/systemd-sleep hibernate\nExecStartPre=/usr/local/libexec/aag-hibernate-qualifier systemd-pre\n",
                "aag-storage-sleep-v2.service": "[Unit]\nDefaultDependencies=no\nBefore=sleep.target\nAfter=local-fs.target\nOnFailure=aag-suspend-failure-failsafe.service\nStopWhenUnneeded=yes\n[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/usr/local/libexec/aag-ugreen-sleep-guard-v2 pre\n",
                "aag-suspend-failure-failsafe.service": "[Unit]\nDefaultDependencies=no\nAfter=aag-storage-sleep-v2.service systemd-suspend.service aag-t700-resume-check.service\n[Service]\nType=oneshot\nExecStart=/usr/bin/false\n",
                "systemd-suspend.service": "[Unit]\nDefaultDependencies=no\nRequires=sleep.target\nAfter=sleep.target\n[Service]\nType=oneshot\nExecStart=/usr/lib/systemd/systemd-sleep suspend\n",
                "aag-sleep-transaction-boot.service": (
                    ROOT / "systemd/production/aag-sleep-transaction-boot.service"
                ).read_text(),
            }
            for name, text in fixtures.items():
                (units / name).write_text(text)
            for kind, unit in {
                "storage": "aag-storage-sleep-v2",
                "failure": "aag-suspend-failure-failsafe",
                "boot": "aag-sleep-transaction-boot",
                "native": "systemd-hibernate",
            }.items():
                drop = units / (unit + ".service.d")
                drop.mkdir()
                if kind in {"storage", "failure"}:
                    shutil.copy2(
                        ROOT / "systemd/production" / (kind + ".conf"),
                        drop / "99-aag-transaction.conf",
                    )
                shutil.copy2(
                    ROOT / "systemd/hibernate" / ("zz-" + kind + ".conf"),
                    drop / "zz-aag-hibernate-transaction.conf",
                )
            shutil.copy2(
                ROOT / "systemd/hibernate/80-aag-hibernate-qualification.conf",
                units / "systemd-hibernate.service.d/80-aag-hibernate-qualification.conf",
            )
            shutil.copy2(ROOT / "systemd/hibernate/aag-hibernate-transaction-finish.service", units)
            for suffix in deploy.LEGACY:
                shutil.copy2(
                    ROOT / "systemd/hibernate/retired.service",
                    units / ("aag-hibernate-" + suffix + ".service"),
                )
            for name in (
                "NetworkManager.service",
                "ModemManager.service",
                "aag-t700-resume-check.service",
            ):
                (units / name).write_text(
                    "[Unit]\nDescription=Fixture\n[Service]\nType=oneshot\nExecStart=/usr/bin/false\n"
                )
            for name in (
                "sysinit.target",
                "basic.target",
                "shutdown.target",
                "sleep.target",
                "local-fs.target",
            ):
                (units / name).write_text("[Unit]\nDescription=Fixture\nDefaultDependencies=no\n")
            (units / "systemd-journald.socket").write_text(
                "[Unit]\nDescription=Fixture\nDefaultDependencies=no\n[Socket]\nListenStream=/run/s4-fixture.socket\n"
            )
            for file in (
                "usr/lib/systemd/systemd-sleep",
                "usr/bin/false",
                "usr/bin/systemd-inhibit",
                "usr/local/libexec/aag-power-transaction",
                "usr/local/libexec/aag-sleep-transaction",
                "usr/local/libexec/aag-ugreen-sleep-guard-v2",
            ):
                target = root / file
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("#!/bin/sh\nexit 0\n")
                target.chmod(0o755)
            result = subprocess.run(
                [
                    "systemd-analyze",
                    "--root=" + str(root),
                    "verify",
                    "systemd-hibernate.service",
                    "aag-storage-sleep-v2.service",
                    "aag-hibernate-transaction-finish.service",
                    "aag-sleep-transaction-boot.service",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
