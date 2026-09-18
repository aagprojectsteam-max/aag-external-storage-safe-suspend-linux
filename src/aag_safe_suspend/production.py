"""Reference-host integration for one ordinary-suspend transaction owner.

The separately installed storage auditor and T700 helper are pinned adapters,
not state-machine owners. Their historical failure/poweroff entrypoints are
never invoked here. Machine identities stay in a root-owned local config.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

from . import transaction as rules
from . import workloads

CONFIG = Path("/etc/aag-sleep-transaction/config.json")
STATE = Path("/var/lib/aag-sleep-transaction")
HELPER = "/usr/local/libexec/aag-sleep-transaction"
V2 = "aag-storage-sleep-v2.service"
NATIVE = "systemd-suspend.service"
VERIFY = "aag-t700-resume-check.service"
FAILURE = "aag-suspend-failure-failsafe.service"
RETRY = "aag-sleep-recovery-retry.service"


class RecoveryCommands:
    """Preserve non-UTF8 filesystem labels without weakening device identity.

    lsblk can emit raw legacy volume-label bytes in JSON. Surrogateescape
    preserves those bytes; serial, UUID and kernel-generation checks still
    compare exact values. This affects only the imported audit adapter.
    """
    def __getattr__(self, name):
        return getattr(subprocess, name)

    def run(self, argv, **kwargs):
        if argv and argv[0] == '/usr/bin/lsblk' and kwargs.get('text'):
            kwargs.setdefault('errors', 'surrogateescape')
        return subprocess.run(argv, **kwargs)


def run(argv, timeout=15, check=True, **kwargs):
    p = subprocess.run(argv, text=True, errors='replace', capture_output=True, timeout=timeout,
                       check=False, **kwargs)
    if check and p.returncode:
        raise rules.Refusal(f"{argv[0]} rc={p.returncode}: {(p.stderr or p.stdout)[-1200:]}")
    return p


def trusted(path: Path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise rules.Refusal(f"untrusted production file: {path}")


def load_adapter(path: str, digest: str, name: str):
    file = Path(path)
    trusted(file)
    if hashlib.sha256(file.read_bytes()).hexdigest() != digest:
        raise rules.Refusal(f"adapter changed; review required: {file}")
    loader = importlib.machinery.SourceFileLoader(name, str(file))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def properties(unit):
    p = run(["/usr/bin/systemctl", "show", unit,
             "--property=ActiveState,InvocationID,Job,Result"], timeout=5)
    return dict(x.split("=", 1) for x in p.stdout.splitlines() if "=" in x)


def stats():
    result = {"monotonic": time.monotonic(), "boottime": time.clock_gettime(time.CLOCK_BOOTTIME)}
    for key in ("success", "fail", "total_hw_sleep", "last_hw_sleep"):
        try:
            result[key] = int((Path("/sys/power/suspend_stats") / key).read_text())
        except (OSError, ValueError):
            result[key] = None
    return result


def battery():
    rows = []
    for p in Path("/sys/class/power_supply").glob("*"):
        try:
            if (p / "type").read_text().strip() != "Battery":
                continue
            capacity = int((p / "capacity").read_text())
            status = (p / "status").read_text().strip()
            rows.append({"capacity": capacity, "status": status})
        except (OSError, ValueError):
            continue
    return {"critical": any(x["status"] == "Discharging" and x["capacity"] <= 2 for x in rows),
            "observed": bool(rows), "batteries": rows}


def lid_ignored():
    try:
        d = json.loads(Path("/run/input-lock/state.json").read_text())
        return d.get("ignore_lid_close") is True
    except (OSError, ValueError):
        return False


def previous_boot_outcome(boot):
    if not re.fullmatch(r'[0-9a-f-]{32,36}', str(boot)):
        return {'classification': 'UNKNOWN', 'reason': 'missing prior boot identity'}
    p = run(['/usr/bin/journalctl', '--boot='+boot, '-n', '500', '-o', 'cat', '--no-pager'],
            timeout=15, check=False)
    tail = p.stdout
    if p.returncode or not tail.strip():
        return {'classification': 'UNKNOWN', 'reason': 'previous journal unavailable'}
    markers = [line for line in tail.splitlines() if any(token in line for token in
        ('Powering off.', 'Reached target System Power Off', 'Reached target system-poweroff.target',
         'Reached target System Reboot', 'Reached target reboot.target', 'Shutting down.'))]
    return {'classification': 'ORDERLY_SHUTDOWN_OBSERVED' if markers else 'UNCLEAN_OR_UNCONFIRMED',
            'shutdown_evidence': markers[-10:], 'forced_power_loss_proven': False}


class Host:
    def __init__(self, config=None):
        if config is None:
            trusted(CONFIG)
            config = json.loads(CONFIG.read_text())
        self.cfg = config
        self.r = load_adapter(**config["recovery_adapter"], name="aag_recovery_audit")
        self.r.subprocess = RecoveryCommands()
        self.t = load_adapter(**config["t700_adapter"], name="aag_t700_policy")
        self.t.load_core()
        STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        s = STATE.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o077:
            raise rules.Refusal("unsafe durable state directory")
        self.value = None

    def emit(self, event, **detail):
        self.r.log(event + " " + json.dumps({"id": (self.value or {}).get("episode"),
                                             **detail}, sort_keys=True))

    def audit_storage(self):
        # A changed namespace/device generation requests another complete
        # audit, never a weaker verdict. Allow bounded normal process churn.
        deadline = time.monotonic() + 15
        while True:
            audit = self.r.audit_three()
            if audit.get('AUDIT_COMPLETE') or time.monotonic() >= deadline:
                return audit
            gaps = audit.get('gaps', [])
            if not gaps or any(g.get('probe') != 'semantic-stability' for g in gaps):
                return audit
            time.sleep(0.2)

    def prune_history(self):
        # Bound only this adapter's completed evidence, never live state,
        # legacy incident archives, deployment backups or user files.
        for directory, keep in (('transactions', 128), ('aborts', 128), ('incidents', 32)):
            folder = STATE / directory
            if not folder.exists():continue
            files = sorted((p for p in folder.iterdir() if not p.is_symlink() and p.is_file()),
                           key=lambda p: p.stat().st_mtime_ns, reverse=True)
            for p in files[keep:]:p.unlink()

    def incident(self, reason, audit):
        detail = {k: audit.get(k) for k in ('identity', 'records', 'gaps', 'AUDIT_COMPLETE',
                                          'EXTERNAL_OWNER', 'semantic_signature') if k in audit}
        key = hashlib.sha256(json.dumps({'reason': reason, 'detail': detail}, sort_keys=True).encode()).hexdigest()
        path = STATE / 'incidents' / (key + '.json')
        if path.exists():return
        self.r.atomic_json(path, {'episode': self.value.get('episode'), 'time': time.time(),
                                  'reason': reason, 'audit': detail})
        self.prune_history()

    def save(self, value=None):
        value = self.value if value is None else value
        self.r.save_state(value)
        self.r.atomic_json(STATE / "current.json", value)
        # fsync the directory as well as the replacement file.
        fd = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def update(self, **changes):
        with self.r.state_lock():
            live = self.r.load_state()
            if self.value and live.get("episode") != self.value.get("episode"):
                raise rules.Refusal("transaction changed while operation was running")
            live.update(changes)
            self.value = live
            self.save()

    def failed(self, stage, reason, blockers=None):
        with self.r.state_lock():
            live = self.r.load_state()
            if self.value and live.get("episode") != self.value.get("episode"):
                self.emit("STALE_CALLBACK_IGNORED", stage=stage)
                return
            if live.get("state") == "IDLE":
                return
            self.value = rules.fail(live, stage, reason, blockers)
            self.save()
        self.emit("SUSPEND_TXN_FAILED", stage=stage, reason=reason,
                  retry=self.value.get("retry_count", 0), fallback="diagnose-and-monitor")

    def archive(self, terminal):
        self.r.validate_automount_marker_for_release(self.value)
        completed = {**self.value, "state": terminal, "completed_at": time.time(), "retry_state": "COMPLETE"}
        name = self.value.get("episode") or "legacy"
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(name)):
            name = hashlib.sha256(str(name).encode()).hexdigest()
        self.r.atomic_json(STATE / "transactions" / (name + ".json"), completed)
        self.r.clear_to_idle(self.value, terminal)
        self.r.atomic_json(STATE / "current.json", self.r.load_state())
        self.emit("TXN_COMPLETE", terminal=terminal, actual_sleep=self.value.get("actual_sleep", False))
        with contextlib.suppress(OSError):self.prune_history()

    def abort_policy(self):
        """Restore only saved T700 policy; never call its old failsafe command."""
        if self.t.PENDING.exists():
            tx = self.t.read_json(self.t.PENDING)
            if tx["before"]["boot_id"] != self.r.boot_id():
                raise rules.Refusal("previous-boot T700 state requires boot reconciliation")
            self.t.LIMIT = time.monotonic() + 20
            self.t.restore_transaction(tx)
            target = STATE / "aborts" / (str(time.time_ns()) + ".json")
            self.r.atomic_json(target, tx)
            self.t.PENDING.unlink()
            self.emit("DEVICE_POLICY_RESTORED", kernel_resume=False)

    def live_owner(self, value):
        if properties(FAILURE).get("ActiveState") in {"active", "activating", "deactivating"}:
            return True
        for unit, key in ((V2, "prep_invocation"), (NATIVE, "native_invocation"),
                          (VERIFY, "verify_invocation")):
            p = properties(unit)
            if (value.get(key) and p.get("InvocationID") == value[key]
                    and p.get("ActiveState") in {"active", "activating", "deactivating"}):
                # The current V2 invocation is acquiring a new/old transaction;
                # only a different actual invocation owns the old state.
                if unit == V2 and value[key] != os.environ.get("INVOCATION_ID"):
                    return True
                if unit != V2:
                    return True
        return False

    def reconcile(self):
        self.abort_policy()
        healthy, reason = self.r.internal_data_healthy()
        if not healthy:
            raise rules.Refusal("reconciliation blocked: " + reason)
        if self.t.QUEUE.exists():
            # A crashed verifier leaves captured, invocation-bound evidence.
            # Recheck it through the same full health path; the logind gate
            # prevents running network recovery inside an active sleep job.
            self.verify_device()
        # Reconcile a failed preparation without pretending the USB bridge
        # resumed. Existing normal mounts and writers are safe while awake;
        # the new preparation will independently audit/unmount them.
        if self.value.get("kernel_cycle"):
            result = self.r.terminal_ugreen_reaudit(self.value)
            if not result.get("ok"):
                raise rules.Refusal("post-resume external storage unresolved: " + result.get("reason", "unknown"))
        if self.t.BLOCKED.exists():
            self.reconcile_qualification_latch()
        self.r.validate_automount_marker_for_release(self.value)
        workloads.restore(self.cfg["workloads"], self.value.get("stopped_workloads", []),
                          self.workload_run, lambda: self.save())
        self.restore_clones()
        with self.r.state_lock():
            if self.r.load_state().get("episode") != self.value.get("episode"):
                raise rules.Refusal("reconciliation owner changed")
            self.archive("RECOVERED")

    def workload_run(self, row, command, timeout):
        uid = row.get("uid")
        prefix = []
        if uid is not None:
            # Never execute a user-writable workload CLI as root.
            prefix = ["/usr/sbin/runuser", "-u", row["user"], "--", "/usr/bin/env",
                      f"XDG_RUNTIME_DIR=/run/user/{uid}",
                      f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus"]
        p = run(prefix + command, timeout=timeout, check=False, cwd="/")
        if row.get("protocol") == "systemd-user" and command[-1] == row.get("unit"):
            if p.returncode == 4:
                # Collected transient units disappear after a successful stop.
                # Confirm absence through the reachable manager; an exit code
                # alone must never turn an unavailable bus into "stopped".
                observed = run(prefix + ["/usr/bin/systemctl", "--user", "show", row["unit"],
                    "--property=LoadState,ActiveState,SubState,MainPID"],
                    timeout=timeout, check=False, cwd="/")
                state = dict(line.split("=", 1) for line in observed.stdout.splitlines() if "=" in line)
                if observed.returncode == 0 and state == {
                        "LoadState": "not-found", "ActiveState": "inactive",
                        "SubState": "dead", "MainPID": "0"}:
                    return {"active": False}
            if p.returncode not in (0, 3):
                raise rules.Refusal("workload user manager is unobservable")
            return {"active": p.stdout.strip() in {"active", "activating", "deactivating", "reloading"}}
        if p.returncode:
            raise rules.Refusal("workload command failed: " + p.stderr[-1000:])
        result = json.loads(p.stdout)
        if not isinstance(result, dict):
            raise rules.Refusal("workload returned invalid response")
        return result

    def begin(self):
        owner = os.environ.get("INVOCATION_ID")
        if not owner:
            raise rules.Refusal("begin requires a systemd invocation")
        with self.r.state_lock():
            self.value = self.r.load_state()
        disposition = rules.request_disposition(self.value, self.r.boot_id(), owner,
                                                self.live_owner(self.value))
        if disposition == "COALESCE":
            self.emit("SUSPEND_REQUEST_COALESCED")
            raise rules.Refusal("active transaction owns sleep/resume")
        if disposition in {"RECONCILE", "BOOT_RECONCILE"}:
            self.reconcile()
        with self.r.state_lock():
            if disposition == "RETRY":
                self.value = self.r.load_state()
                self.value.update(state="PREPARING_SLEEP", prep_invocation=owner,
                                  native_invocation=None, stage="DISCOVER_BLOCKERS")
            else:
                self.value = rules.new(self.r.boot_id(), owner)
            self.save()
            self.r.arm_automount_suppression(self.value)
            self.save()
        self.emit("SUSPEND_TXN_START", retry=self.value["retry_count"])
        try:
            jobs = self.r.live_registered_jobs() + self.r.timeshift_processes()
            if jobs:
                self.failed("DISCOVER_BLOCKERS", "active-or-unobservable-backup", jobs)
                raise rules.Refusal("backup must reach its natural durable boundary")
            # Internal DATA workloads do not block external UGREEN unmount.
            # Checkpoint the configured optional compute worker before sleep.
            self.update(stage="QUIESCE_NONESSENTIAL_WORKLOADS", storage_state="QUIESCING")
            try:
                workloads.quiesce(self.cfg["workloads"], {"DATA", "UGREEN", "compute"},
                                  self.workload_run, lambda: self.save(),
                                  self.value["stopped_workloads"], self.emit,
                                  require_restore=True)
            except workloads.OneWayStopProhibited:
                raise
            except rules.Refusal as e:
                # A missing optional checkpoint API is not a sleep veto.
                # Exact storage references below still require verified release.
                self.emit('CHECKPOINT_API_INCOMPLETE', reason=str(e), fallback='audit-exact-process-blockers')
            self.quiesce_clones()
            self.resolve_process_blockers(self.audit_storage, 'UGREEN')
            self.update(stage="PREPARE_STORAGE")
        except Exception as e:
            self.failed(self.value.get("stage", "PREPARE"), str(e))
            raise

    def verify_release(self):
        with self.r.state_lock():
            self.value = self.r.load_state()
        audit = self.audit_storage()
        if not audit.get("AUDIT_COMPLETE") or audit.get("EXTERNAL_OWNER") != "NONE":
            self.failed("PREPARE_STORAGE", "external-owner-not-released", self.enrich(audit.get("records", [])))
            self.incident("transaction-external-blockers", audit)
            raise rules.Refusal("external ownership not safely released")
        self.update(state="SLEEP_COMMITTED", stage="READY_TO_SUSPEND",
                    storage_state="UNMOUNTED_SAFE", release_verified=True,
                    release_audit=audit)
        self.emit("STORAGE_PREPARED")

    def native_pre(self):
        with self.r.state_lock():
            self.value = self.r.load_state()
        if self.value.get("state") != "SLEEP_COMMITTED":
            raise rules.Refusal("native sleep lacks verified storage preparation")
        self.update(native_invocation=os.environ.get("INVOCATION_ID"))
        try:
            self.quiesce_clones()  # close the preparation-to-native race
            before = stats()
            if before["success"] is None:
                raise rules.Refusal("kernel suspend statistics unavailable")
            self.t.LIMIT = time.monotonic() + 50
            self.update(kernel_before=before, kernel_cursor=self.t.core.last_cursor(), stage="DEVICE_PREPARATION")
            self.t.pre()
            self.update(stage="SUSPEND_REQUESTED", state="SUSPEND_REQUESTED",
                        storage_state="SUSPEND_PENDING")
            self.emit("SUSPEND_REQUESTED", evidence="prepared; kernel entry not yet observed")
        except Exception as e:
            self.abort_policy()
            self.failed("DEVICE_PREPARATION", str(e))
            raise

    def native_post(self):
        with self.r.state_lock():
            self.value = self.r.load_state()
        if self.value.get("native_invocation") != os.environ.get("INVOCATION_ID"):
            self.emit("STALE_CALLBACK_IGNORED", stage="native-post")
            return
        result = os.environ.get("SERVICE_RESULT", "unknown")
        self.t.LIMIT = time.monotonic() + 35
        evidence = rules.sleep_evidence(self.value.get("kernel_before", {}), stats(), result)
        try:
            events = self.t.core.kernel_since(self.value.get("kernel_cursor"))
            messages = [str(e.get("MESSAGE", "")) for e in events]
            evidence.update(kernel_entry=any(x.startswith("PM: suspend entry") for x in messages),
                            kernel_exit=any(x.startswith("PM: suspend exit") for x in messages),
                            kernel_messages=[x for x in messages if x.startswith("PM:")])
        except Exception as e:
            evidence["kernel_journal_gap"] = str(e)
        self.update(**evidence)
        if not evidence["kernel_cycle"]:
            self.abort_policy()
            self.failed("KERNEL_ENTRY", "kernel suspend never completed")
            self.emit("NO_RESUME", evidence=evidence)
            raise rules.Refusal("kernel suspend never completed; recovery required")
        self.emit("KERNEL_SLEEP_OBSERVED", **evidence)
        self.update(state="SLEEP_COMMITTED", stage="RESUME_DETECTED", storage_state="RESUME_WAIT_DEVICE")
        self.t.LIMIT = time.monotonic() + 25
        try:
            self.device_post()
        except Exception as e:
            self.failed('DEVICE_POST', str(e))
            raise

    def device_post(self):
        tx = self.t.stale_pending()
        out = Path(tx['directory'])
        if out.parent != self.t.STATE or not out.name.startswith('cycle-'):
            raise rules.Refusal('invalid device evidence directory')
        try:
            after = self.t.core.snapshot()
        finally:
            self.t.restore_transaction(tx)
        self.t.write_json(out / 'after-hardware.json', after)
        self.t.PENDING.rename(out / 'transaction-restored.json')
        self.t.write_json(self.t.QUEUE, {'transaction': tx, 'after': after,
            'service_result': os.environ.get('SERVICE_RESULT'),
            'invocation': os.environ.get('INVOCATION_ID'), 'lid_at_capture': self.r.lid_state()})
        self.emit('DEVICE_POLICY_RESTORED', kernel_resume=True, lid=self.r.lid_state())
        run(['/usr/bin/systemctl', '--no-block', 'start', VERIFY], timeout=5)

    def verify_device(self):
        """Check operational restoration separately from long-cycle qualification."""
        with self.r.state_lock():
            self.value = self.r.load_state()
        self.t.LIMIT = time.monotonic() + 107
        job = self.t.read_json(self.t.QUEUE)
        tx, after = job['transaction'], job['after']
        out = Path(tx['directory'])
        if (out.parent != self.t.STATE or not out.name.startswith('cycle-')
                or tx['before']['boot_id'] != self.r.boot_id()
                or job['invocation'] != self.value.get('native_invocation')):
            raise rules.Refusal('device verification owner/boot mismatch')
        gate_end = time.monotonic() + 5
        while True:
            p = run(['/usr/bin/busctl', 'get-property', 'org.freedesktop.login1',
                '/org/freedesktop/login1', 'org.freedesktop.login1.Manager', 'PreparingForSleep'], timeout=2)
            if p.stdout.strip() == 'b false':break
            if time.monotonic() >= gate_end:raise rules.Refusal('logind sleep transition remains active')
            time.sleep(.25)
        self.t.verify_files(self.t.config())
        observations = []
        deadline = time.monotonic() + 90
        while True:
            net = self.t.core.cellular()
            observations.append({'time': time.time(), 'state': net})
            if (not tx['cellular_before'].get('healthy') or (net.get('healthy')
                    and net.get('connection_uuid') == tx['cellular_before'].get('connection_uuid'))):break
            if time.monotonic() >= deadline:break
            time.sleep(2)
        self.t.write_json(out / 'network-observations.json', observations)
        events = self.t.core.kernel_since(tx['cursor'])
        self.t.write_json(out / 'kernel-events.json', events)
        result = self.t.assess(tx, after, events, net, job['service_result'],
            self.t.policy() == tx['before']['policy'], self.r.lid_state(), job['invocation'])
        result['checks']['data_still_healthy_after_reconnect'] = self.t.core.data_state() == tx['before']['data']
        result['checks']['loaded_module_still_baseline'] = self.t.core.loaded() == self.t.core.BASE_SRC
        result['qualification_outcome'] = result['outcome']
        healthy = rules.device_health(result)
        result.update(outcome='HEALTHY_RESUME' if healthy else 'DEVICE_RECOVERY_FAILED',
                      actual_sleep=self.value.get('actual_sleep'), lid_after=self.r.lid_state())
        self.t.publish_result(out, result)
        self.t.QUEUE.rename(out / 'resume-check-completed.json')
        self.update(device_health=healthy, device_result=result)
        if not healthy:
            self.t.block('Device health verification failed', str(out))
            raise rules.Refusal('failed device checks: ' + ','.join(k for k,v in result['checks'].items()
                                if k not in rules.QUALIFICATION_CHECKS and v is not True))

    def reconcile_qualification_latch(self):
        """Only retire a proven qualification-only latch, after fresh health checks."""
        latch = self.t.read_json(self.t.BLOCKED)
        result = self.t.read_json(self.t.LAST)
        out = Path(str(result.get('directory', '')))
        historical_qualification_reasons = {
            'Automatic suspend/resume verification failed',
            'Device health verification failed',
        }
        if (latch.get('reason') not in historical_qualification_reasons
                or latch.get('boot_id') != self.r.boot_id() or result.get('boot_id') != self.r.boot_id()
                or out.parent != self.t.STATE or not out.name.startswith('cycle-')
                or latch.get('extra') != str(out) or not rules.device_health(result)):
            raise rules.Refusal('T700 health latch requires diagnosis; not cleared by fence expiry')
        tx = self.t.read_json(out / 'transaction-restored.json')
        if tx.get('invocation') != self.value.get('native_invocation'):
            raise rules.Refusal('qualification latch belongs to another transaction')
        self.t.LIMIT = time.monotonic() + 30
        self.t.check_host(self.t.config())
        net = self.t.core.cellular()
        if (self.t.policy() != tx['before']['policy'] or self.t.core.data_state() != tx['before']['data']
                or (tx['cellular_before'].get('healthy') and (not net.get('healthy')
                    or net.get('connection_uuid') != tx['cellular_before'].get('connection_uuid')))):
            raise rules.Refusal('qualification-only reconciliation found a current device health problem')
        self.r.atomic_json(STATE / 'aborts' / ('qualification-only-' + str(time.time_ns()) + '.json'),
                           {'latch': latch, 'result': result, 'fresh_health_verified': True})
        self.t.BLOCKED.unlink()
        self.emit('QUALIFICATION_LATCH_RECONCILED', actual_sleep=self.value.get('actual_sleep'))

    def resume_begin(self):
        with self.r.state_lock():
            self.value = self.r.load_state()
        if not self.value.get("kernel_cycle"):
            raise rules.Refusal("resume callback without a confirmed kernel cycle")
        self.r.resume_verify_begin()
        with self.r.state_lock():
            self.value = self.r.load_state()
        self.update(verify_invocation=os.environ.get("INVOCATION_ID"), stage="RESTORE_STORAGE")

    def resume_terminal(self):
        with self.r.state_lock():
            self.value = self.r.load_state()
        if self.value.get("verify_invocation") != os.environ.get("INVOCATION_ID"):
            self.emit("STALE_CALLBACK_IGNORED", stage="resume-terminal")
            return
        if os.environ.get("SERVICE_RESULT") != "success":
            self.failed("RESTORE_STORAGE", "device health verification failed", self.value.get('blockers'))
            raise rules.Refusal("device health verification failed")
        terminal = self.r.terminal_ugreen_reaudit(self.value)
        if not terminal.get("ok"):
            self.failed("RESTORE_STORAGE", terminal.get("reason", "unknown"))
            raise rules.Refusal("external storage terminal verification failed")
        self.update(stage="RESTORE_WORKLOADS", storage_state="HEALTHY", terminal_ugreen_reaudit=terminal)
        workloads.restore(self.cfg["workloads"], self.value.get("stopped_workloads", []),
                          self.workload_run, lambda: self.save())
        self.restore_clones()
        if self.r.lid_state() == 'closed' and not lid_ignored():
            self.failed('WAKE_WHILE_LID_CLOSED', 'healthy resume while lid remains closed; bounded retry required')
            raise rules.Refusal('lid remains closed after resume')
        with self.r.state_lock():
            self.archive("COMPLETE" if self.value.get("actual_sleep") else "RECOVERED")

    def teardown(self):
        with self.r.state_lock():
            self.value = self.r.load_state()
        if self.value.get("prep_invocation") != os.environ.get("INVOCATION_ID"):
            return
        if os.environ.get("SERVICE_RESULT") != "success":
            self.failed("PREPARE_STORAGE", self.value.get("reason") or "storage service failed")

    def kernel_resumed(self):
        with self.r.state_lock():
            value = self.r.load_state()
        if value.get("state") == "IDLE":
            with contextlib.suppress(OSError, ValueError):
                value = json.loads((self.r.RUNTIME / "last-terminal.json").read_text())
        if (value.get("boot_id") != self.r.boot_id() or
                value.get("native_invocation") != properties(NATIVE).get("InvocationID")):
            return False
        return value.get("kernel_cycle") is True

    def enrich(self, rows):
        for row in rows:
            if not row.get("pid"):
                continue
            p = Path("/proc") / str(row["pid"])
            for name in ("cmdline", "cgroup", "status"):
                with contextlib.suppress(OSError):
                    row[name] = (p / name).read_text().replace("\0", " ")[:4096]
            for name in ("exe", "cwd", "root", "ns/mnt"):
                with contextlib.suppress(OSError):
                    row[name] = os.readlink(p / name)
        return rows

    def process_blockers(self, audit):
        if not audit.get('AUDIT_COMPLETE'):
            raise rules.Refusal('ownership audit incomplete; cannot select a safe process to stop')
        return ([r for r in audit['records'] if r.get('kind') != 'mount']
                + self.r.unsafe_mount_topology(audit['records']))

    def container_identity(self, info):
        match = re.search(r'(?:docker-|/docker/)([0-9a-f]{64})(?:\.scope|/|$)', info['cgroup'], re.M)
        if not match:
            return None
        cid = match[1]
        data = json.loads(run(['/usr/bin/docker', 'inspect', cid], timeout=10).stdout)[0]
        if data['Id'] != cid or not data['State']['Running']:
            raise rules.Refusal('container identity changed')
        return data

    def container_noncritical(self, info):
        # An ordinary unprivileged process needs no container/application list.
        if min(info['uids']) >= 1000 and not info['caps']:
            return False
        data = self.container_identity(info)
        if not data or data.get('HostConfig', {}).get('Privileged'):
            return False
        # Check the entire container, so a worker subordinate to a database,
        # filesystem service or VM is not mistaken for disposable work.
        rows = run(['/usr/bin/docker', 'top', data['Id'], '-eo', 'pid'], timeout=10).stdout.splitlines()[1:]
        if not rows:
            return False
        for row in rows:
            item = workloads.process_snapshot(int(row.strip()))
            if item and (data['Id'] not in item['cgroup'] or
                         workloads.CRITICAL.search(item['exe'] + ' ' + item['cmdline'])):
                return False
        return True

    def critical_safe_stop(self, info, receipt):
        data = self.container_identity(info)
        if not data:
            return False
        for policy in self.cfg.get('critical_stops', []):
            if (Path(info['exe']).name != policy['executable'] or data['Name'] != policy['container_name']
                    or data['Config']['Image'] != policy['image']):
                continue
            if any(x.get('container_id') == data['Id'] and x is not receipt
                   for x in self.value.get('stopped_processes', [])):
                raise rules.Refusal('safe guest shutdown already requested for this transaction')
            receipt.update(container_id=data['Id'], mechanism=policy['mechanism'], status='SAFE_STOP_REQUESTED')
            self.save()
            current = workloads.process_snapshot(info['pid'])
            if not current or current['start'] != info['start']:
                return True
            if data['Id'] not in current['cgroup'] or current['exe'] != info['exe']:
                raise rules.Refusal('critical component changed before safe shutdown')
            command = [data['Id'] if arg == '{container_id}' else arg for arg in policy['command']]
            self.emit('BLOCKER_STOP_REQUESTED', pid=info['pid'], mechanism=policy['mechanism'], forced=False)
            run(command, timeout=10)
            deadline = time.monotonic() + min(120, max(1, policy['timeout']))
            while time.monotonic() < deadline:
                current = workloads.process_snapshot(info['pid'])
                if not current or current['start'] != info['start']:
                    return True
                time.sleep(1)
            return True  # caller must independently verify actual release
        return False

    def resolve_process_blockers(self, scan, resource):
        deadline = time.monotonic() + 180
        resolved = set()
        while True:
            audit = scan()
            blockers = self.process_blockers(audit)
            if not blockers:
                return audit
            self.incident('process-blockers-' + resource, audit)
            identities = {(r.get('pid'), r.get('start')) for r in blockers}
            if time.monotonic() >= deadline or identities <= resolved:
                raise rules.Refusal(resource + ': blocker generation remains after bounded resolution')
            for record in blockers:
                if time.monotonic() >= deadline:
                    raise rules.Refusal(resource + ': total process-resolution budget exhausted')
                key = (record.get('pid'), record.get('start'))
                if key in resolved:
                    continue
                def still_holds(pid, start):
                    return any(r.get('pid') == pid and str(r.get('start')) == start
                               for r in self.process_blockers(scan()))
                self.value.setdefault('stopped_processes', [])
                try:
                    workloads.terminate_blocker(record, still_holds=still_holds,
                        safe_stop=self.critical_safe_stop, container_safe=self.container_noncritical,
                        save=self.save, receipts=self.value['stopped_processes'], emit=self.emit,
                        allow_stop=False)
                except Exception as e:
                    self.failed('QUIESCE_NONESSENTIAL_WORKLOADS', str(e), self.enrich(blockers))
                    raise
                resolved.add(key)

    def clone_inventory(self):
        root = Path("/sys/kernel/config/usb_gadget")
        if not root.exists():
            return []
        rows = []
        for g in sorted(root.iterdir()):
            if g.is_symlink() or not g.is_dir():
                raise rules.Refusal("unexpected ConfigFS gadget topology")
            udc = (g / "UDC").read_text().strip()
            if not udc:
                continue
            luns = list(g.glob("functions/mass_storage.*/lun.*/file"))
            rows.append({"name": g.name, "udc": udc,
                         "backings": [p.read_text().strip() for p in luns],
                         "functions": sorted(p.name for p in (g / "functions").iterdir())})
        return rows

    def clone_audit(self, clones):
        # Reuse the accepted namespace-aware scanner against the virtual
        # device generation. Also check usbfs character FDs and backing files.
        nodes, devnos, names, usb_paths = [], [], [], []
        for p in Path("/sys/class/block").iterdir():
            if "/dummy_hcd." in str(p.resolve()):
                devnos.append((p / "dev").read_text().strip())
                names.append(p.name)
        for p in Path("/sys/bus/usb/devices").iterdir():
            if "-" in p.name and "/dummy_hcd." in str(p.resolve()):
                if (p / "busnum").exists() and (p / "devnum").exists():
                    usb_paths.append(f"/dev/bus/usb/{int((p / 'busnum').read_text()):03d}/{int((p / 'devnum').read_text()):03d}")
        if not clones and (devnos or usb_paths):
            raise rules.Refusal("orphan virtual USB device without ConfigFS owner")
        if not clones:
            return {"AUDIT_COMPLETE": True, "records": [], "EXTERNAL_OWNER": "NONE"}
        old = self.r.ugreen_identity
        try:
            self.r.ugreen_identity = lambda: {"complete": True, "present": bool(devnos),
                                              "devnos": devnos, "partition_devnos": devnos, "names": names}
            audit = self.audit_storage()
        finally:
            self.r.ugreen_identity = old
        targets = {str(Path(b).resolve()) for c in clones for b in c["backings"] if b} | set(usb_paths)
        inodes = set()
        for target in targets:
            try:
                s = Path(target).stat()
                inodes.add((s.st_dev, s.st_ino))
            except FileNotFoundError:
                raise rules.Refusal("USB Clone backing/device disappeared during audit") from None
        for p in Path("/proc").glob("[0-9]*"):
            before = self.r.proc_stat(int(p.name))
            if not before or before.get("state") == "Z":
                continue
            try:
                for fd in (p / "fd").iterdir():
                    with contextlib.suppress(FileNotFoundError, ProcessLookupError):
                        link = os.readlink(fd)
                        fstat = fd.stat()
                        if link.removesuffix(" (deleted)") in targets or (fstat.st_dev, fstat.st_ino) in inodes:
                            nodes.append({"kind": "usb-or-backing-fd", "pid": int(p.name),
                                          "start": before["start"], "file": link})
                for line in (p / "maps").read_text().splitlines():
                    fields = line.split(maxsplit=5)
                    if len(fields) >= 5:
                        major, minor = (int(x, 16) for x in fields[3].split(":"))
                        if (os.makedev(major, minor), int(fields[4])) in inodes:
                            nodes.append({"kind": "backing-mmap", "pid": int(p.name),
                                          "start": before["start"], "mapping": line})
            except PermissionError:
                audit["AUDIT_COMPLETE"] = False
            except (FileNotFoundError, ProcessLookupError):
                pass
        # A loop mapping of a backing file is a real consumer even when idle.
        for p in Path("/sys/class/block").glob("loop*/loop/backing_file"):
            if p.read_text().strip() in targets:
                nodes.append({"kind": "loop-backing", "path": str(p)})
        audit["records"].extend(nodes)
        return audit

    def quiesce_clones(self):
        clones = self.clone_inventory()
        if not clones:
            # Still reject orphan dummy devices (historical kernel blocker).
            self.clone_audit([])
            return
        configured = self.cfg["usbclone"]
        trusted(Path(configured["command"]))
        if hashlib.sha256(Path(configured["command"]).read_bytes()).hexdigest() != configured["sha256"]:
            raise rules.Refusal("USB Clone command changed")
        for clone in clones:
            name = clone["name"].removeprefix("usbclone_")
            if (not clone["name"].startswith("usbclone_") or name not in configured["profiles"]
                    or not re.fullmatch(r"dummy_udc\.[0-9]+", clone["udc"])
                    or clone["functions"] != ["mass_storage.0"]
                    or clone["backings"] != [configured["profiles"][name]]):
                raise rules.Refusal("unmanaged USB gadget; automatic detach prohibited")
        audit = self.resolve_process_blockers(lambda: self.clone_audit(clones), 'USB-Clone')
        # Only clean host unmounts are permitted. No lazy/forced namespace
        # release; inherited shared mounts must vanish in the second audit.
        mounts = {r["target"] for r in audit["records"] if r.get("kind") == "mount" and r.get("owner") == "HOST"}
        for target in sorted(mounts, key=len, reverse=True):
            run(["/usr/bin/sync", "-f", target], timeout=15)
            run(["/usr/bin/umount", "--", target], timeout=15)
        audit = self.clone_audit(clones)
        if not audit.get("AUDIT_COMPLETE") or audit["records"] or self.clone_inventory() != clones:
            raise rules.Refusal("USB Clone resources changed during graceful release")
        self.update(usbclone_stopped=self.value.get("usbclone_stopped", []), stage="QUIESCE_NONESSENTIAL_WORKLOADS")
        for clone in clones:
            receipt = {**clone, "status": "STOP_REQUESTED", "was_running": True,
                       "restart_after_resume": "if_was_running"}
            self.value["usbclone_stopped"].append(receipt)
            self.save()
            self.emit("BLOCKER_STOP_REQUESTED", workload=clone["name"], policy="unused-known-virtual-device")
            run([configured["command"], "stop", clone["name"].removeprefix("usbclone_")], timeout=30)
            receipt["status"] = "STOPPED"
            self.save()
        run(["/usr/bin/udevadm", "settle", "--timeout=5"], timeout=7)
        if self.clone_inventory():
            raise rules.Refusal("virtual USB disconnect not verified")
        deadline = time.monotonic() + 7
        while True:
            try:
                self.clone_audit([])
                break
            except rules.Refusal:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)  # wait for actual kernel disconnect, not just udev's queue
        self.emit("BLOCKER_STOP_RESULT", workload="USB Clone", success=True,
                  restore="if_was_running")

    def restore_clones(self):
        receipts = [
            row for row in self.value.get("usbclone_stopped", [])
            if row.get("status") == "STOPPED"
            and row.get("was_running") is True
            and row.get("restart_after_resume") == "if_was_running"
        ]
        if not receipts:
            return
        configured = self.cfg.get("usbclone")
        if not configured:
            raise rules.Refusal("USB Clone restore policy disappeared")
        command = Path(configured["command"])
        trusted(command)
        if hashlib.sha256(command.read_bytes()).hexdigest() != configured["sha256"]:
            raise rules.Refusal("USB Clone command changed before restore")
        profiles = configured.get("profiles", {})

        def matches(row, receipt):
            profile = receipt["name"].removeprefix("usbclone_")
            return (
                row.get("name") == receipt.get("name")
                and re.fullmatch(r"dummy_udc\.[0-9]+", str(row.get("udc", ""))) is not None
                and row.get("functions") == ["mass_storage.0"]
                and row.get("backings") == [profiles.get(profile)]
            )

        for receipt in receipts:
            name = receipt.get("name", "")
            profile = name.removeprefix("usbclone_")
            if (not name.startswith("usbclone_") or profile not in profiles
                    or receipt.get("functions") != ["mass_storage.0"]
                    or receipt.get("backings") != [profiles[profile]]):
                raise rules.Refusal("USB Clone restore receipt no longer matches configured profile")
            current = self.clone_inventory()
            if any(row.get("name") == name for row in current):
                if not any(matches(row, receipt) for row in current):
                    raise rules.Refusal("USB Clone identity changed before restore")
                receipt["status"] = "RESTORED"
                self.save()
                continue
            receipt["status"] = "RESTORE_REQUESTED"
            self.save()
            self.emit("BLOCKER_RESTORE_REQUESTED", workload=name, profile=profile)
            run([configured["command"], "start", profile], timeout=30)
            run(["/usr/bin/udevadm", "settle", "--timeout=5"], timeout=7)
            deadline = time.monotonic() + 7
            while True:
                current = self.clone_inventory()
                if any(matches(row, receipt) for row in current):
                    break
                if time.monotonic() >= deadline:
                    receipt["status"] = "RESTORE_FAILED"
                    self.save()
                    raise rules.Refusal("USB Clone restart was not verified")
                time.sleep(0.2)
            receipt["status"] = "RESTORED"
            self.save()
            self.emit("BLOCKER_RESTORE_RESULT", workload=name, success=True)

    def manual_reconcile(self):
        with self.r.state_lock():
            self.value = self.r.load_state()
        if self.value.get("state") == "IDLE":
            return
        if self.live_owner(self.value):
            raise rules.Refusal("manual reconciliation refused while a sleep transaction owner is active")
        if self.r.lid_state() != "open" and not lid_ignored():
            raise rules.Refusal("manual reconciliation requires an open lid")
        self.reconcile()

    def notify(self, reason):
        user = self.cfg.get("notification_user")
        uid = self.cfg.get("notification_uid")
        if user and uid is not None:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                run(["/usr/sbin/runuser", "-u", user, "--", "/usr/bin/env",
                     f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
                     "/usr/bin/notify-send", "-u", "critical", "Sleep needs attention",
                     reason[:500]], check=False, timeout=5)

    def monitor(self):
        with self.r.state_lock():
            self.value = self.r.load_state()
        if self.value.get("state") == "IDLE":
            return
        episode = self.value.get("episode")
        try:
            self.abort_policy()
        except Exception as e:
            self.failed("DEVICE_POLICY_RESTORE", str(e))
        self.failed(self.value.get("stage", "UNKNOWN"), self.value.get("reason") or "native transaction failed")
        self.notify(self.value.get("reason", "Sleep preparation failed; open the lid to inspect."))
        warning = missing = None
        last_reason = None
        while True:
            with self.r.state_lock():
                self.value = self.r.load_state()
            if self.value.get("episode") != episode or self.value.get("state") in rules.TERMINAL:
                return
            now = time.monotonic()
            thermal = self.r.thermal_sample()
            lid = self.r.lid_state()
            warning = (warning or now) if thermal["state"] == "THERMAL_WARNING" else None
            missing = (missing or now) if thermal["state"] == "UNKNOWN" else None
            action, reason = rules.safety_action(thermal, battery(), lid=lid,
                warning_seconds=now-warning if warning else 0, missing_seconds=now-missing if missing else 0)
            if action == "poweroff":
                self.update(stage="SAFETY_TRANSITION", safety_reason=reason, thermal=thermal)
                self.notify("Safety shutdown: " + reason)
                self.emit("SAFETY_TRANSITION", action="poweroff", status="REQUESTED", reason=reason)
                run(["/usr/bin/systemctl", "--no-block", "--check-inhibitors=no", "poweroff"], timeout=8)
                self.emit("SAFETY_TRANSITION", action="poweroff", status="ACCEPTED")
                return
            if lid == "open" or lid_ignored():
                try:
                    self.reconcile()
                except Exception as e:
                    self.emit("RECOVERY_BLOCKED", reason=str(e))
                return  # no stale inhibitor after lid-open or intentional ignore
            if self.value.get("retry_count", 0) < rules.MAX_RETRIES:
                try:
                    if self.r.live_registered_jobs() or self.r.timeshift_processes():
                        raise rules.Refusal("backup remains active")
                    if self.t.BLOCKED.exists() or self.t.QUEUE.exists() or self.t.PENDING.exists():
                        raise rules.Refusal("modem recovery remains unresolved")
                    self.quiesce_clones()
                    self.resolve_process_blockers(self.audit_storage, 'UGREEN')
                    healthy, detail = self.r.internal_data_healthy()
                    if not healthy:
                        raise rules.Refusal(detail)
                    audit = self.audit_storage()
                    if (not audit.get("AUDIT_COMPLETE") or
                            any(r.get("kind") != "mount" for r in audit["records"]) or
                            self.r.unsafe_mount_topology(audit["records"])):
                        raise rules.Refusal("external storage remains busy")
                    with self.r.state_lock():
                        self.value = self.r.load_state()
                        rules.queue_retry(self.value, now)
                        self.save()
                    run(["/usr/bin/systemctl", "--no-block", "start", RETRY])
                    self.emit("RETRY_QUEUED", count=self.value["retry_count"])
                    return  # release inhibitors before ordered retry helper runs
                except Exception as e:
                    reason = str(e)
            else:
                reason = "transaction retry consumed; lid held; monitoring real safety telemetry"
            if reason != last_reason:
                self.emit("RECOVERY_WAIT", reason=reason, thermal=thermal["state"])
                last_reason = reason
            time.sleep(10)

    def retry(self):
        with self.r.state_lock():
            self.value = self.r.load_state()
        if self.value.get("state") != "RETRY_QUEUED":
            return
        if self.r.lid_state() == "open" or lid_ignored():
            self.reconcile()
            return
        self.emit("RETRY_DISPATCH", count=self.value["retry_count"])
        episode = self.value.get("episode")
        prep = self.value.get("prep_invocation")
        # Systemd/logind and the same required V2 path perform preparation.
        run(["/usr/bin/systemctl", "--no-block", "suspend"], timeout=8)
        # An accepted request is not confirmation. Native-post/resume-terminal
        # own confirmation; a refused dispatch fails this unit into monitor.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with self.r.state_lock():
                live = self.r.load_state()
            if live.get("episode") != episode or live.get("prep_invocation") != prep:
                return  # receipt of preparation or safe reconciliation, not sleep
            time.sleep(0.2)
        raise rules.Refusal("retry accepted but no new preparation observed")

    def boot(self):
        old = {}
        with contextlib.suppress(FileNotFoundError):
            old = json.loads((STATE / "current.json").read_text())
        if old.get("boot_id") == self.r.boot_id():
            return
        healthy, reason = self.r.internal_data_healthy()
        if not healthy:
            raise rules.Refusal("boot DATA reconciliation: " + reason)
        for p in (self.t.PENDING, self.t.QUEUE):
            if p.exists():
                d = self.t.read_json(p)
                boot = d.get("before", d.get("transaction", {}).get("before", {})).get("boot_id")
                if boot == self.r.boot_id() or boot is None:
                    raise rules.Refusal("boot reconciliation found current/ambiguous device transaction")
                self.r.atomic_json(STATE / "aborts" / (p.name + "." + str(time.time_ns())), d)
                p.unlink()  # archive stale evidence, never replay old sysfs writes
        if self.t.BLOCKED.exists():
            latch = self.t.read_json(self.t.BLOCKED)
            if latch.get("boot_id") and latch["boot_id"] != self.r.boot_id():
                # Revalidate the actual platform/module/storage baseline,
                # rather than making an old latch globally authoritative.
                self.t.LIMIT = time.monotonic() + 20
                self.t.check_host(self.t.config())
                self.r.atomic_json(STATE / "aborts" / ("previous-boot-modem-latch-" + str(time.time_ns()) + ".json"), latch)
                self.t.BLOCKED.unlink()
        if old and old.get("state") not in rules.TERMINAL:
            self.r.atomic_json(STATE / "aborts" / ("previous-boot-" + str(time.time_ns()) + ".json"),
                               {"prior": old, "status": "ABANDONED", "boot_outcome": previous_boot_outcome(old.get('boot_id'))})
        with self.r.state_lock():
            self.value = self.r.load_state()
            if self.value.get("state") != "IDLE":
                raise rules.Refusal("boot reconciliation will not overwrite current transaction")
            self.save()
        self.emit("BOOT_RECONCILED", previous_transaction=old.get("episode"), old_policy_replayed=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("begin", "verify-release", "v2-teardown", "native-pre",
        "native-post", "resume-verify-begin", "resume-verify-device", "resume-verify-terminal", "failure-recover",
        "retry-suspend", "boot-reconcile", "reconcile-now", "kernel-resumed", "status", "audit"))
    parser.add_argument("--t700-failsafe", nargs=2)  # compatibility declaration, NEVER executed
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise rules.Refusal("production transaction actions require root")
    host = Host()
    if args.action == "kernel-resumed":
        return 0 if host.kernel_resumed() else 1
    methods = {"begin": host.begin, "verify-release": host.verify_release,
        "v2-teardown": host.teardown, "native-pre": host.native_pre, "native-post": host.native_post,
        "resume-verify-begin": host.resume_begin, "resume-verify-terminal": host.resume_terminal,
        "resume-verify-device": host.verify_device,
        "failure-recover": host.monitor, "retry-suspend": host.retry, "boot-reconcile": host.boot,
        "reconcile-now": host.manual_reconcile,
        "status": lambda: print(json.dumps(host.r.load_state(), sort_keys=True)),
        "audit": lambda: print(json.dumps({"ugreen": host.audit_storage(),
                    "usbclone": host.clone_audit(host.clone_inventory()),
                    "stats": stats(), "battery": battery(), "lid_ignored": lid_ignored()}, sort_keys=True))}
    methods[args.action]()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print("SUSPEND_TRANSACTION_REFUSED " + str(e), file=sys.stderr, flush=True)
        raise SystemExit(1)
