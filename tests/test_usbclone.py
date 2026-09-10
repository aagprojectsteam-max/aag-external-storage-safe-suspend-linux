from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aag_safe_suspend import usbclone


class USBCloneGateTests(unittest.TestCase):
    def roots(self, temporary: str) -> tuple[Path, Path, Path]:
        root = Path(temporary)
        gadgets = root / "usb_gadget"
        usb = root / "usb_devices"
        gadgets.mkdir()
        usb.mkdir()
        return root, gadgets, usb

    def add_mass_storage(self, gadgets: Path, name: str = "usbclone_fixture") -> Path:
        gadget = gadgets / name
        function = gadget / "functions/mass_storage.0"
        (function / "lun.0").mkdir(parents=True)
        (gadget / "configs/c.1").mkdir(parents=True)
        (gadget / "UDC").write_text("dummy_udc.0\n")
        (function / "lun.0/file").write_text("/synthetic/disk.img\n")
        (gadget / "configs/c.1/mass_storage.0").symlink_to(function)
        return gadget

    def test_loaded_dummy_hcd_with_empty_controllers_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _root, gadgets, usb = self.roots(temporary)
            observed = usbclone.inventory(gadgets, usb)
        self.assertEqual(usbclone.decision(observed), (True, "NO_BOUND_VIRTUAL_USB_DEVICE"))

    def test_bound_usbclone_mass_storage_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _root, gadgets, usb = self.roots(temporary)
            self.add_mass_storage(gadgets)
            observed = usbclone.inventory(gadgets, usb)
        self.assertEqual(
            usbclone.decision(observed),
            (False, "ACTIVE_USBCLONE_MASS_STORAGE_GADGET"),
        )

    def test_active_winboat_qemu_owner_cannot_trigger_hot_unplug(self) -> None:
        observed = {
            "bound_configfs_gadgets": [
                {
                    "project_owned": True,
                    "mass_storage": True,
                    "guest_owners": ["qemu-system-x86_64"],
                }
            ],
            "dummy_hcd_usb_devices": [],
        }
        self.assertFalse(usbclone.decision(observed)[0])
        source = Path(usbclone.__file__).read_text()
        self.assertNotIn("subprocess", source)
        self.assertNotIn(".unlink(", source)
        self.assertNotIn("/var/lib/aag-t700-auto", source)
        self.assertNotIn("blocked.json", source)

    def test_unmanaged_or_orphan_virtual_device_fails_closed(self) -> None:
        unmanaged = {
            "bound_configfs_gadgets": [{"project_owned": False, "mass_storage": True}],
            "dummy_hcd_usb_devices": [],
        }
        orphan = {
            "bound_configfs_gadgets": [],
            "dummy_hcd_usb_devices": [{"sysname": "9-1"}],
        }
        self.assertEqual(usbclone.decision(unmanaged)[1], "ACTIVE_UNMANAGED_CONFIGFS_GADGET")
        self.assertEqual(
            usbclone.decision(orphan)[1],
            "ACTIVE_DUMMY_HCD_DEVICE_WITHOUT_CONFIGFS_OWNER",
        )

    def test_retry_can_pass_after_condition_is_safely_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _root, gadgets, usb = self.roots(temporary)
            gadget = self.add_mass_storage(gadgets)
            self.assertFalse(usbclone.decision(usbclone.inventory(gadgets, usb))[0])
            # This represents an external, owner-verified shutdown.  The gate
            # itself never performs this write or any USB detach operation.
            (gadget / "UDC").write_text("\n")
            self.assertTrue(usbclone.decision(usbclone.inventory(gadgets, usb))[0])

    def test_existing_t700_review_latch_is_never_acknowledged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            latch = Path(temporary) / "blocked.json"
            original = b'{"status":"review-required","fixture":true}\n'
            latch.write_bytes(original)
            _root, gadgets, usb = self.roots(temporary)

            self.assertTrue(usbclone.decision(usbclone.inventory(gadgets, usb))[0])
            self.assertEqual(latch.read_bytes(), original)

        source = Path(usbclone.__file__).read_text()
        self.assertNotIn("aag-t700-auto", source)
        self.assertNotIn("blocked.json", source)


if __name__ == "__main__":
    unittest.main()
