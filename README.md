# External Storage Safe Suspend for Linux

[![CI](https://github.com/aagprojectsteam-max/aag-external-storage-safe-suspend-linux/actions/workflows/ci.yml/badge.svg)](https://github.com/aagprojectsteam-max/aag-external-storage-safe-suspend-linux/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**v1.3.0 adds the physically validated AAG sleep transaction adapter.** On the
qualified reference installation, normal lid closure now completes one owned
suspend transaction, safely resolves eligible blockers, verifies actual kernel
sleep, and restores storage, modem policy and the desktop session.

A storage-preparation refusal could leave failure state unreconciled. Repeated
lid-triggered suspend requests could then exhaust the retry budget and reach
the fail-safe poweroff path. The new adapter owns preparation through final
completion, reconciles stale failures, scopes retries to that transaction, and
coalesces overlapping lid requests. Ordinary recoverable blockers no longer
reach poweroff merely because a timer expired or a retry was consumed.

## Choose the installation profile

| Profile | Requirements and behavior |
|---|---|
| Qualified transaction adapter, new in v1.3.0 | Existing reviewed AAG V2 storage guard, recovery auditor, T700 integration and LockLock; private host configuration pins adapters by SHA256. Includes the accepted end-to-end remediation. |
| Portable storage coordinator | Existing public v1.x install/upgrade interface for a selected external disk. Retains its conservative read-only USBClone gate and separate opt-in reference Hibernate support. It does not automatically become the qualified transaction stack. |

The `.run` and source archive contain both profiles. They are distinct owners of
the native suspend graph: do not layer them together or assume that a normal
portable upgrade enables the new reference-host adapter. Unreviewed legacy
helpers and machine configuration are not bundled. See
[installation and prerequisites](docs/INSTALLATION.md).

## Transaction behavior

- A single transaction owns preparation, native suspend, recovery and completion.
  Stale `FAILURE_PENDING` is reconciled; retry state belongs to the transaction.
- Overlapping callbacks cannot replace a live owner. Recovery holds the low-level
  lid inhibitor to prevent repeated logind requests from creating a loop.
- Known checkpoint/stop APIs are preferred. Unknown ordinary userspace blockers
  need no application allowlist: exact resource ownership and process identity
  are revalidated before TERM, a bounded wait and a last-resort safe KILL.
- Critical system components, databases, filesystem infrastructure and VMs are
  never blindly killed. A VM requires a configured supported shutdown method;
  unsafe or incomplete release remains an explicit exception.
- Known USB Clone profiles are stopped only after guest, backing-file and mount
  ownership is released. Active guest USB is never forcibly detached.
- Internal DATA remains mounted and identity-checked. External UGREEN storage
  retains clean unmount, namespace/owner auditing, generation checks and targeted
  automount suppression through terminal verification.
- Command success alone is not sleep: actual kernel entry/exit, suspend counters
  and hardware residency establish the result. A false resume cannot complete.
- Real thermal/battery safety supervision remains active. Timer expiry or one
  consumed retry alone does not order a shutdown.
- Resume restores saved device policy and required eligible consumers. Workloads
  with explicit user-stop semantics and safely terminated applications remain
  stopped for manual restart.

[Architecture](docs/ARCHITECTURE.md) · [adapter contract](docs/TRANSACTION-ADAPTER.md)
· [troubleshooting](docs/TROUBLESHOOTING.md)

## Validated scope

The accepted reference implementation passed **201 automated tests** before
physical acceptance. The release candidate passes **209 tests**, including packaging and maintenance
regressions. One final physical lid-close/open test under an existing
workload recorded **112.615150 seconds** of hardware/PMC low-power sleep, one
suspend transaction, successful resume, DATA/UGREEN restoration and **21 passing
FM350/T700 checks**. There was no retry loop, stale failure fence or fail-safe
poweroff; all 15 installed/source manifest entries matched. The desktop recovered.

This validates the tested Ubuntu/GNOME/s2idle reference stack, not every Linux
kernel, enclosure, firmware or workload. A prior user-interrupted cycle was
excluded from acceptance. Only normalized results are public; raw journals,
identifiers, private policies and forensic reports remain local.

The optional checkpoint API was unavailable during the final test; the safe
resource-audit fallback succeeded. Cellular reconnection and IP assignment were
verified, but separate mobile Internet traffic was not tested. Long-duration and
Hibernate behavior were not requalified by this release's lid test.

## Verify and install

Download the `.run`, `SHA256SUMS`, and `release-manifest.json` from the
[v1.3.0 release](https://github.com/aagprojectsteam-max/aag-external-storage-safe-suspend-linux/releases/tag/v1.3.0):

```bash
sha256sum --ignore-missing -c SHA256SUMS
chmod +x aag-external-storage-safe-suspend-linux-v1.3.0.run
```

For an already reviewed reference stack, use its private configuration and a
private deployment-report directory:

```bash
sudo ./aag-external-storage-safe-suspend-linux-v1.3.0.run transaction   --config /etc/aag-sleep-transaction/config.json   --report /var/lib/aag-sleep-transaction/deployment --check
sudo ./aag-external-storage-safe-suspend-linux-v1.3.0.run transaction   --config /etc/aag-sleep-transaction/config.json   --report /var/lib/aag-sleep-transaction/deployment
```

An already accepted production deployment does not need reinstalling solely
because these bytes were published. A new host needs an inspected configuration
and compatible pinned dependencies first; the portable config format cannot be
used for the reference adapter.

For a fresh **portable** installation, the existing interface remains:

```bash
sudo ./aag-external-storage-safe-suspend-linux-v1.3.0.run install   --device /dev/disk/by-id/your-external-backup-disk   --protect-mount /mnt/data --timeshift
```

Omit `--timeshift` when unused. Protect `/` and every important internal mount.
The device path is for discovery; stable serial/USB/UUID identity is stored in
local configuration. Portable v1.0.0 through v1.2.1 upgrades use the verified
asset without arguments and preserve supported configuration.

## Upgrade and rollback

[Upgrade instructions](docs/UPGRADING.md) distinguish the two profiles.
[Rollback instructions](docs/ROLLBACK.md) describe exact snapshots and refuse
arbitrary history replacement. The portable maintenance commands remain:

```bash
aag-safe-suspend --version
sudo aag-safe-suspend health-check
sudo aag-safe-suspend rollback
```

The reference adapter uses its recorded deployment backup instead. Neither
installer performs a suspend test or reboot. The reference deployer may start
the relevant AAG boot-reconciliation unit and reload idle LockLock when required.
Do not run another physical test merely to publish an already accepted runtime.

Plain Hibernate remains opt-in and separately qualified; see
[Hibernate](docs/HIBERNATE.md). Suspend-then-hibernate and hybrid sleep are not
enabled or accepted. [Release notes](docs/RELEASE-NOTES-v1.3.0.md) describe the
precise release scope and limitations.

## Development

```bash
make check
make release-acceptance
```

Release validation pins the twelve accepted runtime/entrypoint/unit files by
SHA256. Those files are deliberately not reformatted during publication;
packaging and test code use the normal lint/format gates. The qualified adapter package version changes as metadata. The portable
maintenance writer additionally fixes stale bytecode during rapid rollback;
that change does not alter the qualified sleep transaction. Tests use synthetic
fixtures and isolated installation roots. Private `/reports/` is excluded from
Git, release payloads and public scans; it is never uploaded as an asset.

Licensed under [MIT](LICENSE). Report storage-safety issues using
[SECURITY.md](SECURITY.md).
