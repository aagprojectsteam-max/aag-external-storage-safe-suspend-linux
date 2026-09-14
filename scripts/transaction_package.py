#!/usr/bin/python3
"""Package the qualified reference adapter; isolated mode never calls the host."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import deploy_transaction as deploy

LINK = Path("/etc/systemd/system/multi-user.target.wants/aag-sleep-transaction-boot.service")
LINK_VALUE = "/etc/systemd/system/aag-sleep-transaction-boot.service"


def target(root, logical):
    if not logical.is_absolute() or ".." in logical.parts:
        raise RuntimeError("invalid logical package path")
    result = root / logical.relative_to("/")
    for parent in [result, *result.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise RuntimeError("symlink in isolated package destination")
    if not result.resolve().is_relative_to(root):
        raise RuntimeError("isolated package path escaped root")
    return result


def isolated(args):
    root = args.root.resolve()
    if root == Path("/") or not args.test_mode:
        raise RuntimeError("isolated mode requires --test-mode and a non-root prefix")
    root.mkdir(parents=True, exist_ok=True)
    report = args.report.resolve()
    report.mkdir(parents=True, exist_ok=True, mode=0o700)
    link = root / LINK.relative_to("/")
    target(root, LINK.parent)
    if (link.exists() or link.is_symlink()) and (
        not link.is_symlink() or str(link.readlink()) != LINK_VALUE
    ):
        raise RuntimeError("unexpected boot enablement")
    if args.rollback:
        backup = args.rollback.resolve()
        receipt = json.loads((backup / "isolated-manifest.json").read_text())
        if receipt["root"] != str(root):
            raise RuntimeError("rollback belongs to a different isolated prefix")
        allowed = set(deploy.mapping(deploy.production.CONFIG).values())
        allowed.update([LINK, Path("/usr/lib/input-lock/input_lock_safety.py")])
        for row in receipt["files"]:
            logical = Path(row["installed"])
            if logical not in allowed:
                raise RuntimeError("unexpected rollback path")
            if logical != LINK:
                target(root, logical)
            if row["existed"] and logical != LINK:
                saved = backup / logical.relative_to("/")
                if saved.is_symlink() or deploy.digest(saved) != row["prior_sha256"]:
                    raise RuntimeError("rollback snapshot checksum mismatch")
        for row in reversed(receipt["files"]):
            logical = Path(row["installed"])
            if logical == LINK:
                if link.is_symlink() and not row["existed"]:
                    link.unlink()
                continue
            destination = target(root, logical)
            if row["existed"]:
                shutil.copy2(backup / logical.relative_to("/"), destination)
            elif destination.exists():
                destination.unlink()
        return {
            "status": "ROLLBACK_COMPLETE",
            "validation": "ISOLATED_PAYLOAD_ONLY",
            "power_state_actions": "none",
        }
    if args.config is None:
        raise RuntimeError("--config is required")
    config = args.config.resolve()
    deploy.workloads.validate_registry(json.loads(config.read_text())["workloads"])
    safety = target(root, Path("/usr/lib/input-lock/input_lock_safety.py"))
    integrated = deploy.prepare_locklock(report, path=safety, verify_trust=False)
    mapping = deploy.mapping(config, integrated)
    for source, logical in mapping.items():
        if not source.is_file() or source.is_symlink():
            raise RuntimeError("missing or unsafe transaction source payload")
        target(root, logical)
    if args.check:
        return {
            "status": "PAYLOAD_READY",
            "files": len(mapping),
            "validation": "ISOLATED_PAYLOAD_ONLY",
            "power_state_actions": "none",
        }
    backup = report / ("isolated-backup-" + str(time.time_ns()))
    backup.mkdir(mode=0o700)
    rows = []
    for source, logical in mapping.items():
        destination = target(root, logical)
        row = {
            "installed": str(logical),
            "existed": destination.exists(),
            "source_sha256": deploy.digest(source),
        }
        if destination.exists():
            saved = backup / logical.relative_to("/")
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, saved)
            row["prior_sha256"] = deploy.digest(saved)
        rows.append(row)
    rows.append({"installed": str(LINK), "existed": link.is_symlink()})
    (backup / "isolated-manifest.json").write_text(
        json.dumps({"root": str(root), "files": rows}, indent=2) + "\n"
    )
    for source, logical in mapping.items():
        destination = target(root, logical)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(
            0o600
            if logical == deploy.production.CONFIG
            else 0o755
            if logical.name == "aag-sleep-transaction"
            else 0o644
        )
        if deploy.digest(destination) != deploy.digest(source):
            raise RuntimeError("installed transaction payload checksum mismatch")
    link.parent.mkdir(parents=True, exist_ok=True)
    if not link.is_symlink():
        link.symlink_to(LINK_VALUE)
    return {
        "status": "INSTALLED",
        "files": len(mapping),
        "rollback": str(backup),
        "validation": "ISOLATED_PAYLOAD_ONLY",
        "power_state_actions": "none",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--rollback", type=Path)
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--test-mode", action="store_true")
    args = parser.parse_args()
    if args.test_mode or args.root != Path("/"):
        print(json.dumps(isolated(args), indent=2))
        return 0
    if args.config is None and args.rollback is None:
        parser.error("--config is required unless restoring --rollback")
    if os.geteuid() != 0:
        parser.error("live transaction deployment requires root")
    command = [
        sys.executable,
        str(Path(__file__).with_name("deploy_transaction.py")),
        "--report",
        str(args.report),
    ]
    if args.config:
        command.extend(["--config", str(args.config)])
    if args.check:
        command.append("--check")
    if args.rollback:
        command.extend(["--rollback", str(args.rollback)])
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
