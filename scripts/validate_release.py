#!/usr/bin/python3
"""Fail closed when release compatibility metadata and asset hashes diverge."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from build_release import PROJECT, ROOT, VERSION


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(dist: Path, expected_tag: str | None = None) -> dict[str, object]:
    manifest = json.loads((dist / "release-manifest.json").read_text())
    required = {
        "product",
        "version",
        "minimum_upgrader_schema",
        "upgrader_schema",
        "config_schema",
        "state_schema",
        "migration_schema",
        "systemd_wiring_revision",
        "udev_rule_revision",
        "supported_upgrade_from",
        "supported_downgrade_from",
        "migration_ids",
        "assets",
        "release_date",
        "hibernate",
        "ordinary_suspend",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise RuntimeError("release manifest fields missing: " + ",".join(missing))
    if manifest["product"] != PROJECT or manifest["version"] != VERSION:
        raise RuntimeError("release manifest product/version mismatch")
    if f'version = "{VERSION}"' not in (ROOT / "pyproject.toml").read_text() or (
        f'__version__ = "{VERSION}"' not in (ROOT / "src/aag_safe_suspend/__init__.py").read_text()
    ):
        raise RuntimeError("source version declarations are inconsistent")
    if expected_tag and expected_tag != f"v{VERSION}":
        raise RuntimeError(f"tag {expected_tag} does not match v{VERSION}")
    if manifest["minimum_upgrader_schema"] > manifest["upgrader_schema"]:
        raise RuntimeError("minimum upgrader schema exceeds release upgrader schema")
    if (
        manifest["config_schema"] != 2
        or manifest["migration_schema"] != 3
        or manifest["systemd_wiring_revision"] != 3
    ):
        raise RuntimeError("v1.2.1 schema or wiring metadata is inconsistent")
    if ">=1.0.0,<1.2.1" not in manifest["supported_upgrade_from"]:
        raise RuntimeError("public v1.0.0 bootstrap compatibility is not declared")
    required_migrations = {
        "bootstrap-public-v1.0.0-to-installed-state-v1",
        "upgrader-schema-1-to-2",
        "config-schema-1-to-2-hibernate-policy",
        "wiring-revision-1-to-2-plain-hibernate",
        "wiring-revision-2-to-3-ordinary-usbclone-gate",
        "adopt-accepted-reference-hibernate-qualification-v1",
    }
    if not required_migrations.issubset(set(manifest["migration_ids"])):
        raise RuntimeError("release manifest omits a required supported migration")
    hibernate = manifest["hibernate"]
    if hibernate != {
        "plain": "SUPPORTED_ON_REFERENCE_PLATFORM",
        "suspend_then_hibernate": "NOT_ENABLED_NOT_ACCEPTED",
        "hybrid_sleep": "NOT_ENABLED_NOT_ACCEPTED",
    }:
        raise RuntimeError("Hibernate support scope is inconsistent")
    if manifest["ordinary_suspend"] != {
        "usbclone_gate": "ENABLED_FAIL_CLOSED",
        "loaded_dummy_hcd_without_bound_device": "ALLOWED",
        "active_guest_usb_detach": "NEVER_AUTOMATIC",
    }:
        raise RuntimeError("ordinary-suspend USBClone scope is inconsistent")
    assets = manifest["assets"]
    if not isinstance(assets, dict) or not assets:
        raise RuntimeError("release assets are missing")
    actual_sums: dict[str, str] = {}
    for name, expected in assets.items():
        path = dist / name
        if not path.is_file() or path.is_symlink() or sha256(path) != expected:
            raise RuntimeError(f"release asset hash mismatch: {name}")
        actual_sums[name] = expected
    parsed_sums: dict[str, str] = {}
    for line in (dist / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        parsed_sums[name] = digest
    if parsed_sums != actual_sums:
        raise RuntimeError("SHA256SUMS and release manifest assets differ")
    return {
        "status": "PASS",
        "version": VERSION,
        "assets": len(assets),
        "upgrade_from_v1_0_0": True,
        "upgrade_from_v1_1_1": True,
        "upgrade_from_v1_2_0": True,
        "ordinary_usbclone_gate": "ENABLED_FAIL_CLOSED",
        "plain_hibernate": "SUPPORTED_ON_REFERENCE_PLATFORM",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--tag")
    args = parser.parse_args()
    print(json.dumps(validate(args.dist, args.tag), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
