# Troubleshooting

## Installer refuses a systemd job

The installer continuously observes the systemd job queue. Power, storage,
project-owned, and unknown jobs can invalidate the install snapshot and fail
closed. Wait for the named job to finish, investigate it, and rerun only when
you understand its role. Numeric job IDs are transient and are never
whitelisted. Known bounded maintenance services are allowed semantically.

## Target identity is incomplete

Use a whole-disk `/dev/disk/by-id/...` link for initial discovery. Every target
partition must contain a filesystem with a unique UUID. A serial mismatch,
changed partition set, missing USB properties, ambiguous disk, or changed
`diskseq` is a safety refusal, not a reason to substitute `/dev/sdX` in the
configuration.

## Suspend is blocked by an owner

Close software intentionally using the target. Check VMs, containers, loop
images, NBD exports, device-mapper stacks, shells whose cwd is on the disk, and
memory-mapped files. Do not force-unmount or kill an unknown process. Preserve
the incident record under `/var/lib/aag-external-storage-safe-suspend/incidents`
after sanitizing it before any public report.

## Resume fence remains armed

Run:

```bash
sudo /usr/local/libexec/aag-safe-suspend status
sudo journalctl -u 'aag-external-storage-safe-suspend*' --since today
```

Do not manually remove the marker while the lid is closed or the external disk
may be owned. Open the lid, keep the machine ventilated, and determine whether
the failure service reached a safe lid-open recovery. Redact evidence before
sharing it.

## Timeshift no longer starts

Inspect project state first. A non-IDLE fence intentionally rejects new backup
starts. Also verify diversion ownership with `dpkg-divert --listpackage
/usr/bin/timeshift`. Do not move diverted binaries manually; use the verified
release uninstaller or report a private storage-safety issue.

## Uninstall refuses

Uninstall requires no active fence, IDLE project state, unchanged project-owned
files, and a clear semantic job guard. The refusal preserves the installation.
Resolve the exact conflict rather than deleting project state or using package
force options.
