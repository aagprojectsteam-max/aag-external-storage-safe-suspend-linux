from __future__ import annotations

import contextlib
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aag_safe_suspend import config, coordinator, thermal


class PolicyTests(unittest.TestCase):
    def test_progress_aware_wait_has_no_universal_short_deadline(self) -> None:
        policy = {
            "soft_wait_seconds": 120,
            "progress_stale_seconds": 120,
            "maximum_wait_seconds": 900,
        }
        self.assertEqual(coordinator.progress_classification(30, 30, policy), "INITIAL_GRACE")
        self.assertEqual(coordinator.progress_classification(300, 10, policy), "PROGRESS_EXTENSION")
        self.assertEqual(
            coordinator.progress_classification(300, 130, policy), "STALLED_FAIL_CLOSED"
        )

    def test_invalid_timeout_order_is_rejected(self) -> None:
        raw = config.load(Path(__file__).parent / "fixtures/config.json")
        raw["managed_backup"]["maximum_wait_seconds"] = 50
        with self.assertRaises(config.ConfigError):
            config.validate(raw)

    def test_configured_awake_window_cannot_exceed_unit_budget(self) -> None:
        raw = config.load(Path(__file__).parent / "fixtures/config.json")
        raw["managed_backup"]["maximum_wait_seconds"] = 901
        with self.assertRaises(config.ConfigError):
            config.validate(raw)

    def test_thermal_normal_warning_emergency_and_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            zone = root / "class/thermal/thermal_zone0"
            zone.mkdir(parents=True)
            (zone / "type").write_text("chassis\n")
            (zone / "trip_point_0_type").write_text("hot\n")
            (zone / "trip_point_0_temp").write_text("75000\n")
            (zone / "trip_point_1_type").write_text("critical\n")
            (zone / "trip_point_1_temp").write_text("85000\n")
            (zone / "temp").write_text("45000\n")
            self.assertEqual(thermal.sample(root)["state"], "NORMAL")
            (zone / "temp").write_text("76000\n")
            self.assertEqual(thermal.sample(root)["state"], "WARNING")
            (zone / "temp").write_text("86000\n")
            self.assertEqual(thermal.sample(root)["state"], "EMERGENCY")
            for path in zone.iterdir():
                path.unlink()
            zone.rmdir()
            self.assertEqual(thermal.sample(root)["state"], "UNKNOWN")

    def test_temperature_without_a_threshold_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            hwmon = root / "class/hwmon/hwmon0"
            hwmon.mkdir(parents=True)
            (hwmon / "name").write_text("nvme\n")
            (hwmon / "temp1_input").write_text("45000\n")
            self.assertEqual(thermal.sample(root)["state"], "UNKNOWN")

    def test_source_has_no_generic_kill_or_force_unmount(self) -> None:
        source = (Path(__file__).parents[1] / "src/aag_safe_suspend/coordinator.py").read_text()
        self.assertNotIn("kill -9", source)
        self.assertNotIn("SIGKILL", source)
        self.assertNotIn("--lazy", source)
        self.assertNotIn("--force", source)
        self.assertIn('["/usr/bin/umount", "--", item["target"]]', source)

    @patch("aag_safe_suspend.coordinator.require_root", return_value=None)
    @patch("aag_safe_suspend.coordinator.state.lock", return_value=contextlib.nullcontext())
    @patch("aag_safe_suspend.coordinator.state.load", return_value={"state": "IDLE"})
    @patch("aag_safe_suspend.coordinator.thermal.sample")
    def test_idle_failure_recovery_returns_without_waiting(
        self, sample, _load, _lock, _root
    ) -> None:
        with patch(
            "aag_safe_suspend.coordinator.load_config",
            return_value=config.load(Path(__file__).parent / "fixtures/config.json"),
        ):
            self.assertEqual(coordinator.recover(), 0)
        sample.assert_not_called()

    @patch("aag_safe_suspend.coordinator.require_root", return_value=None)
    @patch("aag_safe_suspend.coordinator.state.lock", return_value=contextlib.nullcontext())
    @patch("aag_safe_suspend.coordinator.state.load", return_value={"state": "BLOCKED"})
    @patch("aag_safe_suspend.coordinator._lid_state", return_value="closed")
    @patch(
        "aag_safe_suspend.coordinator.thermal.sample",
        return_value={"state": "EMERGENCY", "reasons": ["synthetic"]},
    )
    @patch("aag_safe_suspend.coordinator.time.sleep", return_value=None)
    @patch("aag_safe_suspend.coordinator.time.monotonic", side_effect=[0, 1])
    @patch("aag_safe_suspend.coordinator._run")
    def test_default_emergency_policy_issues_no_power_action(
        self, run, _clock, _sleep, _thermal, _lid, _load, _lock, _root
    ) -> None:
        value = config.load(Path(__file__).parent / "fixtures/config.json")
        with patch("aag_safe_suspend.coordinator.load_config", return_value=value):
            self.assertEqual(coordinator.recover(), 1)
        run.assert_not_called()

    @patch("aag_safe_suspend.coordinator.require_root", return_value=None)
    @patch("aag_safe_suspend.coordinator.state.lock", return_value=contextlib.nullcontext())
    @patch("aag_safe_suspend.coordinator.state.load", return_value={"state": "BLOCKED"})
    @patch("aag_safe_suspend.coordinator._lid_state", return_value="closed")
    @patch(
        "aag_safe_suspend.coordinator.thermal.sample",
        return_value={"state": "EMERGENCY", "reasons": ["synthetic"]},
    )
    @patch("aag_safe_suspend.coordinator.time.sleep", return_value=None)
    @patch("aag_safe_suspend.coordinator.time.monotonic", side_effect=[0, 1])
    @patch("aag_safe_suspend.coordinator._run")
    def test_opt_in_emergency_poweroff_requires_closed_lid_recheck(
        self, run, _clock, _sleep, _thermal, lid, _load, _lock, _root
    ) -> None:
        value = copy.deepcopy(config.load(Path(__file__).parent / "fixtures/config.json"))
        value["thermal"]["emergency_action"] = "poweroff"
        with patch("aag_safe_suspend.coordinator.load_config", return_value=value):
            self.assertEqual(coordinator.recover(), 0)
        self.assertEqual(lid.call_count, 4)
        run.assert_called_once_with(
            ["/usr/bin/systemctl", "--no-block", "--check-inhibitors=no", "poweroff"],
            timeout=8,
            required=True,
        )


if __name__ == "__main__":
    unittest.main()
