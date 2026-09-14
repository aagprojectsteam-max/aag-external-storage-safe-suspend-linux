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

import deploy_hibernate

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.4.0"


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


def previous_release_acceptance(asset: Path, previous: Path, directory: Path) -> None:
    """Use actual verified previous-release bytes, not a reconstructed fixture."""
    previous_copy = directory / "previous-release.run"
    shutil.copyfile(previous, previous_copy)
    previous_copy.chmod(0o755)
    if sha256(previous_copy) != sha256(previous):
        raise RuntimeError("previous-release working copy differs from preserved asset")
    previous = previous_copy
    root = directory / "public-v1.3.1-root"
    root.mkdir()
    run([str(previous), "verify-source"])
    common = ["--root", str(root), "--test-mode"]
    run([str(previous), *common, "install", "--config", str(ROOT / "tests/fixtures/config.json")])
    state_path = root / "var/lib/aag-external-storage-safe-suspend/install-state.json"
    before = json.loads(state_path.read_text())
    if before["installed_version"] != "1.3.1":
        raise RuntimeError("previous asset is not the accepted v1.3.1 baseline")
    originals = {
        name: (sha256(root / name.lstrip("/")), (root / name.lstrip("/")).stat().st_mode & 0o777)
        for name in before["files"]
    }
    upgraded = run([str(asset), *common, "install"])
    if f'"installed_version": "{VERSION}"' not in upgraded.stdout:
        raise RuntimeError("actual previous-release upgrade failed")
    env = {**os.environ, "AAG_SAFE_SUSPEND_ROOT": str(root), "AAG_SAFE_SUSPEND_TEST_MODE": "1"}
    cli = root / "usr/local/bin/aag-safe-suspend"
    result = subprocess.run(
        [str(cli), "rollback"], env=env, capture_output=True, text=True, timeout=60
    )
    if result.returncode or '"to_version": "1.3.1"' not in result.stdout:
        raise RuntimeError("actual previous-release rollback failed")
    restored = json.loads(state_path.read_text())
    if restored["installed_version"] != "1.3.1":
        raise RuntimeError("rollback did not restore previous version")
    for name, expected in originals.items():
        path = root / name.lstrip("/")
        if (sha256(path), path.stat().st_mode & 0o777) != expected:
            raise RuntimeError("rollback did not restore exact previous bytes/modes: " + name)
    version = subprocess.run(
        [str(cli), "--version"], env=env, capture_output=True, text=True, timeout=30
    )
    if version.returncode or version.stdout.strip() != "1.3.1":
        raise RuntimeError("rolled-back CLI did not execute previous version")
    run([str(previous), *common, "uninstall"])


