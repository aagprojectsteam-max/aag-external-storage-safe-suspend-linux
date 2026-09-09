from __future__ import annotations

import unittest
from unittest.mock import patch

from aag_safe_suspend import hibernate


class HibernateCapacityTests(unittest.TestCase):
    def memory(self) -> dict[str, int]:
        gib = 1024**3
        return {
            "MemTotal": 64 * gib,
            "MemAvailable": 48 * gib,
            "Unevictable": 1 * gib,
            "SUnreclaim": 1 * gib,
            "KernelStack": 128 * 1024**2,
            "PageTables": 256 * 1024**2,
            "SecPageTables": 0,
            "AnonPages": 12 * gib,
            "Shmem": 2 * gib,
        }

    def test_corrected_memory_model_passes_without_double_counting(self) -> None:
        gib = 1024**3
        result = hibernate.capacity_assessment(self.memory(), 32 * gib, 72 * gib)
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["resident_pressure_bytes"], 16 * gib)
        self.assertNotIn("AnonPages", result["accounting"])

    def test_insufficient_swap_and_image_policy_fail(self) -> None:
        gib = 1024**3
        result = hibernate.capacity_assessment(self.memory(), 8 * gib, 60 * gib)
        self.assertEqual(result["result"], "FAIL")
        self.assertFalse(result["checks"]["image_policy_covers_resident_pressure"])
        self.assertFalse(result["checks"]["swap_free_covers_ram_plus_ten_percent"])

    def test_parsers_validate_resume_inputs(self) -> None:
        cmdline = hibernate.parse_cmdline("quiet resume=UUID=TEST resume_offset=123")
        self.assertEqual(cmdline["resume"], ["UUID=TEST"])
        self.assertEqual(cmdline["resume_offset"], ["123"])
        self.assertEqual(
            hibernate.parse_filefrag_first_physical(" 0: 0.. 7: 4242.. 4249: 8:"), 4242
        )
        swaps = hibernate.parse_swaps(
            "Filename Type Size Used Priority\n/swap.img file 1024 128 -2\n"
        )
        self.assertEqual(swaps[0]["size_bytes"], 1024 * 1024)

    def test_active_swap_size_excludes_exactly_one_header_page(self) -> None:
        page = 4096
        self.assertTrue(hibernate.swap_area_matches_file(72 * 1024**3, 72 * 1024**3 - page, page))
        self.assertFalse(hibernate.swap_area_matches_file(72 * 1024**3, 72 * 1024**3, page))
        self.assertFalse(
            hibernate.swap_area_matches_file(72 * 1024**3, 72 * 1024**3 - 2 * page, page)
        )


class HibernatePolicyMatrixTests(unittest.TestCase):
    def assert_failure(self, field: str, reason: str) -> None:
        result = hibernate.simulate_policy({field: False})
        self.assertEqual(result["decision"], "REFUSE_OR_FAIL_CLOSED")
        self.assertIn(reason, result["failures"])

    def test_resume_device_offset_swap_and_initramfs_fail_closed(self) -> None:
        for field, reason in (
            ("swap_active", "MISSING_SWAP"),
            ("resume_device", "WRONG_RESUME_DEVICE"),
            ("resume_offset", "WRONG_RESUME_OFFSET"),
            ("swap_identity", "SWAPFILE_IDENTITY_MISMATCH"),
            ("initramfs", "INITRAMFS_RESUME_MISSING"),
            ("image_capacity", "IMAGE_CAPACITY_INSUFFICIENT"),
            ("memory_safety", "MEMORY_SAFETY_UNPROVEN"),
        ):
            with self.subTest(field=field):
                self.assert_failure(field, reason)

    def test_ugreen_owner_generation_automount_and_cold_boot(self) -> None:
        self.assertIn(
            "UGREEN_UNKNOWN_OR_RAW_OWNER",
            hibernate.simulate_policy({"ugreen_owner": "raw"})["failures"],
        )
        self.assertEqual(
            hibernate.simulate_policy({"ugreen_owner": "managed"})["ugreen_action"],
            "GRACEFUL_QUIESCE_THEN_REAUDIT",
        )
        self.assertIn(
            "UGREEN_TERMINAL_AUTOMOUNT",
            hibernate.simulate_policy({"automount_after": True})["failures"],
        )
        self.assertIn(
            "COLD_BOOT_NOT_IMAGE_RESUME",
            hibernate.simulate_policy({"cold_boot": True})["failures"],
        )
        self.assertIn(
            "T700_GENERATION_UNSTABLE",
            hibernate.simulate_policy({"t700_stable": False})["failures"],
        )

    def test_clean_matrix_passes_and_preserves_suspend_graph(self) -> None:
        result = hibernate.simulate_policy({})
        self.assertEqual(result["decision"], "PASS")
        self.assertFalse(result["ordinary_suspend_graph_changed"])


class T700RecoveryTests(unittest.TestCase):
    def test_stable_generation_delays_exactly_one_helper_invocation(self) -> None:
        result = hibernate.simulate_wwan_recovery(
            generation_ready_at=35, settle_seconds=110, stable_seconds=10, deadline=140
        )
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["helper_invocations"], 1)
        self.assertEqual(result["invoked_at"], 119)

    def test_missing_stable_generation_never_invokes_helper(self) -> None:
        result = hibernate.simulate_wwan_recovery(
            generation_ready_at=None, settle_seconds=110, stable_seconds=10, deadline=140
        )
        self.assertEqual(result["result"], "FAIL")
        self.assertEqual(result["helper_invocations"], 0)

    def test_late_modemmanager_and_networkmanager_are_polled(self) -> None:
        settings = {
            "enabled": True,
            "settle_seconds": 110,
            "stable_seconds": 10,
            "stability_timeout_seconds": 30,
            "recovery_timeout_seconds": 100,
            "modem_timeout_seconds": 35,
            "connect_timeout_seconds": 15,
            "recovery_command": [],
        }
        calls: list[list[str]] = []
        polls = {"count": 0}

        def fake_run(argv: list[str], **_kwargs):
            calls.append(argv)
            if argv[0].endswith("mmcli"):
                polls["count"] += 1
                stdout = "/Modem/0\n" if polls["count"] >= 3 else "No modems\n"
            elif argv[0].endswith("nmcli"):
                stdout = "gsm:connected\n" if polls["count"] >= 3 else "gsm:disconnected\n"
            else:
                stdout = ""
            return type("Result", (), {"stdout": stdout, "returncode": 0})()

        with (
            patch.object(
                hibernate.configuration, "load", return_value={"hibernate": {"t700": settings}}
            ),
            patch.object(
                hibernate,
                "_load_episode",
                return_value={"status": "NATIVE_RETURNED", "boot_id": "B"},
            ),
            patch.object(hibernate, "boot_id", return_value="B"),
            patch.object(hibernate, "_t700_identity", return_value=object()),
            patch.object(hibernate, "_ports_ready", return_value=True),
            patch.object(hibernate, "_run", side_effect=fake_run),
        ):
            self.assertEqual(hibernate.wwan_recover(sleep=lambda _seconds: None), 0)
        self.assertEqual(sum(argv[:2] == ["/usr/bin/systemctl", "restart"] for argv in calls), 1)
        self.assertGreaterEqual(polls["count"], 3)


if __name__ == "__main__":
    unittest.main()
