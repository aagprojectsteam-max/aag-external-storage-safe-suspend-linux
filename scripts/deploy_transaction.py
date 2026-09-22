#!/usr/bin/python3
"""Install the locally qualified transaction adapter, with exact-file rollback.

No suspend, reboot, workload stopping, mount changes, or firmware writes occur.
The only service started is the AAG boot reconciliation oneshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from aag_safe_suspend import production, workloads


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def mapping(config, locklock_patch=None):
    files = {REPO / "src/aag-sleep-transaction": Path("/usr/local/libexec/aag-sleep-transaction")}
    # Release version metadata must not replace the qualified reference package.
    files[REPO / "src/reference-transaction-init.py"] = Path(
        "/usr/local/lib/aag-sleep-transaction/aag_safe_suspend/__init__.py"
    )
    for name in ("production.py", "transaction.py", "workloads.py", "resume_observer.py"):
        files[REPO / "src/aag_safe_suspend" / name] = (
            Path("/usr/local/lib/aag-sleep-transaction/aag_safe_suspend") / name
        )
    for name, unit in [
        ("storage", "aag-storage-sleep-v2"),
        ("native", "systemd-suspend"),
        ("resume", "aag-t700-resume-check"),
        ("failure", "aag-suspend-failure-failsafe"),
        ("retry", "aag-sleep-recovery-retry"),
        ("touchpad", "aag-touchpad-resume-fix"),
    ]:
        files[REPO / "systemd/production" / (name + ".conf")] = (
            Path("/etc/systemd/system") / (unit + ".service.d") / "99-aag-transaction.conf"
        )
    files[REPO / "systemd/production/aag-sleep-transaction-boot.service"] = Path(
        "/etc/systemd/system/aag-sleep-transaction-boot.service"
    )
    files[config] = production.CONFIG
    files[REPO / "src/aag_safe_suspend/locklock_compat.py"] = Path(
        "/usr/lib/input-lock/aag_sleep_transaction_compat.py"
    )
    if locklock_patch:
        files[locklock_patch] = Path("/usr/lib/input-lock/input_lock_safety.py")
    return files


def prepare_locklock(
    report, path=Path("/usr/lib/input-lock/input_lock_safety.py"), verify_trust=True
):
    if verify_trust:
        production.trusted(path)
    source = path.read_text()
    for signature, module_function, argument in [
        ("def stack_ready(run=subprocess.run) -> bool:", "stack_ready", "run"),
        (
            "def aag_resume_confirmed(requested_at: float) -> bool:",
            "resume_confirmed",
            "requested_at",
        ),
    ]:
        added = (
            signature + "\n    # AAG transaction adapter contract; preserve legacy fallback.\n"
            '    if Path("/etc/aag-sleep-transaction/config.json").exists():\n'
            "        from aag_sleep_transaction_compat import "
            + module_function
            + " as adapter_contract\n"
            "        return adapter_contract(" + argument + ")\n"
        )
        if added in source:
            continue
        if source.count(signature) != 1:
            raise RuntimeError("LockLock interface changed; cannot apply narrow integration")
        source = source.replace(signature + "\n", added, 1)
    compile(source, str(path), "exec")
    generated = report / "input_lock_safety.integrated.py"
    generated.write_text(source)
    return generated


def cmd(args, check=True):
    p = subprocess.run(args, text=True, capture_output=True, timeout=60)
    if check and p.returncode:
        raise RuntimeError(p.stderr or p.stdout)
    return {"returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}


def rollback(manifest, backup):
    for row in reversed(manifest):
        p = Path(row["installed"])
        if row["existed"]:
            if row.get("symlink"):
                if p.exists() or p.is_symlink():
                    p.unlink()
                p.symlink_to(row["symlink"])
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / row["backup"], p)
            os.chown(p, row["uid"], row["gid"])
            os.chmod(p, row["mode"])
        elif p.exists():
            p.unlink()
    cmd(["systemctl", "daemon-reload"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("root required")
    report = args.report.resolve()
    report.mkdir(parents=True, exist_ok=True)
    if args.rollback:
        backup = args.rollback.resolve()
        manifest = json.loads((backup / "manifest.json").read_text())
        # Only files recorded by this installer can be restored. No directory deletion.
        expected = {str(p) for p in mapping(production.CONFIG).values()}
        expected.add(
            "/etc/systemd/system/multi-user.target.wants/aag-sleep-transaction-boot.service"
        )
        expected.add("/usr/lib/input-lock/input_lock_safety.py")
        if any(row["installed"] not in expected for row in manifest):
            raise RuntimeError("unexpected rollback path")
        rollback(manifest, backup)
        print("ROLLBACK_COMPLETE")
        return
    config = args.config.resolve()
    cfg = json.loads(config.read_text())
    workloads.validate_registry(cfg["workloads"])
    for unit in (
        production.V2,
        production.NATIVE,
        production.VERIFY,
        production.FAILURE,
        production.RETRY,
        "systemd-hibernate.service",
    ):
        state = production.properties(unit)
        if state["ActiveState"] not in ("inactive", "failed") or state.get(
            "Job", ""
        ).strip() not in ("", "0"):
            raise RuntimeError("active sleep/recovery transaction: " + unit)
    host = production.Host(cfg)
    evidence = {
        "ugreen": host.audit_storage(),
        "usbclone": host.clone_audit(host.clone_inventory()),
        "clones": host.clone_inventory(),
        "stats": production.stats(),
        "thermal": host.r.thermal_sample(),
        "battery": production.battery(),
        "lid": host.r.lid_state(),
        "lid_ignored": production.lid_ignored(),
        "t700_pending": host.t.PENDING.exists(),
        "t700_blocked": host.t.BLOCKED.exists(),
        "t700_queue": host.t.QUEUE.exists(),
    }
    (report / "pre-deployment-audit.json").write_text(json.dumps(evidence, indent=2))
    os.chown(report / "pre-deployment-audit.json", 1000, 1000)
    if args.check:
        print(
            json.dumps(
                {
                    k: v
                    for k, v in evidence.items()
                    if k not in {"ugreen", "usbclone", "clones", "thermal"}
                }
            )
        )
        print("READINESS_AUDIT_SAVED")
        return
    # Validate and snapshot before any installed file is changed.
    files = mapping(config, prepare_locklock(report))
    compat = Path("/usr/lib/input-lock/aag_sleep_transaction_compat.py")
    restart_locklock = compat.exists() and digest(compat) != digest(
        REPO / "src/aag_safe_suspend/locklock_compat.py"
    )
    if restart_locklock:
        controls = json.loads(cmd(["/usr/bin/input-lock", "status", "--json"])["stdout"])
        if (
            controls.get("ignore_lid_close")
            or controls.get("lid_is_closed")
            or controls.get("grabbed_devices")
        ):
            raise RuntimeError("LockLock controls are active; preserve them during deployment")
    backup = report / ("deployment-backup-" + str(time.time_ns()))
    backup.mkdir(mode=0o700)
    manifest = []
    for src, dst in files.items():
        row = {
            "source": str(src),
            "source_sha256": digest(src),
            "installed": str(dst),
            "existed": dst.exists(),
        }
        if dst.is_symlink():
            raise RuntimeError("unexpected destination symlink: " + str(dst))
        if dst.exists():
            st = dst.stat()
            target = backup / dst.relative_to("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, target)
            row.update(
                backup=str(dst.relative_to("/")),
                uid=st.st_uid,
                gid=st.st_gid,
                mode=st.st_mode & 0o777,
                prior_sha256=digest(dst),
            )
        manifest.append(row)
    # Enablement is recorded explicitly so rollback can remove only this link.
    link = Path("/etc/systemd/system/multi-user.target.wants/aag-sleep-transaction-boot.service")
    if link.exists() or link.is_symlink():
        if (
            not link.is_symlink()
            or str(link.readlink()) != "/etc/systemd/system/aag-sleep-transaction-boot.service"
        ):
            raise RuntimeError("unexpected boot reconciliation enablement")
        manifest.append({"installed": str(link), "existed": True, "symlink": str(link.readlink())})
    else:
        manifest.append({"installed": str(link), "existed": False})
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2))
    try:
        for src, dst in files.items():
            dst.parent.mkdir(parents=True, exist_ok=True)
            temp = dst.with_name(dst.name + ".aag-new")
            shutil.copyfile(src, temp)
            os.chown(temp, 0, 0)
            os.chmod(
                temp,
                0o600
                if dst == production.CONFIG
                else 0o755
                if dst.name == "aag-sleep-transaction"
                else 0o644,
            )
            os.replace(temp, dst)
        link.parent.mkdir(parents=True, exist_ok=True)
        if not link.exists():
            link.symlink_to("/etc/systemd/system/aag-sleep-transaction-boot.service")
        units = [
            production.V2,
            production.NATIVE,
            production.VERIFY,
            production.FAILURE,
            production.RETRY,
            "aag-sleep-transaction-boot.service",
        ]
        verified = cmd(["systemd-analyze", "verify", "--recursive-errors=no", *units])
        (report / "installed-unit-verification.json").write_text(json.dumps(verified, indent=2))
        cmd(["systemctl", "daemon-reload"])
        cmd(["systemctl", "start", "aag-sleep-transaction-boot.service"])
        if restart_locklock:
            cmd(["systemctl", "restart", "input-lock.service"])
        for row in manifest:
            if row.get("source"):
                row["installed_sha256"] = digest(Path(row["installed"]))
                if row["source_sha256"] != row["installed_sha256"]:
                    raise RuntimeError("source/installed mismatch")
        (report / "deployed-manifest.json").write_text(json.dumps(manifest, indent=2))
        (report / "installed-unit-contents.txt").write_text(
            cmd(["systemctl", "cat", *units])["stdout"]
        )
        (report / "installed-unit-properties.txt").write_text(
            cmd(["systemctl", "show", *units])["stdout"]
        )
        (report / "rollback-path.txt").write_text(str(backup) + "\n")
        print("INSTALLED_UPDATE=YES SOURCE_INSTALLED_HASH_MATCH=YES ROLLBACK=" + str(backup))
    except BaseException:
        rollback(manifest, backup)
        raise
    finally:
        for p in report.rglob("*"):
            with __import__("contextlib").suppress(OSError):
                os.chown(p, 1000, 1000)


if __name__ == "__main__":
    main()
