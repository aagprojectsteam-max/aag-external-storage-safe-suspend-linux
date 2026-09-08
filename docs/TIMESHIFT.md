# Timeshift integration

Timeshift integration is optional. With `--timeshift`, the installer uses
package-aware `dpkg-divert` entries for `/usr/bin/timeshift` and
`/usr/bin/timeshift-gtk`. Project wrappers:

1. acquire the transaction lock;
2. refuse a new start unless the fence is `IDLE`;
3. register PID plus process start time;
4. execute the diverted original binary; and
5. remove the registration on exit.

An already running registered job is allowed to finish under the
progress-aware policy. A site may configure an application-supported graceful
quiesce argv; the default sends no signal. Direct resource audits remain
authoritative even when wrapper state reports no job.

Package upgrades continue to target the diverted original. Uninstall verifies
project wrapper hashes, refuses an active fence or conflicting job, removes the
wrapper, and reverses the diversion so the package binary returns to its normal
path. An interrupted uninstall restores the diversion before restoring the
wrapper, preventing the original binary from being overwritten.

Limitations: commands that deliberately bypass both public Timeshift entry
points are not fenced by the wrapper, though their filesystem/process ownership
should still block release. Other backup applications should use the generic
integration hooks or contribute a separately tested adapter.
