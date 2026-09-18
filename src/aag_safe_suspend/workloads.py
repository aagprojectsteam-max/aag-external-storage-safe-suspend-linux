"""Preferred checkpoint APIs and bounded termination of exact ordinary blockers."""
from __future__ import annotations

from typing import Callable
import contextlib
import os
from pathlib import Path
import re
import signal
import time

from .transaction import Refusal


class OneWayStopProhibited(Refusal):
    """Automatic Suspend may not stop something it cannot safely restore."""


# These are safety exclusions, not an application allowlist. The low-level
# helper can still perform a reviewed TERM/KILL fallback when allow_stop=True;
# production Ordinary Suspend passes allow_stop=False unless a reversible
# registered workload/virtual-device path owns the stop.
CRITICAL = re.compile(r"(?:^|[/\s_-])(?:postgres(?:ql)?|mysqld|mariadbd|mongod|redis-server|"
    r"qdrant|etcd|cockroach|qemu(?:-system)?|virtualbox|vmware|firecracker|"
    r"apt(?:-get)?|dpkg|rpm|dnf|pacman|unattended-upgrade|packagekitd|"
    r"fsck|e2fsck|mkfs|resize2fs|btrfs|zpool|zfs|cryptsetup|mdadm|"
    r"mount|umount|fusermount|ntfs-3g|mount.ntfs|rclone|restic|borg|timeshift|"
    r"systemd|dockerd|containerd|containerd-shim|dbus-daemon|dbus-broker|"
    r"gnome-shell|gnome-session|Xorg|Xwayland|sway|kwin|sshd|sudo|pkexec)"
    r"(?:$|[/\s_.-])", re.I)


def process_snapshot(pid, proc=Path('/proc')):
    """Identity, privilege and executable observations; never guess on gaps."""
    p = proc / str(pid)
    try:
        raw = (p/'stat').read_text(); tail = raw.rsplit(')', 1)[1].split()
        if tail[0] in {'Z', 'X'} or int(tail[6]) & 0x4:  # PF_EXITING; verify release separately
            return None
        fields = dict(line.split(':', 1) for line in (p/'status').read_text().splitlines() if ':' in line)
        info = {'pid': int(pid), 'start': tail[19], 'ppid': int(tail[1]), 'state': tail[0],
                'flags': int(tail[6]), 'uids': [int(x) for x in fields['Uid'].split()],
                'caps': int(fields.get('CapEff', '0'), 16),
                'exe': os.readlink(p/'exe'), 'cmdline': (p/'cmdline').read_text().replace('\0', ' '),
                'cgroup': (p/'cgroup').read_text()}
        after = (p/'stat').read_text().rsplit(')', 1)[1].split()
        if after[19] != info['start']:
            raise Refusal('process identity changed during classification')
        return info
    except (FileNotFoundError, ProcessLookupError):
        # A kernel thread has no executable; absence is NOT proof of exit.
        with contextlib.suppress(FileNotFoundError, ProcessLookupError):
            final = (p/'stat').read_text().rsplit(')', 1)[1].split()
            if final[0] in {'Z', 'X'} or int(final[6]) & 0x4:
                return None
        if p.exists():
            raise Refusal(f'PID {pid}: process metadata incomplete') from None
        return None
    except (OSError, KeyError, ValueError, IndexError) as e:
        raise Refusal(f'PID {pid}: process classification unavailable: {e}') from e


def protected_ancestors():
    protected = {1}
    pid = os.getpid()
    while pid > 1 and pid not in protected:
        protected.add(pid)
        item = process_snapshot(pid)
        if not item:
            break
        pid = item['ppid']
    return protected


def classify_process(info, protected, *, container_safe=False):
    if info['pid'] in protected or info.get('flags', 0) & 0x200000:
        return 'protected-control-or-kernel-process'
    if info['state'] == 'D':
        return 'uninterruptible-I/O-needs-device-recovery'
    if CRITICAL.search(info['exe'] + ' ' + info['cmdline']):
        return 'critical-storage-database-package-VM-or-system-component'
    if info.get('caps', 0) or min(info['uids']) < 1000:
        if not container_safe:
            return 'privileged-system-service-needs-supported-stop'
    return 'ordinary-userspace'


