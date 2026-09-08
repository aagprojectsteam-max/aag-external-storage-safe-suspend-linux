from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


class IdentityError(RuntimeError):
    pass


def _run(argv: list[str], timeout: float = 8) -> str:
    try:
        result = subprocess.run(
            argv,
            text=True,
            capture_output=True,
            check=True,
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise IdentityError(f"command failed: {argv[0]}: {exc}") from exc
    return result.stdout


def flatten(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        result.append(item)
        result.extend(flatten(item.get("children") or []))
    return result


def lsblk_values(source: str | None = None) -> list[dict[str, Any]]:
    raw = source or _run(
        [
            "/usr/bin/lsblk",
            "-J",
            "-b",
            "-o",
            "NAME,KNAME,PATH,TYPE,PKNAME,SERIAL,MODEL,SIZE,FSTYPE,LABEL,UUID,MAJ:MIN,MOUNTPOINTS,TRAN",
        ]
    )
    try:
        return flatten(json.loads(raw).get("blockdevices", []))
    except (TypeError, ValueError) as exc:
        raise IdentityError(f"invalid lsblk output: {exc}") from exc


def udev_properties(sysfs_path: Path) -> dict[str, str]:
    raw = _run(
        [
            "/usr/bin/udevadm",
            "info",
            "--query=property",
            f"--path={sysfs_path}",
        ],
        timeout=5,
    )
    result: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def _safe_handle(value: str, kind: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._+:-]+", value):
        raise IdentityError(f"unsafe {kind} action handle")
    return value


def identify(config: dict[str, Any], source: str | None = None) -> dict[str, Any]:
    """Resolve stable identity to a current kernel generation.

    Kernel names are returned only after the disk serial, bridge IDs and exact
    filesystem UUID set have matched. They are never selectors.
    """
    wanted = config["device"]
    values = lsblk_values(source)
    disks = [
        row
        for row in values
        if row.get("type") == "disk" and str(row.get("serial") or "").strip() == wanted["serial"]
    ]
    if not disks:
        return {"present": False, "complete": True}
    if len(disks) != 1:
        return {"present": True, "complete": False, "gap": "disk-identity-not-unique"}
    disk = disks[0]
    disk_name = _safe_handle(str(disk.get("name") or ""), "disk")
    disk_devno = str(disk.get("maj:min") or "")
    if not re.fullmatch(r"[0-9]+:[0-9]+", disk_devno):
        return {"present": True, "complete": False, "gap": "disk-devno-invalid"}
    parts = [row for row in values if row.get("pkname") == disk_name]
    actual = {str(row.get("uuid")) for row in parts if row.get("uuid")}
    expected = set(wanted["filesystem_uuids"])
    if actual != expected:
        return {
            "present": True,
            "complete": False,
            "gap": "partition-identity-set-mismatch",
            "actual_uuids": sorted(actual),
        }

    test_mode = os.environ.get("AAG_SAFE_SUSPEND_TEST_MODE") == "1"
    if test_mode:
        diskseq = str(disk.get("diskseq") or "1")
        disk_sysfs = f"/sys/class/block/{disk_name}"
    else:
        try:
            disk_sysfs_path = Path("/sys/dev/block", disk_devno).resolve(strict=True)
            class_path = Path("/sys/class/block", disk_name).resolve(strict=True)
            if disk_sysfs_path != class_path:
                raise IdentityError("disk sysfs handles disagree")
            diskseq = Path("/sys/class/block", disk_name, "diskseq").read_text().strip()
            if not re.fullmatch(r"[1-9][0-9]*", diskseq):
                raise IdentityError("invalid disk sequence")
            props = udev_properties(disk_sysfs_path)
            expected_props = {
                "DEVTYPE": "disk",
                "DISKSEQ": diskseq,
                "ID_SERIAL_SHORT": wanted["serial"],
            }
            if wanted["usb_vendor_id"]:
                expected_props.update(
                    {
                        "ID_USB_VENDOR_ID": wanted["usb_vendor_id"],
                        "ID_USB_MODEL_ID": wanted["usb_product_id"],
                    }
                )
            if any(props.get(key) != value for key, value in expected_props.items()):
                raise IdentityError("disk udev stable identity mismatch")
            disk_sysfs = str(disk_sysfs_path)
        except (OSError, IdentityError) as exc:
            return {
                "present": True,
                "complete": False,
                "gap": f"generation-resolution:{type(exc).__name__}:{exc}",
            }

    partitions: list[dict[str, Any]] = []
    for part in parts:
        name = _safe_handle(str(part.get("name") or ""), "partition")
        devno = str(part.get("maj:min") or "")
        uuid = str(part.get("uuid") or "")
        path = str(part.get("path") or "")
        if not re.fullmatch(r"[0-9]+:[0-9]+", devno) or uuid not in expected:
            return {"present": True, "complete": False, "gap": "partition-handle-invalid"}
        if not path.startswith("/dev/") or Path(path).name != name:
            return {"present": True, "complete": False, "gap": "partition-path-not-canonical"}
        sysfs = f"/sys/class/block/{name}"
        if not test_mode:
            try:
                sysfs_path = Path("/sys/dev/block", devno).resolve(strict=True)
                class_part = Path("/sys/class/block", name).resolve(strict=True)
                if sysfs_path != class_part or sysfs_path.parent != Path(disk_sysfs):
                    raise IdentityError("partition not attached to resolved disk generation")
                props = udev_properties(sysfs_path)
                expected_props = {
                    "DEVTYPE": "partition",
                    "DISKSEQ": diskseq,
                    "ID_SERIAL_SHORT": wanted["serial"],
                    "ID_FS_UUID": uuid,
                }
                if wanted["usb_vendor_id"]:
                    expected_props.update(
                        {
                            "ID_USB_VENDOR_ID": wanted["usb_vendor_id"],
                            "ID_USB_MODEL_ID": wanted["usb_product_id"],
                        }
                    )
                if any(props.get(key) != value for key, value in expected_props.items()):
                    raise IdentityError("partition udev stable identity mismatch")
                sysfs = str(sysfs_path)
            except (OSError, IdentityError) as exc:
                return {
                    "present": True,
                    "complete": False,
                    "gap": f"partition-generation-resolution:{type(exc).__name__}:{exc}",
                }
        partitions.append(
            {
                "name": name,
                "uuid": uuid,
                "devno": devno,
                "sysfs": sysfs,
                "action_path": path,
                "mountpoints": list(part.get("mountpoints") or []),
                "fstype": part.get("fstype"),
            }
        )

    partitions.sort(key=lambda row: row["uuid"])
    token = {
        "serial": wanted["serial"],
        "usb_vendor_id": wanted["usb_vendor_id"],
        "usb_product_id": wanted["usb_product_id"],
        "diskseq": diskseq,
        "disk_devno": disk_devno,
        "disk_sysfs": disk_sysfs,
        "partitions": partitions,
    }
    token["fingerprint"] = hashlib.sha256(
        json.dumps(token, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {"present": True, "complete": True, "token": token}


def discover(device: str, protected_mounts: list[str]) -> dict[str, Any]:
    resolved = str(Path(device).resolve(strict=True))
    values = lsblk_values()
    selected = next((row for row in values if row.get("path") == resolved), None)
    if not selected:
        raise IdentityError("selected block device is absent from lsblk")
    if selected.get("type") == "part":
        selected = next((row for row in values if row.get("name") == selected.get("pkname")), None)
    if not selected or selected.get("type") != "disk":
        raise IdentityError("selection does not resolve to exactly one whole disk")
    if selected.get("tran") != "usb":
        raise IdentityError("selected disk is not reported as USB-attached")
    serial = str(selected.get("serial") or "").strip()
    if not serial:
        raise IdentityError("selected disk has no stable underlying serial")
    matching_disks = [
        row
        for row in values
        if row.get("type") == "disk" and str(row.get("serial") or "").strip() == serial
    ]
    if len(matching_disks) != 1:
        raise IdentityError("selected disk serial is not unique among current whole disks")
    parts = [row for row in values if row.get("pkname") == selected.get("name")]
    uuids = sorted({str(row.get("uuid")) for row in parts if row.get("uuid")})
    if not uuids or len(uuids) != len(parts):
        raise IdentityError("every selected-disk partition must have a unique filesystem UUID")
    sysfs = Path("/sys/class/block", str(selected["name"])).resolve(strict=True)
    props = udev_properties(sysfs)
    vendor = str(props.get("ID_USB_VENDOR_ID", "")).lower()
    product = str(props.get("ID_USB_MODEL_ID", "")).lower()
    if not re.fullmatch(r"[0-9a-f]{4}", vendor) or not re.fullmatch(r"[0-9a-f]{4}", product):
        raise IdentityError("USB bridge vendor/product identity is unavailable")

    protected: list[dict[str, str]] = []
    for mount in protected_mounts:
        raw = _run(
            [
                "/usr/bin/findmnt",
                "-J",
                "-M",
                mount,
                "-o",
                "TARGET,SOURCE,UUID,FSTYPE,OPTIONS",
            ]
        )
        rows = json.loads(raw).get("filesystems") or []
        if len(rows) != 1 or not rows[0].get("uuid"):
            raise IdentityError(f"protected mount is absent or has no UUID: {mount}")
        if rows[0]["uuid"] in uuids:
            raise IdentityError("selected external disk contains a protected mount")
        protected.append({"path": mount, "uuid": rows[0]["uuid"]})

    return {
        "version": 1,
        "device": {
            "serial": serial,
            "usb_vendor_id": vendor,
            "usb_product_id": product,
            "filesystem_uuids": uuids,
            "profile": "rtl9210"
            if (vendor, product) == ("0bda", "9210")
            else "generic-usb-storage",
        },
        "protected_mounts": protected,
        "managed_backup": {
            "process_names": ["timeshift", "timeshift-gtk", "timeshift.real", "timeshift-gtk.real"],
            "quiesce_command": [],
            "soft_wait_seconds": 120,
            "progress_stale_seconds": 120,
            "maximum_wait_seconds": 900,
        },
        "resume": {
            "enumeration_window_seconds": 15,
            "audit_window_seconds": 15,
            "poll_seconds": 0.2,
        },
        "thermal": {
            "emergency_action": "none",
            "warning_grace_seconds": 60,
            "unknown_ceiling_seconds": 300,
        },
        "integration": {"pre_suspend_command": [], "post_resume_command": []},
    }


def udev_rule(config: dict[str, Any], marker: str) -> str:
    device = config["device"]
    rules = ["# Generated by aag-external-storage-safe-suspend. Do not hand-edit."]
    for filesystem_uuid in device["filesystem_uuids"]:
        clauses = [
            'ACTION=="add|change"',
            'SUBSYSTEM=="block"',
            'ENV{DEVTYPE}=="partition"',
            f'ENV{{ID_SERIAL_SHORT}}=="{device["serial"]}"',
        ]
        if device["usb_vendor_id"]:
            clauses.extend(
                [
                    f'ENV{{ID_USB_VENDOR_ID}}=="{device["usb_vendor_id"]}"',
                    f'ENV{{ID_USB_MODEL_ID}}=="{device["usb_product_id"]}"',
                ]
            )
        clauses.extend(
            [
                f'ENV{{ID_FS_UUID}}=="{filesystem_uuid}"',
                f'TEST=="{marker}"',
                'ENV{UDISKS_AUTO}="0"',
                'ENV{AAG_SAFE_SUSPEND_TARGET}="1"',
            ]
        )
        rules.append(", ".join(clauses))
    return "\n".join(rules) + "\n"
