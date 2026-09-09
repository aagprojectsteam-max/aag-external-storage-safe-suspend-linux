from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = Path("/etc/aag-external-storage-safe-suspend/config.json")
HEX_ID = re.compile(r"^[0-9a-f]{4}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9._:+-]{2,256}$")


class ConfigError(RuntimeError):
    pass


def _absolute_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
        raise ConfigError(f"{field} must be an absolute path")
    if posixpath.normpath(value) != value:
        raise ConfigError(f"{field} must be normalized")
    return value


def _command(value: object, field: str) -> list[str]:
    if value in (None, []):
        return []
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item and "\x00" not in item for item in value)
    ):
        raise ConfigError(f"{field} must be an argv array")
    _absolute_path(value[0], field + "[0]")
    return list(value)


def validate(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ConfigError("configuration version must be 1")
    device = raw.get("device")
    if not isinstance(device, dict):
        raise ConfigError("device section is required")
    serial = device.get("serial")
    if not isinstance(serial, str) or not SAFE_ID.fullmatch(serial):
        raise ConfigError("device.serial is missing or unsafe")
    vendor = str(device.get("usb_vendor_id", "")).lower()
    product = str(device.get("usb_product_id", "")).lower()
    if (
        bool(vendor) != bool(product)
        or (vendor and not HEX_ID.fullmatch(vendor))
        or (product and not HEX_ID.fullmatch(product))
    ):
        raise ConfigError("USB vendor/product IDs must be a complete four-hex pair")
    uuids = device.get("filesystem_uuids")
    if (
        not isinstance(uuids, list)
        or not uuids
        or not all(isinstance(item, str) and SAFE_ID.fullmatch(item) for item in uuids)
    ):
        raise ConfigError("device.filesystem_uuids must be a non-empty safe string list")
    if len(uuids) != len(set(uuids)):
        raise ConfigError("device.filesystem_uuids contains duplicates")
    if len(uuids) > 64:
        raise ConfigError("device.filesystem_uuids is unexpectedly large")
    profile = device.get("profile", "generic-usb-storage")
    if not isinstance(profile, str) or not SAFE_ID.fullmatch(profile):
        raise ConfigError("device.profile is missing or unsafe")

    protected = raw.get("protected_mounts", [])
    if not isinstance(protected, list) or not protected:
        raise ConfigError("at least one protected mount is required")
    normalized_mounts: list[dict[str, str]] = []
    for index, item in enumerate(protected):
        if not isinstance(item, dict):
            raise ConfigError(f"protected_mounts[{index}] must be an object")
        path = _absolute_path(item.get("path"), f"protected_mounts[{index}].path")
        uuid = item.get("uuid")
        if not isinstance(uuid, str) or not SAFE_ID.fullmatch(uuid):
            raise ConfigError(f"protected_mounts[{index}].uuid is missing or unsafe")
        if uuid in uuids:
            raise ConfigError("a protected internal mount cannot belong to the external target")
        normalized_mounts.append({"path": path, "uuid": uuid})
    if len({item["path"] for item in normalized_mounts}) != len(normalized_mounts):
        raise ConfigError("protected mount paths must be unique")
    if len({item["uuid"] for item in normalized_mounts}) != len(normalized_mounts):
        raise ConfigError("protected mount UUIDs must be unique")

    backup = raw.get("managed_backup", {})
    if not isinstance(backup, dict):
        raise ConfigError("managed_backup must be an object")
    names = backup.get("process_names", ["timeshift", "timeshift-gtk"])
    if not isinstance(names, list) or not all(
        isinstance(name, str) and SAFE_ID.fullmatch(name) for name in names
    ):
        raise ConfigError("managed_backup.process_names must be safe process names")
    soft = int(backup.get("soft_wait_seconds", 120))
    stale = int(backup.get("progress_stale_seconds", 120))
    maximum = int(backup.get("maximum_wait_seconds", 900))
    if not (5 <= soft <= stale <= maximum <= 900):
        raise ConfigError("managed backup time bounds are not safely ordered")

    resume = raw.get("resume", {})
    if not isinstance(resume, dict):
        raise ConfigError("resume must be an object")
    enumeration = float(resume.get("enumeration_window_seconds", 15))
    audit = float(resume.get("audit_window_seconds", 15))
    if not (1 <= enumeration <= 120 and 1 <= audit <= 120):
        raise ConfigError("resume windows must be between 1 and 120 seconds")

    thermal = raw.get("thermal", {})
    if not isinstance(thermal, dict):
        raise ConfigError("thermal must be an object")
    action = thermal.get("emergency_action", "none")
    if action not in {"none", "poweroff"}:
        raise ConfigError("thermal.emergency_action must be none or poweroff")
    warning_grace = int(thermal.get("warning_grace_seconds", 60))
    unknown_ceiling = int(thermal.get("unknown_ceiling_seconds", 300))
    if not (10 <= warning_grace <= 600 and 30 <= unknown_ceiling <= 1800):
        raise ConfigError("thermal time bounds are unsafe")

    integration = raw.get("integration", {})
    if not isinstance(integration, dict):
        raise ConfigError("integration must be an object")

    return {
        "version": 1,
        "device": {
            "serial": serial,
            "usb_vendor_id": vendor,
            "usb_product_id": product,
            "filesystem_uuids": sorted(uuids),
            "profile": profile,
        },
        "protected_mounts": normalized_mounts,
        "managed_backup": {
            "process_names": sorted(set(names)),
            "quiesce_command": _command(
                backup.get("quiesce_command"), "managed_backup.quiesce_command"
            ),
            "soft_wait_seconds": soft,
            "progress_stale_seconds": stale,
            "maximum_wait_seconds": maximum,
        },
        "resume": {
            "enumeration_window_seconds": enumeration,
            "audit_window_seconds": audit,
            "poll_seconds": min(max(float(resume.get("poll_seconds", 0.2)), 0.05), 2.0),
        },
        "thermal": {
            "emergency_action": action,
            "warning_grace_seconds": warning_grace,
            "unknown_ceiling_seconds": unknown_ceiling,
        },
        "integration": {
            "pre_suspend_command": _command(
                integration.get("pre_suspend_command"), "integration.pre_suspend_command"
            ),
            "post_resume_command": _command(
                integration.get("post_resume_command"), "integration.post_resume_command"
            ),
        },
    }


def load(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError, TypeError) as exc:
        raise ConfigError(f"cannot read configuration: {exc}") from exc
    return validate(data)


def dump(value: dict[str, Any]) -> str:
    return json.dumps(validate(value), indent=2, sort_keys=True) + "\n"
