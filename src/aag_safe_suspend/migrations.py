"""Declarative, side-effect-free migration planning.

File mutation is performed only by the installer's transaction after this plan
has passed.  Keeping schema transitions pure makes preconditions and
idempotence independently testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    migration_id: str
    domain: str
    from_schema: int
    to_schema: int
    precondition: str
    idempotent: bool
    rollback: str


CATALOG = {
    "bootstrap-public-v1.0.0-to-installed-state-v1": Migration(
        migration_id="bootstrap-public-v1.0.0-to-installed-state-v1",
        domain="installed-state",
        from_schema=0,
        to_schema=1,
        precondition="published-v1.0.0-static-hashes-and-semantic-config-valid",
        idempotent=True,
        rollback="restore-legacy-active-json-and-exact-file-snapshot",
    ),
    "upgrader-schema-1-to-2": Migration(
        migration_id="upgrader-schema-1-to-2",
        domain="upgrader",
        from_schema=1,
        to_schema=2,
        precondition="managed-state-v1-or-verified-public-v1.0.0-bootstrap",
        idempotent=True,
        rollback="restore-prior-installed-state-and-file-snapshot",
    ),
    "config-schema-1-to-2-hibernate-policy": Migration(
        migration_id="config-schema-1-to-2-hibernate-policy",
        domain="configuration",
        from_schema=1,
        to_schema=2,
        precondition="validated-v1-supported-fields-preserved-hibernate-opt-in-defaults-added",
        idempotent=True,
        rollback="restore-exact-prior-configuration-and-generated-rule-snapshot",
    ),
    "wiring-revision-1-to-2-plain-hibernate": Migration(
        migration_id="wiring-revision-1-to-2-plain-hibernate",
        domain="systemd-wiring",
        from_schema=1,
        to_schema=2,
        precondition="ordinary-suspend-contract-v1-verified",
        idempotent=True,
        rollback="restore-exact-prior-unit-set-and-daemon-reload",
    ),
    "wiring-revision-2-to-3-ordinary-usbclone-gate": Migration(
        migration_id="wiring-revision-2-to-3-ordinary-usbclone-gate",
        domain="systemd-wiring",
        from_schema=2,
        to_schema=3,
        precondition="ordinary-and-hibernate-v2-graphs-semantically-verified",
        idempotent=True,
        rollback="restore-exact-v2-unit-set-and-daemon-reload",
    ),
    "adopt-accepted-reference-hibernate-qualification-v1": Migration(
        migration_id="adopt-accepted-reference-hibernate-qualification-v1",
        domain="qualification-integration",
        from_schema=1,
        to_schema=2,
        precondition="complete-final-accepted-qualification-file-set-matches-published-hashes",
        idempotent=True,
        rollback="restore-exact-qualification-files-from-bounded-snapshot",
    ),
}


def plan(
    active: dict[str, Any] | None,
    *,
    legacy_bootstrap: bool,
    accepted_qualification: bool = False,
) -> list[Migration]:
    if active is None:
        return []
    result: list[Migration] = []
    if legacy_bootstrap:
        if active.get("installed_version") != "1.0.0" or active.get("migration_schema") != 0:
            raise MigrationError("legacy bootstrap preconditions are not satisfied")
        result.append(CATALOG["bootstrap-public-v1.0.0-to-installed-state-v1"])
    schema = active.get("upgrader_schema")
    if schema == 1:
        result.append(CATALOG["upgrader-schema-1-to-2"])
    elif schema != 2:
        raise MigrationError(f"unsupported upgrader schema: {schema}")
    config_schema = active.get("configuration_schema", 1)
    if config_schema == 1:
        result.append(CATALOG["config-schema-1-to-2-hibernate-policy"])
    elif config_schema != 2:
        raise MigrationError(f"unsupported configuration schema: {config_schema}")
    wiring = active.get("systemd_wiring_revision", 1)
    if wiring == 1:
        result.append(CATALOG["wiring-revision-1-to-2-plain-hibernate"])
        result.append(CATALOG["wiring-revision-2-to-3-ordinary-usbclone-gate"])
    elif wiring == 2:
        result.append(CATALOG["wiring-revision-2-to-3-ordinary-usbclone-gate"])
    elif wiring != 3:
        raise MigrationError(f"unsupported systemd wiring revision: {wiring}")
    if accepted_qualification:
        result.append(CATALOG["adopt-accepted-reference-hibernate-qualification-v1"])
    return result


def apply(value: dict[str, Any], migrations: list[Migration]) -> dict[str, Any]:
    """Return a migrated copy; applying the same plan twice is harmless."""
    result = dict(value)
    applied = list(result.get("migrations_applied", []))
    for migration in migrations:
        if migration.migration_id in applied:
            continue
        if migration.domain == "installed-state":
            if result.get("state_schema") != migration.to_schema:
                raise MigrationError("installed-state bootstrap normalization is incomplete")
            result["migration_schema"] = 1
        elif migration.domain == "upgrader":
            if result.get("upgrader_schema") != migration.from_schema:
                raise MigrationError("upgrader migration FROM precondition failed")
            result["upgrader_schema"] = migration.to_schema
        elif migration.domain == "configuration":
            if result.get("configuration_schema", 1) != migration.from_schema:
                raise MigrationError("configuration migration FROM precondition failed")
            result["configuration_schema"] = migration.to_schema
        elif migration.domain == "systemd-wiring":
            if result.get("systemd_wiring_revision", 1) != migration.from_schema:
                raise MigrationError("systemd wiring migration FROM precondition failed")
            result["systemd_wiring_revision"] = migration.to_schema
        elif migration.domain == "qualification-integration":
            # Exact file identity and removal are performed by the surrounding
            # transactional installer; the pure plan records the declaration.
            pass
        applied.append(migration.migration_id)
    result["migrations_applied"] = applied
    return result
