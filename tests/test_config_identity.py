from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aag_safe_suspend import config, identity

HERE = Path(__file__).parent


class ConfigIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = config.load(HERE / "fixtures/config.json")
        self.lsblk = (HERE / "fixtures/lsblk-generation-a.json").read_text()

    def test_configuration_is_valid(self) -> None:
        self.assertEqual(self.config["device"]["serial"], "TEST-NVME-0001")
        self.assertEqual(self.config["thermal"]["emergency_action"], "none")

    def test_protected_mount_cannot_be_target_uuid(self) -> None:
        value = json.loads((HERE / "fixtures/config.json").read_text())
        value["protected_mounts"][0]["uuid"] = "TEST-FS-A"
        with self.assertRaises(config.ConfigError):
            config.validate(value)

    def test_generation_uses_stable_identity_not_devname(self) -> None:
        with patch.dict(os.environ, {"AAG_SAFE_SUSPEND_TEST_MODE": "1"}):
            result = identity.identify(self.config, self.lsblk)
        self.assertTrue(result["complete"])
        self.assertEqual(result["token"]["serial"], "TEST-NVME-0001")
        self.assertEqual(result["token"]["diskseq"], "11")
        self.assertEqual(
            {row["uuid"] for row in result["token"]["partitions"]}, {"TEST-FS-A", "TEST-FS-B"}
        )

    def test_different_sd_name_same_identity(self) -> None:
        renamed = (
            self.lsblk.replace("sdz", "sdc")
            .replace("65:144", "8:32")
            .replace("65:145", "8:33")
            .replace("65:146", "8:34")
        )
        with patch.dict(os.environ, {"AAG_SAFE_SUSPEND_TEST_MODE": "1"}):
            result = identity.identify(self.config, renamed)
        self.assertEqual(result["token"]["serial"], "TEST-NVME-0001")
        self.assertEqual(result["token"]["disk_devno"], "8:32")

    def test_unrelated_usb_is_not_selected(self) -> None:
        with patch.dict(os.environ, {"AAG_SAFE_SUSPEND_TEST_MODE": "1"}):
            result = identity.identify(self.config, self.lsblk)
        paths = {row["action_path"] for row in result["token"]["partitions"]}
        self.assertNotIn("/dev/sdy1", paths)

    def test_partition_set_mismatch_fails_closed(self) -> None:
        changed = self.lsblk.replace('"uuid": "TEST-FS-B"', '"uuid": "CHANGED-FS"')
        with patch.dict(os.environ, {"AAG_SAFE_SUSPEND_TEST_MODE": "1"}):
            result = identity.identify(self.config, changed)
        self.assertFalse(result["complete"])
        self.assertEqual(result["gap"], "partition-identity-set-mismatch")

    def test_udev_rule_is_targeted_and_transaction_scoped(self) -> None:
        rule = identity.udev_rule(self.config, "/run/test/marker")
        self.assertIn('ID_SERIAL_SHORT}=="TEST-NVME-0001"', rule)
        self.assertIn('ID_USB_VENDOR_ID}=="0bda"', rule)
        self.assertIn('ID_FS_UUID}=="TEST-FS-A"', rule)
        self.assertIn('ID_FS_UUID}=="TEST-FS-B"', rule)
        self.assertNotIn("TEST-FS-A|TEST-FS-B", rule)
        self.assertIn('TEST=="/run/test/marker"', rule)
        self.assertNotIn("/dev/sd", rule)
        self.assertNotIn("UNRELATED-FS", rule)

    def test_udevadm_accepts_generated_rule_syntax(self) -> None:
        udevadm = shutil.which("udevadm")
        if not udevadm:
            self.skipTest("udevadm is unavailable")
        rule = identity.udev_rule(self.config, "/run/test/marker")
        with tempfile.TemporaryDirectory(prefix="aag-udev-fixture-") as temporary:
            path = Path(temporary) / "99-aag-test.rules"
            path.write_text(rule)
            result = subprocess.run(
                [udevadm, "verify", str(path)],
                text=True,
                capture_output=True,
                check=False,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