def hibernate_package_acceptance(asset: Path, root: Path, directory: Path) -> None:
    """Validate the packaged S4 route on the installed synthetic reference stack."""
    baseline = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            baseline.append(
                {"installed": "/" + str(path.relative_to(root)), "expected": sha256(path)}
            )
    baseline_path = directory / "s4-baseline.json"
    baseline_path.write_text(json.dumps(baseline))
    gates = {key: "PASS" for key in deploy_hibernate.REQUIRED_GATES}
    gates["ROLLBACK_READY"] = "YES"
    gates_path = directory / "s4-gates.json"
    gates_path.write_text(json.dumps(gates))
    config = directory / "s4-config.json"
    config.write_text(
        json.dumps(
            {
                "simulated_gates": {key: "PASS" for key in deploy_hibernate.REQUIRED_GATES},
                "pinned_files": {
                    str(dst): sha256(src)
                    for src, dst in deploy_hibernate.mapping(config)
                    if src != config
                },
            }
        )
    )
    old = root / "etc/systemd/system/aag-hibernate-wwan-recovery.service"
    old.write_text("synthetic retired owner rollback fixture\n")
    old.chmod(0o640)
    common = [
        str(asset),
        "hibernate",
        "--root",
        str(root),
        "--test-mode",
        "--report",
        str(directory / "s4-report"),
    ]
    receipt = json.loads(
        run(
            common
            + [
                "--config",
                str(config),
                "--gates",
                str(gates_path),
                "--baseline",
                str(baseline_path),
            ]
        ).stdout
    )
    if receipt["power_actions"] or receipt["status"] != "CANDIDATE_INSTALLED":
        raise RuntimeError("S4 packaged deployment did not complete without power actions")
    for source, logical in deploy_hibernate.mapping(config):
        if sha256(root / logical.relative_to("/")) != sha256(source):
            raise RuntimeError("packaged S4 runtime differs from qualified source")
    # Reinstall/rollback must preserve the already-qualified S4 payload exactly.
    upgrade = json.loads(
        run(
            common
            + [
                "--config",
                str(config),
                "--gates",
                str(gates_path),
                "--baseline",
                str(baseline_path),
            ]
        ).stdout
    )
    run(common + ["--rollback", upgrade["rollback"]])
    for source, logical in deploy_hibernate.mapping(config):
        if sha256(root / logical.relative_to("/")) != sha256(source):
            raise RuntimeError("S4 update rollback changed the preceding payload")
    run(common + ["--rollback", receipt["rollback"]])
    for row in baseline:
        if sha256(root / row["installed"].lstrip("/")) != row["expected"]:
            raise RuntimeError("S4 rollback changed the accepted reference baseline")
    if (
        old.read_text() != "synthetic retired owner rollback fixture\n"
        or old.stat().st_mode & 0o777 != 0o640
    ):
        raise RuntimeError("S4 rollback did not restore the old owner bytes/mode")
    if (root / "usr/local/libexec/aag-power-transaction").exists():
        raise RuntimeError("S4 rollback left the added dispatcher")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("asset", type=Path)
    parser.add_argument("--previous-asset", type=Path)
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
        if '"installed_version": "1.4.0"' not in upgraded.stdout:
            raise RuntimeError("v1.1.1 fixture did not upgrade to v1.4.0")
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
            raise RuntimeError("v1.4.0 rollback did not restore v1.1.1")
        run(upgrade_common + ["uninstall"])

        # Exercise the patch-release boundary separately.  This sanitized
        # managed-state fixture reproduces the v1.2.0 wiring and absence of
        # the new ordinary-only unit; independent post-publication validation
        # uses the real downloaded v1.2.0 asset.
        patch_root = Path(temporary) / "v1.2.0-upgrade-root"
        (patch_root / "usr/bin").mkdir(parents=True)
        shutil.copy2("/bin/true", patch_root / "usr/bin/timeshift")
        shutil.copy2("/bin/true", patch_root / "usr/bin/timeshift-gtk")
        patch_common = [str(asset), "--root", str(patch_root), "--test-mode"]
        run(
            patch_common
            + ["install", "--config", str(ROOT / "tests/fixtures/config.json"), "--timeshift"]
        )
        patch_state_path = (
            patch_root / "var/lib/aag-external-storage-safe-suspend/install-state.json"
        )
        patch_state = json.loads(patch_state_path.read_text())
        old_dropin = (
            b"[Unit]\n"
            b"Requires=aag-external-storage-safe-suspend.service\n"
            b"After=aag-external-storage-safe-suspend.service\n"
            b"Wants=aag-external-storage-safe-suspend-resume.service\n"
            b"Before=aag-external-storage-safe-suspend-resume.service\n"
            b"OnFailure=aag-external-storage-safe-suspend-failure.service\n"
        )
        dropin_logical = (
            "/etc/systemd/system/systemd-suspend.service.d/"
            "70-aag-external-storage-safe-suspend.conf"
        )
        dropin_path = patch_root / dropin_logical.removeprefix("/")
        dropin_path.write_bytes(old_dropin)
        patch_state["files"][dropin_logical]["sha256"] = sha256(dropin_path)
        common_service_logical = "/etc/systemd/system/aag-external-storage-safe-suspend.service"
        common_service_path = patch_root / common_service_logical.removeprefix("/")
        common_service_path.write_text(
            common_service_path.read_text().replace(
                "Before=sleep.target", "Before=systemd-suspend.service sleep.target"
            )
        )
        patch_state["files"][common_service_logical]["sha256"] = sha256(common_service_path)
        failure_service_logical = (
            "/etc/systemd/system/aag-external-storage-safe-suspend-failure.service"
        )
        failure_service_path = patch_root / failure_service_logical.removeprefix("/")
        failure_service_path.write_text(
            failure_service_path.read_text().replace(
                "After=systemd-suspend.service "
                "aag-external-storage-safe-ordinary-suspend.service "
                "aag-external-storage-safe-suspend.service",
                "After=systemd-suspend.service aag-external-storage-safe-suspend.service",
            )
        )
        patch_state["files"][failure_service_logical]["sha256"] = sha256(failure_service_path)
        new_paths = (
            "/etc/systemd/system/aag-external-storage-safe-ordinary-suspend.service",
            "/usr/lib/aag-external-storage-safe-suspend/aag_safe_suspend/usbclone.py",
            "/usr/lib/aag-external-storage-safe-suspend/aag_safe_suspend/systemd_graph.py",
        )
        for logical in new_paths:
            (patch_root / logical.removeprefix("/")).unlink()
            del patch_state["files"][logical]
        init_logical = "/usr/lib/aag-external-storage-safe-suspend/aag_safe_suspend/__init__.py"
        init_path = patch_root / init_logical.removeprefix("/")
        init_path.write_text(init_path.read_text().replace('"1.4.0"', '"1.2.0"'))
        patch_state["files"][init_logical]["sha256"] = sha256(init_path)
        patch_state.update(
            installed_version="1.2.0",
            migration_schema=2,
            systemd_wiring_revision=2,
        )
        patch_state["migrations_applied"] = [
            item
            for item in patch_state["migrations_applied"]
            if item != "wiring-revision-2-to-3-ordinary-usbclone-gate"
        ]
        patch_state["release"]["tag"] = "v1.2.0"
        patch_state_path.write_text(json.dumps(patch_state, sort_keys=True, indent=2) + "\n")
        patch_state_path.chmod(0o600)
        patch_upgrade = run(patch_common + ["install"])
        if '"installed_version": "1.4.0"' not in patch_upgrade.stdout:
            raise RuntimeError("v1.2.0 fixture did not upgrade to v1.4.0")
        if not (patch_root / new_paths[0].removeprefix("/")).is_file():
            raise RuntimeError("v1.2.0 upgrade omitted the ordinary USBClone unit")
        patch_env = {
            **os.environ,
            "AAG_SAFE_SUSPEND_ROOT": str(patch_root),
            "AAG_SAFE_SUSPEND_TEST_MODE": "1",
        }
        patch_rollback = subprocess.run(
            [str(patch_root / "usr/local/bin/aag-safe-suspend"), "rollback"],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
            env=patch_env,
        )
        if patch_rollback.returncode or '"to_version": "1.2.0"' not in patch_rollback.stdout:
            raise RuntimeError("v1.4.0 rollback did not restore v1.2.0")
        if dropin_path.read_bytes() != old_dropin:
            raise RuntimeError("v1.2.0 ordinary graph bytes were not restored")
        if (patch_root / new_paths[0].removeprefix("/")).exists():
            raise RuntimeError("v1.2.0 rollback retained the v1.4.0 ordinary unit")
        run(patch_common + ["uninstall"])

        # Exercise the packaged reference-adapter dispatch, not just module tests.
        reference_root = Path(temporary) / "reference-root"
        safety = reference_root / "usr/lib/input-lock/input_lock_safety.py"
        safety.parent.mkdir(parents=True)
        original_safety = (
            "import subprocess\nfrom pathlib import Path\n"
            "def stack_ready(run=subprocess.run) -> bool:\n    return False\n"
            "def aag_resume_confirmed(requested_at: float) -> bool:\n    return False\n"
        )
        safety.write_text(original_safety)
        synthetic_config = Path(temporary) / "reference-config.json"
        synthetic_config.write_text('{"workloads": []}\n')
        reference_common = [
            str(asset),
            "transaction",
            "--root",
            str(reference_root),
            "--test-mode",
            "--report",
            str(Path(temporary) / "reference-report"),
        ]
        reference = json.loads(run(reference_common + ["--config", str(synthetic_config)]).stdout)
        if reference["status"] != "INSTALLED" or reference["files"] != 15:
            raise RuntimeError("packaged transaction mapping was incomplete")
        runtime = (
            reference_root / "usr/local/lib/aag-sleep-transaction/aag_safe_suspend/production.py"
        )
        if sha256(runtime) != sha256(ROOT / "src/aag_safe_suspend/production.py"):
            raise RuntimeError("packaged transaction runtime differs from qualified source")
        reference_init = (
            reference_root / "usr/local/lib/aag-sleep-transaction/aag_safe_suspend/__init__.py"
        )
        if sha256(reference_init) != sha256(ROOT / "src/reference-transaction-init.py"):
            raise RuntimeError("release metadata replaced the qualified reference package")
        hibernate_package_acceptance(asset, reference_root, Path(temporary))
        restored = json.loads(run(reference_common + ["--rollback", reference["rollback"]]).stdout)
        if restored["status"] != "ROLLBACK_COMPLETE" or safety.read_text() != original_safety:
            raise RuntimeError("packaged transaction rollback did not restore fixture")
        if (reference_root / "usr/local/libexec/aag-sleep-transaction").exists():
            raise RuntimeError("packaged transaction rollback left the new helper")

        corrupt = Path(temporary) / "corrupt.run"
        data = bytearray(asset.read_bytes())
        data[-1] ^= 1
        corrupt.write_bytes(data)
        corrupt.chmod(0o755)
        refused = run([str(corrupt), "verify-source"], expected=1)
        if "checksum mismatch" not in refused.stderr.lower():
            raise RuntimeError("corrupt release payload did not fail at its checksum")

        if args.previous_asset:
            previous_release_acceptance(asset, args.previous_asset.resolve(), Path(temporary))

    print("SELF_EXTRACT_CHECKSUM=PASS")
    print("RELEASE_INSTALLER_TEST=PASS")
    print("RELEASE_ROLLBACK_TEST=PASS")
    print("UPGRADE_1_1_1_TO_1_4_0=PASS")
    print("UPGRADE_1_2_0_TO_1_4_0=PASS")
    print("REFERENCE_TRANSACTION_PAYLOAD_AND_ROLLBACK=PASS")
    print("REFERENCE_HIBERNATE_PAYLOAD_AND_ROLLBACK=PASS")
    if args.previous_asset:
        print("ACTUAL_PUBLIC_V1_3_1_UPGRADE_AND_ROLLBACK=PASS")
    print("POWER_STATE_ACTIONS=NONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
