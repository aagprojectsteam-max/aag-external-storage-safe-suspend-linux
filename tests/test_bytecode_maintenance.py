from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aag_safe_suspend import maintenance


class BytecodeMaintenanceTests(unittest.TestCase):
    def test_replacement_invalidates_same_size_same_timestamp_bytecode(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, {"AAG_SAFE_SUSPEND_TEST_MODE": "1"}),
        ):
            root = Path(temporary)
            module = root / "version_fixture.py"
            module.write_text('value = "old"\n')
            stamp = module.stat().st_mtime
            command = [sys.executable, "-c", "import version_fixture; print(version_fixture.value)"]
            env = dict(os.environ)
            env.pop("PYTHONDONTWRITEBYTECODE", None)
            self.assertEqual(
                subprocess.check_output(command, cwd=root, env=env, text=True).strip(), "old"
            )
            cache = Path(importlib.util.cache_from_source(str(module)))
            self.assertTrue(cache.is_file())
            replacement = root / "replacement.py"
            replacement.write_text('value = "new"\n')
            maintenance._atomic_copy(replacement, module, 0o644)
            os.utime(module, (stamp, stamp))
            self.assertFalse(cache.exists())
            self.assertEqual(
                subprocess.check_output(command, cwd=root, env=env, text=True).strip(), "new"
            )

    def test_cache_symlink_is_refused_before_source_replacement(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, {"AAG_SAFE_SUSPEND_TEST_MODE": "1"}),
        ):
            root = Path(temporary)
            module = root / "managed.py"
            module.write_text("original\n")
            replacement = root / "replacement.py"
            replacement.write_text("new\n")
            outside = root / "unrelated"
            outside.mkdir()
            marker = outside / "managed.cpython-fixture.pyc"
            marker.write_text("preserve\n")
            (root / "__pycache__").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(maintenance.MaintenanceError, "bytecode directory"):
                maintenance._atomic_copy(replacement, module, 0o644)
            self.assertEqual(module.read_text(), "original\n")
            self.assertEqual(marker.read_text(), "preserve\n")


if __name__ == "__main__":
    unittest.main()
