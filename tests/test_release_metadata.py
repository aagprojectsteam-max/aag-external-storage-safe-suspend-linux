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
                    "release_date": "2026-09-08",
                    "minimum_upgrader_schema": 1,
                    "upgrader_schema": 2,
                    "config_schema": 1,
                    "state_schema": 1,
                    "migration_schema": 1,
                    "systemd_wiring_revision": 1,
                    "udev_rule_revision": 1,
                    "supported_upgrade_from": [">=1.0.0,<1.2.0"],
                    "supported_downgrade_from": [],
                    "migration_ids": ["bootstrap-public-v1.0.0-to-installed-state-v1"],
                    "assets": {"asset.run": digest},
                }
            )
            + "\n"
        )
        return dist

    def test_consistent_release_metadata_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = validate_release.validate(self.fixture(Path(temporary)), "v1.1.1")
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


if __name__ == "__main__":
    unittest.main()
