#!/usr/bin/python3
"""Build deterministic, self-verifying release artifacts and compatibility metadata."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import shutil
import stat
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.3.1"
PROJECT = "aag-external-storage-safe-suspend-linux"
EPOCH = 1789344000  # 2026-09-14T00:00:00Z
EXCLUDES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "release-work",
    "reports",
    "PAYLOAD-SHA256SUMS",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_mode(path: Path) -> int:
    """Ignore checkout umask while preserving the Git executable distinction."""
    return 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644


def public_files() -> list[Path]:
    result: list[Path] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDES for part in relative.parts):
            continue
        if path.is_symlink():
            raise RuntimeError(f"release tree contains a symlink: {relative}")
        if path.is_file():
            result.append(relative)
    return sorted(result)


def tar_bytes(prefix: str = "") -> tuple[bytes, dict[str, str]]:
    paths = public_files()
    hashes = {str(path): digest((ROOT / path).read_bytes()) for path in paths}
    manifest = "".join(f"{hashes[str(path)]}  {path}\n" for path in paths).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for relative in paths:
            data = (ROOT / relative).read_bytes()
            name = str(Path(prefix) / relative) if prefix else str(relative)
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = canonical_mode(ROOT / relative)
            info.mtime = EPOCH
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            archive.addfile(info, io.BytesIO(data))
        name = str(Path(prefix) / "PAYLOAD-SHA256SUMS") if prefix else "PAYLOAD-SHA256SUMS"
        info = tarfile.TarInfo(name)
        info.size = len(manifest)
        info.mode = 0o644
        info.mtime = EPOCH
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        archive.addfile(info, io.BytesIO(manifest))
    return buffer.getvalue(), hashes


def deterministic_gzip(data: bytes) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=output, mtime=EPOCH, compresslevel=9
    ) as stream:
        stream.write(data)
    return output.getvalue()


def run_header(payload_hash: str) -> bytes:
    text = f'''#!/bin/sh
set -eu
PROJECT="{PROJECT}"
PAYLOAD_SHA256="{payload_hash}"
SELF="$0"
WORK="$(mktemp -d "/var/tmp/${{PROJECT}}.XXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT HUP INT TERM
LINE="$(awk '/^__AAG_PAYLOAD_BELOW__$/ {{ print NR + 1; exit }}' "$SELF")"
test -n "$LINE" || {{ echo "Installer payload marker is missing" >&2; exit 1; }}
tail -n "+$LINE" "$SELF" > "$WORK/payload.tar.gz"
ACTUAL="$(sha256sum "$WORK/payload.tar.gz" | awk '{{print $1}}')"
test "$ACTUAL" = "$PAYLOAD_SHA256" || {{ echo "Installer payload checksum mismatch" >&2; exit 1; }}
mkdir "$WORK/tree"
tar -xzf "$WORK/payload.tar.gz" -C "$WORK/tree"
(cd "$WORK/tree" && sha256sum -c PAYLOAD-SHA256SUMS >/dev/null)
INSTALLER_SHA256="$(sha256sum "$SELF" | awk '{{print $1}}')"
ENTRYPOINT="$WORK/tree/scripts/install.py"
if [ "${{1-}}" = "transaction" ]; then
    shift
    ENTRYPOINT="$WORK/tree/scripts/transaction_package.py"
fi
AAG_RELEASE_SOURCE="GITHUB_RELEASE_VERIFIED_PAYLOAD" \
AAG_RELEASE_PAYLOAD_SHA256="$PAYLOAD_SHA256" \
AAG_RELEASE_INSTALLER_SHA256="$INSTALLER_SHA256" \
/usr/bin/python3 "$ENTRYPOINT" "$@"
exit 0
__AAG_PAYLOAD_BELOW__
'''
    return text.encode()


def build() -> dict[str, object]:
    dist = ROOT / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(mode=0o755)

    payload_tar, file_hashes = tar_bytes()
    payload = deterministic_gzip(payload_tar)
    installer_name = f"{PROJECT}-v{VERSION}.run"
    installer_path = dist / installer_name
    installer_path.write_bytes(run_header(digest(payload)) + payload)
    installer_path.chmod(0o755)

    source_tar, _ = tar_bytes(f"{PROJECT}-{VERSION}")
    source_name = f"{PROJECT}-v{VERSION}.tar.gz"
    source_path = dist / source_name
    source_path.write_bytes(deterministic_gzip(source_tar))

    assets = {
        installer_name: digest(installer_path.read_bytes()),
        source_name: digest(source_path.read_bytes()),
    }
    checksums = "".join(f"{value}  {name}\n" for name, value in sorted(assets.items()))
    (dist / "SHA256SUMS").write_text(checksums)
    release_manifest = {
        "product": PROJECT,
        "version": VERSION,
        "release_date": "2026-09-14",
        "source_date_epoch": EPOCH,
        "minimum_upgrader_schema": 1,
        "upgrader_schema": 2,
        "config_schema": 2,
        "state_schema": 1,
        "migration_schema": 3,
        "systemd_wiring_revision": 3,
        "udev_rule_revision": 1,
        "supported_upgrade_from": [">=1.0.0,<1.3.1"],
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
            "runtime_sha256_manifest": "docs/transaction-runtime-sha256.json",
            "actual_physical_lid_acceptance": "PASS",
        },
        "ordinary_suspend": {
            "usbclone_gate": "ENABLED_FAIL_CLOSED",
            "loaded_dummy_hcd_without_bound_device": "ALLOWED",
            "active_guest_usb_detach": "NEVER_AUTOMATIC",
        },
        "hibernate": {
            "plain": "SUPPORTED_ON_REFERENCE_PLATFORM",
            "suspend_then_hibernate": "NOT_ENABLED_NOT_ACCEPTED",
            "hybrid_sleep": "NOT_ENABLED_NOT_ACCEPTED",
        },
        "assets": assets,
        "public_file_count": len(file_hashes),
        "power_state_actions": "none",
    }
    (dist / "release-manifest.json").write_text(
        json.dumps(release_manifest, sort_keys=True, indent=2) + "\n"
    )
    return release_manifest


def validate_only() -> dict[str, object]:
    accepted = json.loads((ROOT / "docs/transaction-runtime-sha256.json").read_text())
    for name, expected in accepted["sha256"].items():
        if digest((ROOT / name).read_bytes()) != expected:
            raise RuntimeError("qualified runtime bytes changed: " + name)
    paths = public_files()
    required = {
        Path("README.md"),
        Path("LICENSE"),
        Path("SECURITY.md"),
        Path("CONTRIBUTING.md"),
        Path("scripts/install.py"),
        Path("scripts/transaction_package.py"),
        Path("scripts/deploy_transaction.py"),
        Path("src/aag-sleep-transaction"),
        Path("src/aag_safe_suspend/production.py"),
        Path("docs/transaction-runtime-sha256.json"),
        Path("src/aag_safe_suspend/maintenance.py"),
        Path("systemd/aag-external-storage-safe-suspend.service"),
        Path("udev/99-aag-external-storage-safe-suspend.rules.in"),
    }
    missing = sorted(str(path) for path in required - set(paths))
    if missing:
        raise RuntimeError("required release files missing: " + ",".join(missing))
    for path in paths:
        if (ROOT / path).stat().st_size > 2 * 1024 * 1024:
            raise RuntimeError(f"unexpected large public file: {path}")
    raw, hashes = tar_bytes()
    if not raw or not hashes:
        raise RuntimeError("release payload is empty")
    return {"release_tree": "valid", "files": len(paths), "power_state_actions": "none"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    value = validate_only() if args.check_only else build()
    print(json.dumps(value, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
