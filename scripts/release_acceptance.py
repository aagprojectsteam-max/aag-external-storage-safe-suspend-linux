#!/usr/bin/python3
"""Exercise the built self-extracting asset in an isolated filesystem root."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.1.1"


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
    print("POWER_STATE_ACTIONS=NONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
