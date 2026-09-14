"""Narrow LockLock contract for the installed AAG transaction adapter.

This is installed beside input_lock_safety.py. LockLock remains the sole owner
of intentional lid-ignore and its existing thermal/battery protection.
"""
import json
from pathlib import Path
import subprocess


def stack_ready(run=subprocess.run):
    checks = {
        'systemd-suspend.service': ('aag-storage-sleep-v2.service', 'aag-suspend-failure-failsafe.service',
                                    '/usr/lib/systemd/systemd-sleep suspend', 'aag-sleep-transaction native-pre',
                                    'aag-sleep-transaction native-post'),
        'aag-storage-sleep-v2.service': ('aag-ugreen-sleep-guard-v2 pre', 'aag-sleep-transaction begin',
                                       'aag-sleep-transaction verify-release'),
        'aag-suspend-failure-failsafe.service': ('aag-sleep-transaction failure-recover',
                                               '--what=sleep:handle-lid-switch'),
        'aag-t700-resume-check.service': ('aag-sleep-transaction resume-verify-device', 'aag-sleep-transaction resume-verify-terminal'),
        'input-lock-protect.service': ('input_lock_safety.py',),
    }
    try:
        for unit, tokens in checks.items():
            p = run(['/usr/bin/systemctl', 'show', unit,
                     '--property=LoadState,Requires,After,OnFailure,ExecStart,ExecStartPre,ExecStartPost,ExecStopPost'],
                    capture_output=True, text=True, timeout=3, check=False)
            if p.returncode or 'LoadState=loaded' not in p.stdout or not all(t in p.stdout for t in tokens):
                return False
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def resume_confirmed(requested_at):
    try:
        d = json.loads(Path('/run/aag-ugreen-safety-r1/last-terminal.json').read_text())
        return (d.get('boot_id') == Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                and d.get('state') == 'COMPLETE' and d.get('actual_sleep') is True
                and d.get('kernel_cycle') is True and float(d.get('started_monotonic', -1)) >= requested_at)
    except (OSError, ValueError, TypeError):
        return False
