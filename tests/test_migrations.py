from __future__ import annotations

import unittest

from aag_safe_suspend import migrations


class MigrationTests(unittest.TestCase):
    def test_fresh_install_has_no_migrations(self) -> None:
        self.assertEqual(migrations.plan(None, legacy_bootstrap=False), [])

    def test_legacy_bootstrap_declares_from_to_and_rollback(self) -> None:
        active = {
            "installed_version": "1.0.0",
            "state_schema": 1,
            "migration_schema": 0,
            "upgrader_schema": 1,
        }
        plan = migrations.plan(active, legacy_bootstrap=True)
        self.assertEqual(
            [item.migration_id for item in plan],
            [
                "bootstrap-public-v1.0.0-to-installed-state-v1",
                "upgrader-schema-1-to-2",
                "config-schema-1-to-2-hibernate-policy",
                "wiring-revision-1-to-2-plain-hibernate",
            ],
        )
        self.assertTrue(all(item.idempotent and item.rollback for item in plan))
        migrated = migrations.apply(active, plan)
        self.assertEqual(migrated["migration_schema"], 1)
        self.assertEqual(migrated["upgrader_schema"], 2)
        self.assertEqual(migrated["configuration_schema"], 2)
        self.assertEqual(migrated["systemd_wiring_revision"], 2)
        self.assertEqual(migrations.apply(migrated, plan), migrated)

    def test_unknown_schema_refuses(self) -> None:
        with self.assertRaises(migrations.MigrationError):
            migrations.plan(
                {"installed_version": "1.0.0", "upgrader_schema": 99},
                legacy_bootstrap=False,
            )


if __name__ == "__main__":
    unittest.main()
