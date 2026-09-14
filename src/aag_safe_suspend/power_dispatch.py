"""Route only S4 to its specialization; exec unchanged v1.3.1 for Suspend."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from . import production as p
from . import s4_checks as checks
from . import s4_transaction as s4
from . import transaction as tx
from .s4_host import FINISH, NATIVE, PERMIT, Host
from .transaction import Refusal

LOCK = Path("/run/lock/aag-power-transaction.lock")


def current():
    try:
        return json.loads((p.STATE / "current.json").read_text())
    except FileNotFoundError:
        return {}


def s4_active(value):
    return value.get("transaction_type") == s4.TYPE and value.get("state") not in tx.TERMINAL


def hibernate_job():
    state = p.properties(NATIVE)
    return state.get("Job", "0") not in {"", "0"} or state.get("ActiveState") in {
        "activating",
        "active",
    }


def route(action, value, requested_s4=False):
    if action.startswith("hibernate-"):
        return "S4"
    if action == "begin":
        return "S4" if requested_s4 else "RECONCILE_THEN_SUSPEND" if s4_active(value) else "SUSPEND"
    if action in {
        "verify-release",
        "v2-teardown",
        "failure-recover",
        "boot-reconcile",
        "retry-suspend",
    } and s4_active(value):
        return "S4"
    return "SUSPEND"


def ordinary(action, argv):
    os.execv(p.HELPER, [p.HELPER, action, *argv])


def wait_for_teardown(timeout=30, *, clock=time.monotonic, sleep=time.sleep):
    """Wait outside the operation lock so V2's late stop callback can finish.

    After= orders jobs that are already queued. Native OnSuccess may start us
    before StopWhenUnneeded even queues V2's stop job. Holding the lock while
    waiting would prevent that callback from completing.
    """
    deadline = clock() + timeout
    while True:
        states = {
            unit: p.properties(unit)
            for unit in (NATIVE, p.V2, "aag-sleep-transaction-boot.service")
        }
        if all(
            state.get("ActiveState") in {"inactive", "failed"} and state.get("Job") in {"", "0"}
            for state in states.values()
        ):
            return
        if clock() >= deadline:
            raise Refusal("S4 teardown did not finish before recovery deadline: " + str(states))
        sleep(0.1)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Shared units spell out the exact existing delegate command. LockLock's
    # contract can still inspect it, and ordinary execution uses that argv.
    if argv and argv[0] == p.HELPER:
        argv = argv[1:]
    if os.geteuid() != 0 or not argv:
        raise Refusal("power transaction actions require root and an action")
    action, rest = argv[0], argv[1:]
    value = current()
    selection = route(action, value, hibernate_job() if action == "begin" else False)
    if selection == "SUSPEND":
        return ordinary(action, rest)
    if action in {"failure-recover", "retry-suspend"}:
        # Queueing the one systemd owner is safe even while that owner holds
        # the operation lock. Do not turn lock contention into service retries.
        p.run(["/usr/bin/systemctl", "--no-block", "start", FINISH], timeout=5)
        return 0
    if selection == "RECONCILE_THEN_SUSPEND":
        p.run(["/usr/bin/systemctl", "--no-block", "start", FINISH], timeout=5)
        raise Refusal("prior S4 recovery queued to its owner; wait for reconciliation")
    if action == "hibernate-finish":
        if not s4_active(value):
            return 0
        wait_for_teardown()
    # A single process owns all S4 restore side effects, including cold boot.
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise Refusal("S4 transaction operation already owned; no duplicate recovery") from exc
    try:
        host = Host()
        actions = {
            "begin": host.begin,
            "verify-release": host.verify_release,
            "v2-teardown": host.teardown,
            "boot-reconcile": host.boot,
            "hibernate-native-pre": host.native_pre,
            "hibernate-native-post": host.native_post,
            "hibernate-finish": host.finish,
        }
        if action in actions:
            actions[action]()
        elif action == "hibernate-readiness":
            result = checks.readiness(host, host.s4_cfg)
            print(json.dumps(result, sort_keys=True))
            return 0 if result["result"] == "PASS" else 1
        elif action == "hibernate-once":
            if s4_active(value) or value.get("state", "IDLE") not in tx.TERMINAL:
                raise Refusal("an unresolved power transaction exists")
            if hibernate_job() or p.properties(p.NATIVE).get("Job") not in {"", "0"}:
                raise Refusal("a native power transition already exists")
            if PERMIT.exists():
                prior = json.loads(PERMIT.read_text())
                if (
                    prior.get("boot_id") == host.r.boot_id()
                    and not prior.get("consumed")
                    and prior.get("expires", 0) > time.time()
                ):
                    raise Refusal("one S4 dispatch is already reserved")
            result = checks.readiness(host, host.s4_cfg)
            if result["result"] != "PASS":
                raise Refusal("NO_GO: " + json.dumps(result, sort_keys=True))
            if host.r.lid_state() != "open":
                raise Refusal("Hibernate acceptance requires the lid open")
            thermal = host.r.thermal_sample()
            if thermal.get("state") != "NORMAL" or p.battery().get("critical"):
                raise Refusal("thermal/battery state does not permit the physical test")
            host.persist(
                PERMIT,
                {
                    "go": "PASS",
                    "boot_id": host.r.boot_id(),
                    "expires": time.time() + 600,
                    "consumed": False,
                    "config_sha256": hashlib.sha256(checks.CONFIG.read_bytes()).hexdigest(),
                    "gate": result,
                    "before_dispatch": host.snapshot(),
                },
            )
            # Evidence and permit are durable before the only native dispatch.
            host.persist(p.STATE / "s4-evidence" / ("go-" + str(time.time_ns()) + ".json"), result)
            p.run(["/usr/bin/journalctl", "--sync"], timeout=10)
            # V2 may start before systemctl returns. Its begin action must be
            # able to acquire the operation lock and consume the durable permit.
            os.close(fd)
            fd = -1
            try:
                p.run(["/usr/bin/systemctl", "--no-block", "hibernate"], timeout=15)
            except Exception:
                # A refused dispatcher cannot leave an unused permission for a
                # later unrelated native request. Never modify a consumed one.
                with LOCK.open("r+") as cleanup:
                    try:
                        fcntl.flock(cleanup, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        pass  # A live begin/restore now owns the reservation.
                    else:
                        reservation = json.loads(PERMIT.read_text())
                        if not reservation.get("consumed"):
                            reservation.update(consumed=True, go="DISPATCH_FAILED", expires=0)
                            host.persist(PERMIT, reservation)
                raise
            print("HIBERNATE_REQUESTED_ONCE; native transaction owns evidence")
        else:
            raise Refusal("unknown S4 action")
    finally:
        if fd >= 0:
            os.close(fd)
    return 0