def terminate_blocker(record, *, snapshot=process_snapshot, protected=None,
                      still_holds, safe_stop, container_safe, save, receipts, emit,
                      clock=time.monotonic, sleep=time.sleep, term_seconds=15,
                      allow_stop=True):
    """Only an audited PID/start pair may be signalled, via a pinned pidfd.

    Recheck resource ownership and classification before each signal. A stale
    record never authorizes signalling a replacement PID or a changed exec.
    Critical components can use a supported safe stop; they never receive the
    generic forced fallback. Release, rather than command success, is decisive.
    """
    pid, start = record.get('pid'), record.get('start')
    if not isinstance(pid, int) or not start:
        raise Refusal('blocker lacks an exact userspace process identity')
    protected = protected_ancestors() if protected is None else protected
    with contextlib.ExitStack() as stack:
        try:
            fd = os.pidfd_open(pid)
        except ProcessLookupError:
            return
        stack.callback(os.close, fd)
        info = snapshot(pid)
        if not info or info['start'] != str(start):
            return
        if not still_holds(pid, str(start)):
            return
        policy = classify_process(info, protected, container_safe=container_safe(info))
        if not allow_stop:
            emit('BLOCKER_FOUND', pid=pid, start=str(start), exe=info['exe'], policy=policy,
                 resource=record, action='left-running-no-safe-restore')
            raise OneWayStopProhibited(
                f'PID {pid}: automatic stop prohibited without a verified restore recipe'
            )
        receipt = {'pid': pid, 'start': str(start), 'exe': info['exe'], 'policy': policy,
                   'resource': record, 'status': 'STOP_REQUESTED', 'restart_after_resume': 'never'}
        receipts.append(receipt); save()
        emit('BLOCKER_FOUND', **receipt)
        if policy != 'ordinary-userspace':
            if pid in protected or info.get('flags', 0) & 0x200000:
                raise Refusal(f'PID {pid}: {policy}; cannot stop transaction infrastructure')
            if not safe_stop(info, receipt):
                receipt['status'] = 'CRITICAL_STOP_UNAVAILABLE'; save()
                raise Refusal(f'PID {pid}: {policy}; supported safe stop unavailable')
            if still_holds(pid, str(start)):
                receipt['status'] = 'CRITICAL_STOP_INCOMPLETE'; save()
                raise Refusal(f'PID {pid}: {policy}; safe stop did not release resource')
        else:
            for sig, timeout in ((signal.SIGTERM, term_seconds), (signal.SIGKILL, 5)):
                current = snapshot(pid)
                if not current or current['start'] != str(start) or not still_holds(pid, str(start)):
                    break
                if current['exe'] != info['exe'] or classify_process(
                        current, protected, container_safe=container_safe(current)) != 'ordinary-userspace':
                    raise Refusal(f'PID {pid}: classification changed before signal')
                receipt['status'] = sig.name + '_REQUESTED'; save()
                emit('BLOCKER_STOP_REQUESTED', pid=pid, start=str(start), signal=sig.name)
                try:
                    signal.pidfd_send_signal(fd, sig)
                except ProcessLookupError:
                    break
                deadline = clock() + timeout
                while clock() < deadline:
                    current = snapshot(pid)
                    if not current or current['start'] != str(start) or not still_holds(pid, str(start)):
                        break
                    sleep(0.25)
            if still_holds(pid, str(start)):
                receipt['status'] = 'RELEASE_FAILED'; save()
                raise Refusal(f'PID {pid}: resource remains after bounded termination')
        receipt['status'] = 'RELEASED'; save()
        emit('BLOCKER_STOP_RESULT', pid=pid, released=True, policy=policy)

IMPORTANCE = {"nonessential_checkpointable", "nonessential_pauseable", "disposable", "critical"}


def validate_registry(rows: list) -> None:
    seen = set()
    for row in rows:
        name = row.get("name")
        if not name or name in seen:
            raise Refusal("workload names must be unique")
        seen.add(name)
        if row.get("importance") not in IMPORTANCE:
            raise Refusal("unknown workload importance")
        if row.get("importance") == "critical":
            continue
        for key in ("detect", "safe_stop_command", "verify_stopped"):
            cmd = row.get(key)
            if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) for x in cmd):
                raise Refusal(f"{name}: {key} must be an argument array")
            if not cmd[0].startswith("/"):
                raise Refusal("workload executables must be absolute paths")
        if not 1 <= row.get("stop_timeout", 0) <= 240:
            raise Refusal("workload stop timeout outside 1..240 seconds")
        if row.get("restart_after_resume") not in {"never", "if_was_running"}:
            raise Refusal("explicit restore policy required")
        if row.get("restart_after_resume") == "if_was_running" and not row.get("restart_command"):
            raise Refusal("restart policy requires a command")


def quiesce(rows: list, affected: set[str], run: Callable, save: Callable,
            stopped: list, emit: Callable, *, require_restore: bool = False) -> None:
    validate_registry(rows)
    for row in rows:
        if row["importance"] == "critical" or row.get("storage_dependency") not in affected:
            continue
        status = run(row, row["detect"], 15)
        if status.get("active") is False:
            continue
        if status.get("active") is not True:
            raise Refusal(f"{row['name']}: detection incomplete")
        if require_restore and row.get("restart_after_resume") != "if_was_running":
            emit("BLOCKER_FOUND", workload=row["name"], policy=row["importance"],
                 action="left-running-no-safe-restore")
            raise OneWayStopProhibited(
                f"{row['name']}: automatic stop prohibited without a verified restore recipe"
            )
        emit("BLOCKER_FOUND", workload=row["name"], policy=row["importance"])
        receipt = {"name": row["name"], "was_running": True, "status": "STOP_REQUESTED",
                   "restart_after_resume": row["restart_after_resume"]}
        stopped.append(receipt)
        save()  # durable intent BEFORE requesting a checkpoint
        emit("BLOCKER_STOP_REQUESTED", workload=row["name"])
        result = run(row, row["safe_stop_command"], row["stop_timeout"])
        verified = run(row, row["verify_stopped"], 15)
        if result.get("durable_state_preserved") is not True or verified.get("active") is not False:
            receipt.update(status="STOP_FAILED", result=result)
            save()
            raise Refusal(f"{row['name']}: graceful stop not confirmed")
        receipt.update(status="STOPPED", result=result)
        save()
        emit("BLOCKER_STOP_RESULT", workload=row["name"], success=True)


def restore(rows: list, stopped: list, run: Callable, save: Callable) -> None:
    registry = {row["name"]: row for row in rows}
    for receipt in stopped:
        if receipt.get("status") != "STOPPED":
            continue
        if receipt.get("restart_after_resume") == "never":
            receipt["status"] = "LEFT_STOPPED_BY_POLICY"
        else:
            row = registry.get(receipt["name"])
            if not row or not row.get("restart_command"):
                raise Refusal("restore policy disappeared")
            receipt["status"] = "RESTORE_REQUESTED"
            save()
            run(row, row["restart_command"], row["stop_timeout"])
            if run(row, row["detect"], 15).get("active") is not True:
                raise Refusal("workload restore not confirmed")
            receipt["status"] = "RESTORED"
        save()
