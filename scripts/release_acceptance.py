#!/usr/bin/python3
"""Exercise the built self-extracting asset in an isolated filesystem root."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.2.0"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(argv: list[str], expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, text=True, capture_output=True, check=False, timeout=60)
    if result.returncode != expected:
        raise RuntimeError(
            f"unexpected status {result.returncode} for {argv[0]}: "
            + (result.stderr or result.stdout)[-2000:]
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("asset", type=Path)
    args = parser.parse_args()
    asset = args.asset.resolve()
    if not asset.is_file() or not (asset.stat().st_mode & 0o111):
        raise RuntimeError("release installer is missing or not executable")
    verified = run([str(asset), "verify-source"])
    if '"source": "valid"' not in verified.stdout:
        raise RuntimeError("self-extracted source validation did not pass")

    with tempfile.TemporaryDirectory(prefix="aag-release-acceptance-") as temporary:
        root = Path(temporary) / "root"
        (root / "usr/bin").mkdir(parents=True)
        shutil.copy2("/bin/true", root / "usr/bin/timeshift")
        shutil.copy2("/bin/true", root / "usr/bin/timeshift-gtk")
        original = sha256(root / "usr/bin/timeshift")
        common = [str(asset), "--root", str(root), "--test-mode"]
        install = run(
            common
            + [
                "install",
                "--config",
                str(ROOT / "tests/fixtures/config.json"),
                "--timeshift",
            ]
        )
        if "POWER_STATE_ACTIONS=NONE" not in install.stdout:
            raise RuntimeError("release installer did not attest to no power action")
        if not (root / "usr/local/libexec/aag-safe-suspend").is_file():
            raise RuntimeError("release asset did not install its coordinator")
        state = root / "var/lib/aag-external-storage-safe-suspend/install-state.json"
        if not state.is_file() or f'"installed_version": "{VERSION}"' not in state.read_text():
            raise RuntimeError("release asset did not commit canonical installed state")
        cli_env = {
            **os.environ,
            "AAG_SAFE_SUSPEND_ROOT": str(root),
            "AAG_SAFE_SUSPEND_TEST_MODE": "1",
        }
        version = subprocess.run(
            [str(root / "usr/local/bin/aag-safe-suspend"), "--version"],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
            env=cli_env,
        )
        if version.returncode or version.stdout.strip() != VERSION:
            raise RuntimeError("stable installed version command failed")
        status = subprocess.run(
            [str(root / "usr/local/bin/aag-safe-suspend"), "status"],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
            env=cli_env,
        )
        if status.returncode or '"status": "HEALTHY"' not in status.stdout:
            raise RuntimeError("stable installed status command failed")
        if '"plain_hibernate_support": "SUPPORTED_ON_REFERENCE_PLATFORM"' not in status.stdout:
            raise RuntimeError("installed status omitted scoped plain-Hibernate support")
        health = subprocess.run(
            [str(root / "usr/local/bin/aag-safe-suspend"), "health-check"],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
            env=cli_env,
        )
        if (
            health.returncode
            or '"HIBERNATE_READY": "NOT_EVALUATED_ISOLATED_ROOT"' not in health.stdout
        ):
            raise RuntimeError("non-destructive Hibernate health surface failed")
        same = run(common + ["install"])
        if '"result": "SAME_VERSION"' not in same.stdout:
            raise RuntimeError("release installer did not detect a same-version invocation")
        run(common + ["uninstall"])
        if sha256(root / "usr/bin/timeshift") != original:
            raise RuntimeError("release asset did not restore the Timeshift fixture")
        if (root / "usr/local/libexec/aag-safe-suspend").exists():
            raise RuntimeError("release asset left a project executable after uninstall")
        if (root / "etc/aag-external-storage-safe-suspend").exists():
            raise RuntimeError("release asset left an empty project configuration directory")

        # Build a sanitized managed v1.1.1 fixture from the just-verified
        # payload, then prove the complete asset upgrade and deliberate
        # rollback path without embedding or downloading an old executable.
        upgrade_root = Path(temporary) / "upgrade-root"
        (upgrade_root / "usr/bin").mkdir(parents=True)
        shutil.copy2("/bin/true", upgrade_root / "usr/bin/timeshift")
        shutil.copy2("/bin/true", upgrade_root / "usr/bin/timeshift-gtk")
        upgrade_common = [str(asset), "--root", str(upgrade_root), "--test-mode"]
        run(
            upgrade_common
            + ["install", "--config", str(ROOT / "tests/fixtures/config.json"), "--timeshift"]
        )
        upgrade_state_path = (
            upgrade_root / "var/lib/aag-external-storage-safe-suspend/install-state.json"
        )
        upgrade_state = json.loads(upgrade_state_path.read_text())
        for logical in list(upgrade_state["files"]):
            if "hibernate" not in logical.lower():
                continue
            (upgrade_root / logical.removeprefix("/")).unlink()
            del upgrade_state["files"][logical]
        config_path = upgrade_root / "etc/aag-external-storage-safe-suspend/config.json"
        old_config = json.loads(config_path.read_text())
        old_config["version"] = 1
        old_config.pop("hibernate", None)
        config_path.write_text(json.dumps(old_config, sort_keys=True, indent=2) + "\n")
        upgrade_state["files"]["/etc/aag-external-storage-safe-suspend/config.json"]["sha256"] = (
            sha256(config_path)
        )
        upgrade_state.update(
            installed_version="1.1.1",
            configuration_schema=1,
            migration_schema=1,
            systemd_wiring_revision=1,
        )
        upgrade_state["release"]["tag"] = "v1.1.1"
        upgrade_state_path.write_text(json.dumps(upgrade_state, sort_keys=True, indent=2) + "\n")
        upgrade_state_path.chmod(0o600)
        upgraded = run(upgrade_common + ["install"])
        if '"installed_version": "1.2.0"' not in upgraded.stdout:
            raise RuntimeError("v1.1.1 fixture did not upgrade to v1.2.0")
        upgrade_env = {
            **os.environ,
            "AAG_SAFE_SUSPEND_ROOT": str(upgrade_root),
            "AAG_SAFE_SUSPEND_TEST_MODE": "1",
        }
        rolled_back = subprocess.run(
            [str(upgrade_root / "usr/local/bin/aag-safe-suspend"), "rollback"],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
            env=upgrade_env,
        )
        if rolled_back.returncode or '"to_version": "1.1.1"' not in rolled_back.stdout:
            raise RuntimeError("v1.2.0 rollback did not restore v1.1.1")
        run(upgrade_common + ["uninstall"])

        corrupt = Path(temporary) / "corrupt.run"
        data = bytearray(asset.read_bytes())
        data[-1] ^= 1
        corrupt.write_bytes(data)
        corrupt.chmod(0o755)
        refused = run([str(corrupt), "verify-source"], expected=1)
        if "checksum mismatch" not in refused.stderr.lower():
            raise RuntimeError("corrupt release payload did not fail at its checksum")

    print("SELF_EXTRACT_CHECKSUM=PASS")
    print("RELEASE_INSTALLER_TEST=PASS")
    print("RELEASE_ROLLBACK_TEST=PASS")
    print("UPGRADE_1_1_1_TO_1_2_0=PASS")
    print("POWER_STATE_ACTIONS=NONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
