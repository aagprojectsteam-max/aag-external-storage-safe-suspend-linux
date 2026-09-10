"""Ordinary-suspend preflight for ConfigFS devices on dummy-HCD controllers.

The accepted regression involved bound USB mass-storage gadgets.  Merely loading
``dummy_hcd`` and leaving its controllers empty is safe and must not be treated
as the same condition.  This module is deliberately read-only: it never writes
``UDC``, removes a ConfigFS object, detaches USB, or signals a VM.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIGFS_GADGETS = Path("/sys/kernel/config/usb_gadget")
USB_DEVICES = Path("/sys/bus/usb/devices")
TEST_MODE = os.environ.get("AAG_SAFE_SUSPEND_TEST_MODE") == "1"


class Refusal(RuntimeError):
    pass


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return ""


def inventory(
    gadget_root: Path = CONFIGFS_GADGETS,
    usb_root: Path = USB_DEVICES,
) -> dict[str, Any]:
    """Return a privacy-minimized inventory of active virtual USB devices."""
    bound: list[dict[str, Any]] = []
    if gadget_root.is_dir():
        for gadget in sorted(gadget_root.iterdir(), key=lambda item: item.name):
            if not gadget.is_dir() or gadget.is_symlink():
                continue
            udc = _read(gadget / "UDC")
            if not udc:
                continue
            functions: list[str] = []
            configs = gadget / "configs"
            if configs.is_dir():
                for item in sorted(configs.glob("*/*")):
                    if item.is_symlink():
                        functions.append(item.name)
            mass_storage = any(name.startswith("mass_storage.") for name in functions)
            mass_storage = mass_storage or any(gadget.glob("functions/mass_storage.*/lun.*/file"))
            bound.append(
                {
                    "name": gadget.name,
                    "udc": udc,
                    "project_owned": gadget.name.startswith("usbclone_"),
                    "functions": functions,
                    "mass_storage": mass_storage,
                }
            )

    dummy_devices: list[dict[str, str]] = []
    if usb_root.is_dir():
        for item in sorted(usb_root.iterdir(), key=lambda entry: entry.name):
            # Root hubs are named usbN; a device has an N-M[.M...] sysname.
            if "-" not in item.name or not (item / "idVendor").exists():
                continue
            try:
                resolved = str(item.resolve())
            except OSError:
                continue
            if "/dummy_hcd." not in resolved:
                continue
            dummy_devices.append({"sysname": item.name})
    return {
        "bound_configfs_gadgets": bound,
        "dummy_hcd_usb_devices": dummy_devices,
    }


def decision(observed: dict[str, Any]) -> tuple[bool, str]:
    bound = observed.get("bound_configfs_gadgets")
    devices = observed.get("dummy_hcd_usb_devices")
    if not isinstance(bound, list) or not isinstance(devices, list):
        return False, "USBCLONE_INVENTORY_INCOMPLETE"
    if any(not isinstance(item, dict) for item in [*bound, *devices]):
        return False, "USBCLONE_INVENTORY_INCOMPLETE"
    if any(not item.get("project_owned") for item in bound):
        return False, "ACTIVE_UNMANAGED_CONFIGFS_GADGET"
    if any(item.get("mass_storage") for item in bound):
        return False, "ACTIVE_USBCLONE_MASS_STORAGE_GADGET"
    if bound:
        # Non-mass-storage bound functions are outside the physically proven
        # condition.  They remain fail-closed because ownership and residency
        # are not proven safe; they are not reported as the known root cause.
        return False, "ACTIVE_CONFIGFS_GADGET_OUTSIDE_PROVEN_SCOPE"
    if devices:
        return False, "ACTIVE_DUMMY_HCD_DEVICE_WITHOUT_CONFIGFS_OWNER"
    return True, "NO_BOUND_VIRTUAL_USB_DEVICE"


def preflight(
    gadget_root: Path = CONFIGFS_GADGETS,
    usb_root: Path = USB_DEVICES,
) -> int:
    if not TEST_MODE and os.geteuid() != 0:
        raise Refusal("ordinary USBClone preflight requires root")
    observed = inventory(gadget_root, usb_root)
    allowed, reason = decision(observed)
    summary = {
        "decision": "PASS" if allowed else "REFUSE",
        "reason": reason,
        "bound_configfs_gadgets": len(observed["bound_configfs_gadgets"]),
        "dummy_hcd_usb_devices": len(observed["dummy_hcd_usb_devices"]),
        "power_state_actions": "none",
        "usb_detach_actions": "none",
    }
    print(json.dumps(summary, sort_keys=True), flush=True)
    if not allowed:
        raise Refusal(reason)
    return 0
