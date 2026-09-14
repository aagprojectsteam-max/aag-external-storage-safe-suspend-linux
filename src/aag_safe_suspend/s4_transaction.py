"""Hibernate lifecycle and recovery rules, using the existing power ledger.

Phase observations are evidence, not instructions to the kernel. In particular,
POWERED_OFF is inferred only after a verified S4 image round trip.
"""

from __future__ import annotations

import re
import time

from .transaction import Refusal

TYPE = "HIBERNATE"
OWNER = "aag-hibernate-transaction-finish.service"
PHASES = (
    "REQUESTED",
    "PREPARING",
    "BLOCKERS_HANDLED",
    "STORAGE_SAFE",
    "IMAGE_WRITE_REQUESTED",
    "HIBERNATE_ENTRY",
    "POWERED_OFF",
    "BOOT_RESUME_DETECTED",
    "IMAGE_RESUME_CONFIRMED",
    "RESTORING",
    "COMPLETE",
)
FAILURES = {
    "PREP_FAILED",
    "IMAGE_WRITE_FAILED",
    "COLD_BOOT_FALLBACK",
    "RESTORE_FAILED",
    "ABORTED",
    "REBOOT_AFTER_RESUME_FAILURE",
}
GIB = 1024**3


def reboot_outcome(value):
    # A later reboot cannot turn an observed native return into an image-load
    # failure, or turn unsuccessful restoration into a fully accepted cycle.
    if value.get("image_resume_confirmed") or (
        value.get("s4_native_result") == "success" and value.get("s4_native_returned")
    ):
        return "REBOOT_AFTER_RESUME_FAILURE"
    return "COLD_BOOT_FALLBACK"


def attach(value, before):
    value.update(
        transaction_type=TYPE,
        origin_boot_id=value["boot_id"],
        s4_phase="REQUESTED",
        s4_history=[],
        final_outcome=None,
        preparation_state="REQUESTED",
        blocker_state="UNASSESSED",
        data_state="UNASSESSED",
        ugreen_state="UNASSESSED",
        consumer_state="UNASSESSED",
        wwan_state="SAVED",
        wwan_recovery_owner=OWNER,
        s4_before=before,
        image_resume_confirmed=False,
    )
    observe(value, "REQUESTED")


def observe(value, phase, **evidence):
    if phase not in PHASES and phase not in FAILURES:
        raise Refusal("unknown S4 phase")
    if value.get("final_outcome"):
        raise Refusal("terminal S4 transaction cannot be relabeled")
    prior = value.get("s4_phase")
    if (
        phase in PHASES
        and phase != "RESTORING"
        and prior in PHASES
        and PHASES.index(phase) < PHASES.index(prior)
    ):
        raise Refusal("S4 phase cannot move backwards")
    value["s4_phase"] = phase
    value.setdefault("s4_history", []).append(
        {"phase": phase, "time": time.time(), "evidence": evidence}
    )


def image_evidence(value, boot, invocation, result, text, sessions_match):
    patterns = {
        "entry": r"PM: hibernation: hibernation entry",
        "snapshot": r"PM: hibernation: Allocated \d+ pages for snapshot",
        "s4_prepare": r"ACPI: PM: Preparing to enter system sleep state S4",
        "s4_wake": r"ACPI: PM: Waking up from system sleep state S4",
        "location_consumed": r"efivarfs: removing variable HibernateLocation-",
        "exit": r"PM: hibernation: hibernation exit",
        "native_return": r"System returned from sleep operation ['\"]hibernate['\"]",
    }
    checks = {k: re.search(p, text) is not None for k, p in patterns.items()}
    checks.update(
        same_boot=boot == value.get("origin_boot_id"),
        invocation=bool(invocation) and invocation == value.get("native_invocation"),
        native_success=result == "success",
        sessions_match=sessions_match is True,
        requested=value.get("s4_phase") == "IMAGE_WRITE_REQUESTED"
        or any(x.get("phase") == "IMAGE_WRITE_REQUESTED" for x in value.get("s4_history", [])),
        single_entry=len(re.findall(patterns["entry"], text)) == 1,
        single_exit=len(re.findall(patterns["exit"], text)) == 1,
        no_fs_errors=not re.search(
            r"EXT4-fs (?:error|warning|abort)|JBD2:.*(?:error|abort)|"
            r"Buffer I/O error",
            text,
            re.I,
        ),
    )
    return {"confirmed": all(checks.values()), "checks": checks}


