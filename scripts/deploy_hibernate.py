#!/usr/bin/python3
"""Add the S4 integration with exact rollback; never dispatch a power action."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REQUIRED_GATES = (
    "HIBERNATE_TRANSACTION_MODEL",
    "CONSUMER_RESTORE",
    "WWAN_SINGLE_OWNER",
    "COLD_BOOT_RECONCILIATION",
    "ABORT_RECONCILIATION",
    "FULL_SUSPEND_REGRESSION",
)
LEGACY = ("abort-reconcile", "boot-check", "resume-check", "wwan-recovery")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mapping(config):
    files = {REPO / "src/aag-power-transaction": Path("/usr/local/libexec/aag-power-transaction")}
    for name in ("power_dispatch", "s4_transaction", "s4_checks", "s4_host"):
        files[REPO / "src/aag_safe_suspend" / (name + ".py")] = Path(
            "/usr/local/lib/aag-sleep-transaction/aag_safe_suspend"
        ) / (name + ".py")
    units = {
        "storage": "aag-storage-sleep-v2",
        "failure": "aag-suspend-failure-failsafe",
        "boot": "aag-sleep-transaction-boot",
        "native": "systemd-hibernate",
    }
    for kind, unit in units.items():
        files[REPO / "systemd/hibernate" / ("zz-" + kind + ".conf")] = (
            Path("/etc/systemd/system")
            / (unit + ".service.d")
            / "zz-aag-hibernate-transaction.conf"
        )
    files[REPO / "systemd/hibernate/aag-hibernate-transaction-finish.service"] = Path(
        "/etc/systemd/system/aag-hibernate-transaction-finish.service"
    )
    files[REPO / "systemd/hibernate/80-aag-hibernate-qualification.conf"] = Path(
        "/etc/systemd/system/systemd-hibernate.service.d/80-aag-hibernate-qualification.conf"
    )
    # A list is necessary because the same retired stub has several destinations.
    rows = list(files.items())
    for name in LEGACY:
        rows.append(
            (
                REPO / "systemd/hibernate/retired.service",
                Path("/etc/systemd/system/aag-hibernate-" + name + ".service"),
            )
        )
    rows.extend(
        [
            (
                REPO / "systemd/hibernate/retired-qualifier",
                Path("/usr/local/libexec/aag-hibernate-qualifier"),
            ),
            (
                REPO / "systemd/hibernate/99-aag-wwan-hibernate",
                Path("/usr/lib/systemd/system-sleep/99-aag-wwan-hibernate"),
            ),
            (
                REPO / "systemd/hibernate/retired-wwan-helper",
                Path("/usr/local/sbin/aag-wwan-hibernate-recover"),
            ),
            (
                REPO / "systemd/hibernate/retired-wwan-helper",
                Path("/usr/local/libexec/aag-hibernate-wwan-qualified-recover"),
            ),
            (config, Path("/etc/aag-sleep-transaction/hibernate.json")),
        ]
    )
    return rows


def destination(root, path):
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError("invalid installation path")
    target = root / path.relative_to("/")
    for parent in [target, *target.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise RuntimeError("symlink in S4 installation path: " + str(parent))
    return target


def validate_gates(gates):
    if (
        any(gates.get(key) != "PASS" for key in REQUIRED_GATES)
        or gates.get("ROLLBACK_READY") != "YES"
    ):
        raise RuntimeError("S4 deployment gates are incomplete or failing")


def rollback(root, backup):
    receipt = json.loads((backup / "manifest.json").read_text())
    if receipt["root"] != str(root):
        raise RuntimeError("rollback belongs to a different installation root")
    allowed = {str(dst) for _, dst in mapping(Path("/unused"))}
    for row in receipt["files"]:
        if row["path"] not in allowed:
            raise RuntimeError("unexpected rollback destination")
        destination(root, Path(row["path"]))
        if row["existed"]:
            saved = backup / row["path"].lstrip("/")
            if saved.is_symlink() or digest(saved) != row["before_sha256"]:
                raise RuntimeError("corrupted rollback artifact")
    for row in reversed(receipt["files"]):
        target = destination(root, Path(row["path"]))
        if row["existed"]:
            shutil.copy2(backup / row["path"].lstrip("/"), target)
            if root == Path("/"):
                os.chown(target, row["uid"], row["gid"])
            target.chmod(row["mode"])
        elif target.exists():
            target.unlink()
    return receipt


def install(root, config, report, gates, baseline):
    validate_gates(gates)
    config_value = json.loads(config.read_text())
    if config_value.get("simulated_gates") != {key: gates[key] for key in REQUIRED_GATES}:
        raise RuntimeError("runtime configuration does not match tested gates")
    files = mapping(config)
    # Refuse partial compatibility or a baseline changed since qualification.
    for row in baseline:
        target = destination(root, Path(row["installed"]))
        if digest(target) != row["expected"]:
            raise RuntimeError("accepted v1.3.1 baseline changed: " + str(target))
    for source, path in files:
        if source.is_symlink() or not source.is_file():
            raise RuntimeError("invalid S4 payload source")
        destination(root, path)
    for source, path in files:
        if path.name != "hibernate.json" and config_value["pinned_files"].get(str(path)) != digest(
            source
        ):
            raise RuntimeError("payload differs from pinned/tested candidate")
    report.mkdir(parents=True, exist_ok=True)
    backup = report / ("rollback-" + str(time.time_ns()))
    backup.mkdir(mode=0o700)
    rows = []
    for source, path in files:
        target = destination(root, path)
        row = {
            "path": str(path),
            "source": str(source),
            "after_sha256": digest(source),
            "existed": target.exists(),
        }
        if target.exists():
            saved = backup / path.relative_to("/")
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
            st = target.stat()
            row.update(
                before_sha256=digest(saved), uid=st.st_uid, gid=st.st_gid, mode=st.st_mode & 0o777
            )
        rows.append(row)
    receipt = {
        "root": str(root),
        "files": rows,
        "baseline": baseline,
        "power_actions": False,
        "service_restarts": False,
    }
    (backup / "manifest.json").write_text(json.dumps(receipt, indent=2) + "\n")
    try:
        for source, path in files:
            target = destination(root, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.name + ".aag-s4-new")
            with temp.open("xb") as stream:
                stream.write(source.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            temp.chmod(
                0o600
                if path.name == "hibernate.json"
                else 0o755
                if ("libexec" in path.parts or "sbin" in path.parts or "system-sleep" in path.parts)
                else 0o644
            )
            if root == Path("/"):
                os.chown(temp, 0, 0)
            os.replace(temp, target)
            fd = os.open(target.parent, os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            if digest(target) != digest(source):
                raise RuntimeError("installed S4 hash mismatch")
        for row in baseline:
            if digest(destination(root, Path(row["installed"]))) != row["expected"]:
                raise RuntimeError("S4 installation altered the accepted baseline")
    except BaseException:
        rollback(root, backup)
        raise
    (report / "installed-manifest.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--gates", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--test-mode", action="store_true")
    parser.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if (root != Path("/")) != args.test_mode:
        parser.error("isolated root and --test-mode must be specified together")
    if root == Path("/") and os.geteuid() != 0:
        parser.error("live installation requires Linux root authentication")
    args.report.mkdir(parents=True, exist_ok=True)
    if root == Path("/"):
        ledger = Path("/var/lib/aag-sleep-transaction/current.json")
        if ledger.exists() and json.loads(ledger.read_text()).get("state") not in {
            "IDLE",
            "COMPLETE",
            "RECOVERED",
            "ABANDONED",
        }:
            raise RuntimeError("reconcile the current power transaction before install/rollback")
    if args.rollback:
        rollback(root, args.rollback)
        backup = args.rollback
    else:
        if root == Path("/"):
            for unit in (
                "aag-storage-sleep-v2.service",
                "systemd-suspend.service",
                "systemd-hibernate.service",
                "aag-t700-resume-check.service",
                "aag-hibernate-transaction-finish.service",
            ):
                cp = subprocess.run(
                    ["systemctl", "show", unit, "-p", "ActiveState,Job"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                fields = dict(x.split("=", 1) for x in cp.stdout.splitlines() if "=" in x)
                if fields.get("ActiveState") not in {"inactive", "failed"} or fields.get(
                    "Job"
                ) not in {"", "0"}:
                    raise RuntimeError("active power transaction: " + unit)
        backup = install(
            root,
            args.config,
            args.report,
            json.loads(args.gates.read_text()),
            json.loads(args.baseline.read_text()),
        )
    if root == Path("/"):
        try:
            subprocess.run(
                [
                    "systemd-analyze",
                    "verify",
                    "--recursive-errors=no",
                    "systemd-hibernate.service",
                    "aag-hibernate-transaction-finish.service",
                    "aag-storage-sleep-v2.service",
                    "aag-sleep-transaction-boot.service",
                ],
                check=True,
                timeout=40,
            )
            subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=30)
        except BaseException:
            if not args.rollback:
                rollback(root, backup)
                subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=30)
            raise
        finally:
            for file in args.report.rglob("*"):
                if not file.is_symlink():
                    os.chown(file, 1000, 1000)
    print(
        json.dumps(
            {
                "status": "ROLLBACK_COMPLETE" if args.rollback else "CANDIDATE_INSTALLED",
                "rollback": str(backup),
                "power_actions": False,
            }
        )
    )


if __name__ == "__main__":
    main()
