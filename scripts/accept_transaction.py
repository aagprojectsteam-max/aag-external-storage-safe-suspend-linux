#!/usr/bin/python3
"""One safe, open-lid RTC-backed production acceptance cycle.

Run only after automated tests. Uses the normal systemd suspend path, never
bypasses the preparation guard, and removes its temporary RTC unit override.
"""
from __future__ import annotations
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, '/usr/local/lib/aag-sleep-transaction')
from aag_safe_suspend import production, transaction


def transition_pending():
    # logind's delay-inhibitor phase can precede any systemd job. Keep the
    # wake override until that phase AND all relevant units have finished.
    try:
        p=production.run(['/usr/bin/busctl','get-property','org.freedesktop.login1',
            '/org/freedesktop/login1','org.freedesktop.login1.Manager','PreparingForSleep'],timeout=5)
        if p.stdout.strip()!='b false':return True
        return any(production.properties(unit)['ActiveState'] not in ('inactive','failed')
                   or production.properties(unit).get('Job','').strip() not in ('','0')
                   for unit in (production.V2,production.NATIVE,production.VERIFY,production.FAILURE))
    except Exception:return True


def execute(report: Path, inject_stale=True):
    if os.geteuid()!=0:raise RuntimeError('root required')
    host=production.Host()
    start=time.time()
    result={'start':start,'status':'NOT_RUN','lid_mode':'open','rtc_seconds':25,'reboots':0}
    runtime=Path('/run/systemd/system/systemd-suspend.service.d/99-zz-aag-acceptance.conf')
    alarm=Path('/sys/class/rtc/rtc0/wakealarm')
    if runtime.exists():raise RuntimeError('existing acceptance override requires review')
    for unit in (production.V2,production.NATIVE,production.VERIFY,production.FAILURE,production.RETRY):
        p=production.properties(unit)
        if p['ActiveState'] not in ('inactive','failed') or p.get('Job','').strip() not in ('','0'):
            raise RuntimeError('active transaction: '+unit)
    if host.r.lid_state()!='open' or production.lid_ignored():
        raise RuntimeError('open lid with lid-ignore OFF required for controlled test')
    thermal=host.r.thermal_sample()
    if thermal['state']!='NORMAL' or production.battery().get('critical'):
        raise RuntimeError('safe idle thermal/battery preconditions not met')
    prior_alarm=alarm.read_text().strip()
    if prior_alarm and int(prior_alarm)>time.time():raise RuntimeError('existing future RTC alarm must be preserved')
    clones=host.clone_inventory();audit=host.clone_audit(clones)
    if not audit.get('AUDIT_COMPLETE'):
        (report/'acceptance-blockers.json').write_text(json.dumps(host.enrich(audit.get('records',[])),indent=2))
        raise RuntimeError('virtual USB audit incomplete; no acceptance suspend attempted')
    # Exact live userspace blockers are intentionally part of acceptance:
    # production must close them through its checked policy before any detach.
    (report/'acceptance-initial-blockers.json').write_text(json.dumps(host.enrich(audit.get('records',[])),indent=2))
    ugreen=host.audit_storage()
    if not ugreen.get('AUDIT_COMPLETE'):
        (report/'acceptance-storage-blockers.json').write_text(json.dumps(ugreen,indent=2))
        raise RuntimeError('external storage consumers remain; no acceptance suspend attempted')
    result.update(clones_before=clones,stats_before=production.stats(),thermal_before=thermal['state'])
    (report/'inhibitors-acceptance-before.txt').write_text(production.run(['systemd-inhibit','--list','--no-pager']).stdout)
    fixture = None
    try:
        with host.r.state_lock():host.value=host.r.load_state()
        if host.value.get('state')!='IDLE':host.reconcile()
        # A test-owned ordinary process with only a cwd reference exercises
        # generic discovery/TERM without writing anything to external storage.
        mounts = sorted({x['target'] for x in ugreen['records']
                         if x.get('kind') == 'mount' and x.get('owner') == 'HOST'})
        if mounts and host.cfg.get('notification_user'):
            fixture = subprocess.Popen(['/usr/sbin/runuser', '-u', host.cfg['notification_user'], '--',
                '/usr/bin/python3', '-c',
                'import os,sys,time; os.chdir(sys.argv[1]); print(os.getpid(),flush=True); time.sleep(600)',
                mounts[0]], cwd='/', text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            pid = fixture.stdout.readline().strip()
            if not pid.isdecimal():
                raise RuntimeError('test cwd fixture did not start: ' + fixture.stderr.read()[-500:])
            result['ordinary_blocker_fixture'] = {'pid': int(pid), 'cwd': mounts[0], 'writes': False}
        if inject_stale:
            with host.r.state_lock():
                if host.r.load_state().get('state')!='IDLE':raise RuntimeError('acceptance requires reconciled IDLE state')
                host.value=transaction.new(host.r.boot_id(),'acceptance-expired-owner')
                transaction.fail(host.value,'ACCEPTANCE_STALE_FENCE','synthetic stale state; no resources changed')
                host.value['retry_count']=1
                host.value['retry_state']='RETRY_CONSUMED'
                host.r.arm_automount_suppression(host.value)
                host.save()
                result['stale_episode']=host.value['episode']
        runtime.parent.mkdir(parents=True,exist_ok=True)
        runtime.write_text('[Service]\nExecStartPre=/usr/sbin/rtcwake -m no -s 25\n')
        production.run(['systemctl','daemon-reload'])
        print('ACCEPTANCE_BEGIN: normal suspend; RTC alarm armed only after successful preparation',flush=True)
        production.run(['systemctl','--no-block','suspend'])
        deadline=time.monotonic()+480
        last=None
        while time.monotonic()<deadline:
            time.sleep(2)
            with host.r.state_lock():value=host.r.load_state()
            stage=(value.get('state'),value.get('stage'),value.get('reason'))
            if stage!=last:
                print('ACCEPTANCE_STAGE '+json.dumps(stage),flush=True);last=stage
            current=production.stats()
            if current['success']>result['stats_before']['success'] and value.get('state')=='IDLE':
                terminal=json.loads((host.r.RUNTIME/'last-terminal.json').read_text())
                result.update(stats_after=current,terminal=terminal,
                    status='PASS' if terminal.get('actual_sleep') and terminal.get('state')=='COMPLETE' else 'FAIL')
                break
            fresh = value.get('episode') != result.get('stale_episode')
            if fresh and value.get('state')=='FAILURE_PENDING' and production.properties(production.FAILURE)['ActiveState'] in ('inactive','failed'):
                result.update(status='FAIL',failure=value,stats_after=current);break
            idle_units = all(production.properties(unit)['ActiveState'] in ('inactive','failed')
                             for unit in (production.V2,production.NATIVE,production.FAILURE))
            if value.get('state')=='IDLE' and value.get('last_terminal')=='RECOVERED' and idle_units and current['success']==result['stats_before']['success']:
                result.update(status='FAIL',failure='suspend aborted and safely recovered',stats_after=current);break
        else:result.update(status='TIMEOUT',failure=value,stats_after=production.stats())
    finally:
        cleanup_deadline=time.monotonic()+120
        while transition_pending() and time.monotonic()<cleanup_deadline:
            time.sleep(1)
        pending=transition_pending()
        if fixture is not None:
            if fixture.poll() is None:
                fixture.terminate()  # only this test's runuser supervisor/child
                try:fixture.wait(timeout=5)
                except subprocess.TimeoutExpired:fixture.kill();fixture.wait(timeout=3)
            result['fixture_exit'] = fixture.returncode
            fixture.stdout.close();fixture.stderr.close()
        if pending:
            result.update(status='FAIL',cleanup='deferred: active transition retains timed wake safety')
        else:
            with contextlib.suppress(FileNotFoundError):runtime.unlink()
            production.run(['systemctl','daemon-reload'])
            # No previous future alarm was allowed; remove only our test alarm.
            if alarm.exists():alarm.write_text('0\n')
        result['end']=time.time()
        (report/'real-acceptance.json').write_text(json.dumps(result,indent=2))
        for name,args in [('acceptance-journal.txt',['journalctl','--since','@'+str(int(start)),'--no-pager','-o','short-iso']),
                          ('inhibitors-acceptance-after.txt',['systemd-inhibit','--list','--no-pager'])]:
            (report/name).write_text(production.run(args,timeout=30).stdout)
        for p in report.glob('*'):
            with contextlib.suppress(OSError):os.chown(p,1000,1000)
    print('REAL_ACCEPTANCE='+result['status'],flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(execute(Path(sys.argv[1])))
