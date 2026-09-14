"""Transaction rules shared by the production adapter and deterministic tests.

The runtime fence remains version 1 for existing backup-wrapper compatibility.
Neither command acceptance nor successful preparation is evidence of sleep.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

ACTIVE = {"PREPARING_SLEEP", "SLEEP_COMMITTED", "SUSPEND_REQUESTED", "VERIFYING_RESUME"}
TERMINAL = {"COMPLETE", "RECOVERED", "ABANDONED", "IDLE"}
MAX_RETRIES = 1
LEASE_SECONDS = 300
QUALIFICATION_CHECKS = {'sustained_hardware_sleep', 'residency_matches_cycle', 'lid_open_after_cycle'}
HEALTH_CHECKS = {'same_boot', 'exactly_one_completed_cycle', 'no_suspend_counter_failure',
    'one_kernel_entry_and_exit', 'candidate_held_through_capture', 'no_relevant_kernel_error',
    'data_unchanged', 'v2_files_unchanged', 'same_loaded_baseline',
    'cellular_reconnected_automatically', 'same_cellular_profile', 'saved_policy_restored',
    'native_service_success', 'same_systemd_invocation', 'journal_sequence_valid',
    'pmc_agrees_with_hardware_counter', 'data_still_healthy_after_reconnect', 'loaded_module_still_baseline'}


def device_health(result):
    checks = result.get('checks', {})
    return (HEALTH_CHECKS <= checks.keys() and all(value is True for key, value in checks.items()
            if key not in QUALIFICATION_CHECKS))


class Refusal(RuntimeError):
    pass


def new(boot: str, owner: str, *, now: float | None = None) -> dict[str, Any]:
    now = time.monotonic() if now is None else now
    return {
        "version": 1, "transaction_schema": 1, "boot_id": boot,
        "episode": uuid.uuid4().hex, "prep_invocation": owner,
        "state": "PREPARING_SLEEP", "stage": "REQUESTED",
        "created": time.time(), "started_monotonic": now, "episode_started_monotonic": now,
        "lease_until": now + LEASE_SECONDS, "retry_count": 0,
        "retry_state": "RETRY_AVAILABLE", "stopped_workloads": [],
        "storage_state": "MOUNTED", "actual_sleep": False,
        "kernel_cycle": False, "reason": None, "blockers": [],
    }


def fail(value: dict, stage: str, reason: str, blockers: list | None = None) -> dict:
    value.setdefault("first_failure", {"stage": stage, "reason": reason,
                                       "blockers": blockers or [], "time": time.time()})
    value.update(state="FAILURE_PENDING", stage=stage, reason=reason,
                 failed_at=time.time(), failure_origin=stage)
    if blockers is not None:
        value["blockers"] = blockers
    return value


def request_disposition(value: dict, boot: str, owner: str, live_owner: bool) -> str:
    if value.get("boot_id") != boot:
        return "BOOT_RECONCILE"
    if value.get("state") in TERMINAL:
        return "NEW"
    if live_owner:
        return "COALESCE"
    if value.get("state") == "RETRY_QUEUED":
        if value.get("retry_count") != MAX_RETRIES:
            raise Refusal("invalid transaction retry token")
        return "RETRY"
    # A timer is never proof that hardware is safe. The adapter must reconcile
    # storage, pending device policy and workload receipts before replacing it.
    return "RECONCILE"


def queue_retry(value: dict, now: float) -> None:
    if value.get("state") != "FAILURE_PENDING":
        raise Refusal("retry requires a failed transaction")
    if value.get("retry_count", 0) >= MAX_RETRIES:
        raise Refusal("retry consumed for this transaction")
    value.update(state="RETRY_QUEUED", retry_count=MAX_RETRIES,
                 retry_state="RETRY_CONSUMED", retry_queued_at=now)


def sleep_evidence(before: dict, after: dict, native_result: str) -> dict:
    success = after.get("success")
    baseline = before.get("success")
    cycle = isinstance(success, int) and isinstance(baseline, int) and success > baseline
    hw_before, hw_after = before.get("total_hw_sleep"), after.get("total_hw_sleep")
    residency = (hw_after - hw_before if isinstance(hw_before, int)
                 and isinstance(hw_after, int) and hw_after >= hw_before else None)
    elapsed = max(0, after.get("boottime", 0) - before.get("boottime", 0)
                  - after.get("monotonic", 0) + before.get("monotonic", 0))
    actual = cycle and residency is not None and residency > 0
    return {
        "kernel_cycle": cycle, "actual_sleep": actual,
        "hardware_sleep_us": residency, "suspended_seconds": elapsed,
        "native_result": native_result,
        "sleep_class": ("NO_KERNEL_SLEEP" if not cycle else
                        "LOW_POWER_CONFIRMED" if actual else "NO_LOW_POWER_RESIDENCY"),
        "immediate_wake": cycle and elapsed < 2,
    }


def safety_action(thermal: dict, battery: dict, *, warning_seconds: float = 0,
                  missing_seconds: float = 0, lid: str = "unknown") -> tuple[str, str]:
    if battery.get("critical") is True:
        return "poweroff", "critical-battery-discharge"
    if thermal.get("state") == "THERMAL_EMERGENCY":
        return "poweroff", "measured-critical-temperature"
    if thermal.get("state") == "THERMAL_WARNING" and warning_seconds >= 60:
        return "poweroff", "measured-hot-state-persisted"
    if thermal.get("state") == "UNKNOWN" and missing_seconds >= 300 and lid != "open":
        return "poweroff", "closed-lid-thermal-telemetry-lost-300s"
    return "monitor", "no-measured-safety-emergency"
