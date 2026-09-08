from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from aag_safe_suspend import audit, config, coordinator

HERE = Path(__file__).parent


def generation(name: str = "sdz", diskseq: str = "11") -> dict:
    token = {
        "serial": "TEST-NVME-0001",
        "usb_vendor_id": "0bda",
        "usb_product_id": "9210",
        "diskseq": diskseq,
        "disk_devno": "65:144",
        "disk_sysfs": f"/sys/class/block/{name}",
        "partitions": [
            {
                "name": name + "1",
                "uuid": "TEST-FS-A",
                "devno": "65:145",
                "action_path": "/dev/" + name + "1",
            },
            {
                "name": name + "2",
                "uuid": "TEST-FS-B",
                "devno": "65:146",
                "action_path": "/dev/" + name + "2",
            },
        ],
        "fingerprint": "generation-" + diskseq,
    }
    return {"present": True, "complete": True, "token": token}


class ResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = config.load(HERE / "fixtures/config.json")

    @patch("aag_safe_suspend.coordinator.protected_mounts_healthy", return_value=(True, []))
    @patch("aag_safe_suspend.coordinator._audit_until")
    @patch("aag_safe_suspend.coordinator.identity.identify")
    def test_reenumerates_before_terminal(self, identify, audit_until, _healthy) -> None:
        current = generation()
        identify.return_value = current
        audit_until.return_value = ({"AUDIT_COMPLETE": True, "EXTERNAL_OWNER": "NONE"}, [])
        value = coordinator._terminal_resume(self.config)
        self.assertEqual(value["terminal"], "TARGET_PRESENT_ALL_FILESYSTEMS_UNMOUNTED")

    @patch("aag_safe_suspend.coordinator.time.sleep", return_value=None)
    @patch("aag_safe_suspend.coordinator.time.monotonic", side_effect=[0, 0, 1, 2, 16])
    @patch("aag_safe_suspend.coordinator.protected_mounts_healthy", return_value=(True, []))
    @patch("aag_safe_suspend.coordinator.identity.identify")
    def test_target_never_reappears_is_safe_after_bounded_window(
        self, identify, _healthy, _clock, _sleep
    ) -> None:
        identify.return_value = {"present": False, "complete": True}
        value = coordinator._terminal_resume(self.config)
        self.assertEqual(value["terminal"], "TARGET_ABSENT_AFTER_BOUNDED_WINDOW")

    @patch("aag_safe_suspend.coordinator.protected_mounts_healthy", return_value=(True, []))
    @patch("aag_safe_suspend.coordinator.audit.audit_three")
    @patch("aag_safe_suspend.coordinator.identity.identify")
    def test_genuine_writer_after_automount_fails_closed(
        self, identify, audit_three, _healthy
    ) -> None:
        identify.return_value = generation()
        audit_three.return_value = {
            "AUDIT_COMPLETE": True,
            "EXTERNAL_OWNER": "HOST",
            "semantic_stable_scan_2_to_3": True,
            "records": [{"kind": "fd", "owner": "HOST", "pid": 9, "start": "10", "dev": "65:145"}],
        }
        with self.assertRaises(coordinator.Refusal):
            coordinator._terminal_resume(self.config)

    @patch("aag_safe_suspend.coordinator.protected_mounts_healthy", return_value=(True, []))
    @patch("aag_safe_suspend.coordinator.audit.audit_three")
    @patch("aag_safe_suspend.coordinator.identity.identify")
    def test_generation_change_during_audit_fails_closed(
        self, identify, audit_three, _healthy
    ) -> None:
        identify.side_effect = [
            generation(diskseq="11"),
            generation(diskseq="11"),
            generation(diskseq="12"),
        ]
        audit_three.return_value = {
            "AUDIT_COMPLETE": True,
            "EXTERNAL_OWNER": "NONE",
            "semantic_stable_scan_2_to_3": True,
            "records": [],
        }
        with self.assertRaises(coordinator.Refusal):
            coordinator._terminal_resume(self.config)

    def test_changed_devname_does_not_define_identity(self) -> None:
        first = generation("sdz", "11")
        second = generation("sdc", "11")
        self.assertEqual(
            coordinator._generation_fingerprint(first), coordinator._generation_fingerprint(second)
        )

    def test_all_partitions_automounted_are_clean_unmount_candidates(self) -> None:
        mounts = [
            {
                "kind": "mount",
                "owner": "HOST",
                "namespace": "host",
                "dev": "65:145",
                "target": "/media/test/a",
                "propagation": {"shared": "7"},
            },
            {
                "kind": "mount",
                "owner": "HOST",
                "namespace": "host",
                "dev": "65:146",
                "target": "/media/test/b",
                "propagation": {"shared": "8"},
            },
        ]
        value = {
            "AUDIT_COMPLETE": True,
            "EXTERNAL_OWNER": "MOUNT_ONLY",
            "records": mounts,
            "semantic_stable_scan_2_to_3": True,
        }
        self.assertEqual(audit.disposition(value, allow_mounts=True)[0], "UNMOUNT")

    def test_one_partition_automounted_is_clean_unmount_candidate(self) -> None:
        value = {
            "AUDIT_COMPLETE": True,
            "EXTERNAL_OWNER": "MOUNT_ONLY",
            "records": [
                {
                    "kind": "mount",
                    "owner": "HOST",
                    "namespace": "host",
                    "dev": "65:145",
                    "target": "/media/test/a",
                    "propagation": {"shared": "7"},
                }
            ],
            "semantic_stable_scan_2_to_3": True,
        }
        self.assertEqual(audit.disposition(value, allow_mounts=True)[0], "UNMOUNT")

    def test_automount_during_terminal_audit_retries(self) -> None:
        value = {
            "AUDIT_COMPLETE": True,
            "EXTERNAL_OWNER": "MOUNT_ONLY",
            "records": [],
            "semantic_stable_scan_2_to_3": False,
        }
        self.assertEqual(audit.disposition(value, allow_mounts=True)[0], "RETRY")

    @patch("aag_safe_suspend.coordinator.time.monotonic", side_effect=[0, 1, 2, 3, 4, 5])
    @patch("aag_safe_suspend.coordinator.clean_unmount", return_value=[{"result": "clean"}])
    @patch("aag_safe_suspend.coordinator.audit.audit_three")
    @patch("aag_safe_suspend.coordinator.identity.identify")
    def test_repeated_harmless_automount_is_removed_then_reaudited(
        self, identify, audit_three, clean_unmount, _clock
    ) -> None:
        current = generation()
        identify.return_value = current
        mounted = {
            "AUDIT_COMPLETE": True,
            "EXTERNAL_OWNER": "MOUNT_ONLY",
            "semantic_stable_scan_2_to_3": True,
            "records": [
                {
                    "kind": "mount",
                    "owner": "HOST",
                    "namespace": "host",
                    "dev": "65:145",
                    "target": "/media/test/a",
                    "propagation": {"shared": "7"},
                }
            ],
        }
        clear = {
            "AUDIT_COMPLETE": True,
            "EXTERNAL_OWNER": "NONE",
            "semantic_stable_scan_2_to_3": True,
            "records": [],
        }
        audit_three.side_effect = [mounted, mounted, clear]
        value, actions = coordinator._audit_until(self.config, current, True, deadline=5)
        self.assertEqual(value["EXTERNAL_OWNER"], "NONE")
        self.assertEqual(clean_unmount.call_count, 2)
        self.assertEqual(len(actions), 2)


if __name__ == "__main__":
    unittest.main()
