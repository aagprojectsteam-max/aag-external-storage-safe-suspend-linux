from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_release  # noqa: E402


class ReleaseMetadataTests(unittest.TestCase):
    def fixture(self, root: Path) -> Path:
        dist = root / "dist"
        dist.mkdir()
        asset = dist / "asset.run"
        asset.write_bytes(b"verified fixture\n")
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()
        (dist / "SHA256SUMS").write_text(f"{digest}  asset.run\n")
        (dist / "release-manifest.json").write_text(
            json.dumps(
                {
                    "product": validate_release.PROJECT,
                    "version": validate_release.VERSION,
                    "release_date": "2026-09-09",
                    "minimum_upgrader_schema": 1,
                    "upgrader_schema": 2,
                    "config_schema": 2,
                    "state_schema": 1,
                    "migration_schema": 3,
                    "systemd_wiring_revision": 3,
                    "udev_rule_revision": 1,
                    "supported_upgrade_from": [">=1.0.0,<1.4.0"],
                    "supported_downgrade_from": [],
                    "migration_ids": [
                        "bootstrap-public-v1.0.0-to-installed-state-v1",
                        "upgrader-schema-1-to-2",
                        "config-schema-1-to-2-hibernate-policy",
                        "wiring-revision-1-to-2-plain-hibernate",
                        "wiring-revision-2-to-3-ordinary-usbclone-gate",
                        "adopt-accepted-reference-hibernate-qualification-v1",
                    ],
                    "transaction_adapter": {
                        "installer_command": "transaction",
                        "scope": "QUALIFIED_EXISTING_V2_T700_LOCKLOCK_STACK",
                        "automatic_portable_conversion": False,
                        "accepted_implementation_commit": "8c0095236f1095e29f5674db0a32476f431c964b",
                    },
                    "reference_hibernate": {
                        "installer_command": "hibernate",
                        "scope": "QUALIFIED_EXISTING_V2_T700_LOCKLOCK_STACK",
                        "automatic_portable_conversion": False,
                        "accepted_implementation_commit": "5cc43022551ad521f13af1eea72099b854e45230",
                        "runtime_sha256_manifest": "docs/hibernate-runtime-sha256.json",
                        "wwan_recovery_owner": "aag-hibernate-transaction-finish.service",
                        "physical_s4_acceptance": "PASS",
                        "mobile_connectivity_after_s4": "PASS",
                        "manual_reboot_required": False,
                    },
                    "ordinary_suspend": {
                        "usbclone_gate": "ENABLED_FAIL_CLOSED",
                        "loaded_dummy_hcd_without_bound_device": "ALLOWED",
                        "active_guest_usb_detach": "NEVER_AUTOMATIC",
                    },
                    "assets": {"asset.run": digest},
                    "hibernate": {
                        "plain": "SUPPORTED_ON_REFERENCE_PLATFORM",
                        "suspend_then_hibernate": "NOT_ENABLED_NOT_ACCEPTED",
                        "hybrid_sleep": "NOT_ENABLED_NOT_ACCEPTED",
                    },
                }
            )
            + "\n"
        )
        return dist

    def test_consistent_release_metadata_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = validate_release.validate(self.fixture(Path(temporary)), "v1.4.0")
        self.assertEqual(result["status"], "PASS")

    def test_asset_and_metadata_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dist = self.fixture(Path(temporary))
            (dist / "asset.run").write_bytes(b"substituted\n")
            with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                validate_release.validate(dist)

    def test_tag_and_version_mismatch_fails(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            self.assertRaisesRegex(RuntimeError, "does not match"),
        ):
            validate_release.validate(self.fixture(Path(temporary)), "v1.0.0")

    def test_wrong_s4_owner_or_weakened_acceptance_is_rejected(self) -> None:
        for key, value in (
            ("wwan_recovery_owner", "legacy.service"),
            ("mobile_connectivity_after_s4", "NOT_TESTED"),
            ("manual_reboot_required", True),
            ("automatic_portable_conversion", True),
        ):
            with tempfile.TemporaryDirectory() as temporary:
                dist = self.fixture(Path(temporary))
                path = dist / "release-manifest.json"
                manifest = json.loads(path.read_text())
                manifest["reference_hibernate"][key] = value
                path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(RuntimeError, "qualification scope"):
                    validate_release.validate(dist)


if __name__ == "__main__":
    unittest.main()