def memory_gate(memory, image, swap_free):
    total, available = memory.get("MemTotal", 0), memory.get("MemAvailable", 0)
    floor = sum(
        memory.get(k, 0)
        for k in ("Unevictable", "SUnreclaim", "KernelStack", "PageTables", "SecPageTables")
    )
    checks = {
        "valid_measurement": 0 < available <= total,
        "image_policy_covers_resident_pressure": image >= total - available,
        "image_policy_covers_nonreclaimable_floor_plus_2gib": image >= floor + 2 * GIB,
        "available_memory_allows_snapshot_workspace": available >= image + 4 * GIB,
        "swap_free_covers_ram_plus_ten_percent": swap_free
        >= max(int(total * 1.10), int(image * 1.25)),
        "image_size_nonzero": image > 0,
    }
    return {
        "result": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "mem_total_bytes": total,
        "mem_available_bytes": available,
        "image_size_bytes": image,
        "swap_free_bytes": swap_free,
    }


def restore_consumers(rows, receipts, run, save):
    """Resume durable intents without repeating a completed start.

    The accepted preparation receipts are reused. Detect before starting even
    for STOP_REQUESTED/STOP_FAILED: the checkpoint may have completed just as
    its command or its parent was interrupted. Only declared restart policy
    authorizes restart; unknown/manual processes are never reconstructed.
    """
    registry = {row["name"]: row for row in rows}
    for receipt in receipts:
        if receipt.get("status") in {"RESTORED", "LEFT_STOPPED_BY_POLICY"}:
            continue
        if receipt.get("restart_after_resume") == "never":
            receipt["status"] = "LEFT_STOPPED_BY_POLICY"
            save()
            continue
        if receipt.get("was_running") is not True:
            raise Refusal("consumer receipt lacks original running identity")
        row = registry.get(receipt.get("name"))
        if (
            not row
            or row.get("restart_after_resume") != "if_was_running"
            or not row.get("restart_command")
        ):
            raise Refusal("consumer restore policy disappeared")
        detected = run(row, row["detect"], 15).get("active")
        if detected is None:
            raise Refusal("consumer restore detection incomplete")
        if detected is False:
            receipt["status"] = "RESTORE_REQUESTED"
            save()
            run(row, row["restart_command"], row["stop_timeout"])
            if run(row, row["detect"], 15).get("active") is not True:
                raise Refusal("consumer restoration unconfirmed")
        receipt["status"] = "RESTORED"
        save()


def recover_wwan(
    before,
    sample,
    recover,
    save,
    receipt,
    *,
    clock=time.monotonic,
    sleep=time.sleep,
    settle=110,
    timeout=240,
):
    """One generation-qualified recovery attempt; never reset/rescan hardware.

    The durable attempt token is consumed before the command. A crashed owner
    may only observe the result on re-entry, never repeat a modem restart.
    """
    deadline = clock() + timeout
    ready_at = clock() + settle
    fingerprint = None
    stable_since = None
    observations = []
    next_save = clock()
    while clock() < deadline:
        current = sample()
        now = clock()
        observations.append({"time": now, "sample": current})
        if now >= next_save:
            receipt.update(status="OBSERVING", observations=observations)
            save()
            next_save = now + 5
        token = current.get("generation") if current.get("hardware_ok") else None
        if not token or token != fingerprint:
            fingerprint = token
            stable_since = now if token else None
        if token and stable_since is not None and now >= ready_at and now - stable_since >= 10:
            healthy = current.get("modem_present") is True
            if before.get("healthy"):
                healthy = (
                    healthy
                    and current.get("healthy") is True
                    and (current.get("connection_uuid") == before.get("connection_uuid"))
                )
            if healthy:
                receipt.update(status="RESTORED", generation=token, observations=observations)
                save()
                return current
            if not receipt.get("attempt_consumed"):
                receipt.update(
                    attempt_consumed=True,
                    status="RECOVERY_REQUESTED",
                    generation=token,
                    observations=observations,
                )
                save()
                # The host checks the same identity immediately before each action.
                try:
                    recover(before, token)
                except Exception as exc:
                    receipt.update(status="RESTORE_FAILED", error=str(exc))
                    save()
                    raise
                stable_since = None
                fingerprint = None
        sleep(1)
    receipt.update(status="RESTORE_FAILED", observations=observations)
    save()
    raise Refusal("FM350/T700 stable generation or restored WWAN not confirmed")
