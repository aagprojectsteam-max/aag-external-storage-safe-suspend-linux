from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_release  # noqa: E402
import transaction_package as package  # noqa: E402


class TransactionPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.root = base / "root"
        self.safety = self.root / "usr/lib/input-lock/input_lock_safety.py"
        self.safety.parent.mkdir(parents=True)
        self.original = (
            "import subprocess\nfrom pathlib import Path\n"
            "def stack_ready(run=subprocess.run) -> bool:\n    return False\n"
            "def aag_resume_confirmed(requested_at: float) -> bool:\n    return False\n"
        )
        self.safety.write_text(self.original)
        self.safety.chmod(0o640)
        self.config = base / "config.json"
        self.config.write_text('{"workloads": []}\n')
        self.args = argparse.Namespace(
            root=self.root,
            test_mode=True,
            report=base / "report",
            config=self.config,
            check=False,
            rollback=None,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_isolated_install_exact_payload_and_rollback(self):
        with patch.object(
            package.deploy.production, "Host", side_effect=AssertionError("host called")
        ):
            result = package.isolated(self.args)
            self.assertEqual(result["files"], 15)
            for source, logical in package.deploy.mapping(self.config).items():
                installed = self.root / logical.relative_to("/")
                self.assertEqual(installed.read_bytes(), source.read_bytes())
                expected = (
                    0o600
                    if logical == package.deploy.production.CONFIG
                    else 0o755
                    if logical.name == "aag-sleep-transaction"
                    else 0o644
                )
                self.assertEqual(installed.stat().st_mode & 0o777, expected)
            self.assertIn("return adapter_contract(run)", self.safety.read_text())
            self.args.rollback = Path(result["rollback"])
            self.assertEqual(package.isolated(self.args)["status"], "ROLLBACK_COMPLETE")
            self.assertEqual(self.safety.read_text(), self.original)
            self.assertEqual(self.safety.stat().st_mode & 0o777, 0o640)
            self.assertFalse((self.root / "usr/local/libexec/aag-sleep-transaction").exists())
            self.assertFalse((self.root / package.LINK.relative_to("/")).is_symlink())

    def test_upgrade_then_rollback_restores_exact_reference_files(self):
        initial = package.isolated(self.args)
        executable = self.root / "usr/local/libexec/aag-sleep-transaction"
        executable.write_text("prior qualified fixture\n")
        executable.chmod(0o750)
        upgraded = package.isolated(self.args)
        self.assertNotEqual(executable.read_text(), "prior qualified fixture\n")
        self.args.rollback = Path(upgraded["rollback"])
        package.isolated(self.args)
        self.assertEqual(executable.read_text(), "prior qualified fixture\n")
        self.assertEqual(executable.stat().st_mode & 0o777, 0o750)
        self.assertTrue((self.root / package.LINK.relative_to("/")).is_symlink())
        self.args.rollback = Path(initial["rollback"])
        package.isolated(self.args)

    def test_corrupt_rollback_refuses_before_changing_installation(self):
        installed = package.isolated(self.args)
        self.args.rollback = Path(installed["rollback"])
        (self.args.rollback / "usr/lib/input-lock/input_lock_safety.py").write_text("damaged\n")
        current = self.safety.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            package.isolated(self.args)
        self.assertEqual(self.safety.read_bytes(), current)

    def test_isolated_mode_cannot_target_host_or_follow_symlink(self):
        self.args.root = Path("/")
        with self.assertRaisesRegex(RuntimeError, "non-root"):
            package.isolated(self.args)
        self.args.root = self.root
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (self.root / "usr/local").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            package.isolated(self.args)
        self.assertEqual(list(outside.iterdir()), [])

    def test_private_reports_are_not_packaged(self):
        tree = Path(self.temporary.name) / "public"
        (tree / "reports/raw").mkdir(parents=True)
        (tree / "reports/raw/journal.txt").write_text("PRIVATE_FIXTURE\n")
        (tree / "README.md").write_text("Public fixture\n")
        with patch.object(build_release, "ROOT", tree):
            self.assertEqual(build_release.public_files(), [Path("README.md")])

    def test_changed_qualified_runtime_fails_release_validation(self):
        tree = Path(self.temporary.name) / "candidate"
        (tree / "docs").mkdir(parents=True)
        (tree / "runtime.py").write_text("changed\n")
        (tree / "docs/transaction-runtime-sha256.json").write_text(
            json.dumps({"sha256": {"runtime.py": hashlib.sha256(b"accepted\n").hexdigest()}})
        )
        with (
            patch.object(build_release, "ROOT", tree),
            self.assertRaisesRegex(RuntimeError, "qualified runtime bytes changed"),
        ):
            build_release.validate_only()


if __name__ == "__main__":
    unittest.main()
