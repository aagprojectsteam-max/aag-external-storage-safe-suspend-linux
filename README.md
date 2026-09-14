# External Storage Safe Suspend for Linux

[![CI](https://github.com/aagprojectsteam-max/aag-external-storage-safe-suspend-linux/actions/workflows/ci.yml/badge.svg)](https://github.com/aagprojectsteam-max/aag-external-storage-safe-suspend-linux/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**v1.4.0 adds qualified reference Hibernate with automatic cellular recovery.**
One controlled S4 cycle restored the image, mobile DNS/HTTPS and the normal desktop
session without reboot. Recovery has one owner and waits for callback lock release
and stable modem enumeration. The accepted Suspend v1.3.1 runtime is preserved.
See [the S4 qualification and limitations](docs/RELEASE-NOTES-v1.4.0.md).

**v1.3.1 fixes checkpoint verification for collected user services.** A transient
service that saved its checkpoint and exited can disappear from systemd before
verification. The adapter now confirms that the reachable user manager reports
the unit absent and inactive before accepting the stop. Missing bus access or
incomplete observations still use the existing safe fallback.

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
| Reference Hibernate extension, new in v1.4.0 | Explicit `hibernate` installer route on the qualified reference stack, with private pinned configuration, passing gates and exact rollback. |
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

The original Suspend qualification and its separately accepted v1.3.1 regression
matrix remain preserved. Hibernate was independently qualified on the reference
stack after **283 passing automated tests**: one real S4 image resume, exactly one
WWAN recovery owner, actual mobile DNS/HTTPS through the cellular interface,
DATA/UGREEN and consumer restoration by saved policy, and user-confirmed desktop
and input recovery without reboot. All **35 source/installed entries** matched.
No stale fence, retry loop or fail-safe poweroff remained.

Transient modem/PCIe errors still occur during resume; the owner recovered usable
service in approximately two minutes. The preceding S4 WWAN failure requiring a
manual reboot remains a separate failure. Earlier interrupted Suspend cycles are
also excluded. The tested memory gate required the Windows guest to be shut down.
This result does not qualify other hardware/firmware, long-term endurance or
required automatic external-partition remounts. Private reports and identifiers
are excluded from the public release.

## Verify and install

Download the `.run`, `SHA256SUMS`, and `release-manifest.json` from the
[v1.4.0 release](https://github.com/aagprojectsteam-max/aag-external-storage-safe-suspend-linux/releases/tag/v1.4.0):

```bash
sha256sum --ignore-missing -c SHA256SUMS
chmod +x aag-external-storage-safe-suspend-linux-v1.4.0.run
```

For an already reviewed reference stack, use its private configuration and a
private deployment-report directory:

```bash
sudo ./aag-external-storage-safe-suspend-linux-v1.4.0.run transaction   --config /etc/aag-sleep-transaction/config.json   --report /var/lib/aag-sleep-transaction/deployment --check
sudo ./aag-external-storage-safe-suspend-linux-v1.4.0.run transaction   --config /etc/aag-sleep-transaction/config.json   --report /var/lib/aag-sleep-transaction/deployment
```

An already accepted production deployment does not need reinstalling solely
because these bytes were published. A new host needs an inspected configuration
and compatible pinned dependencies first; the portable config format cannot be
used for the reference adapter.

For a fresh **portable** installation, the existing interface remains:

```bash
sudo ./aag-external-storage-safe-suspend-linux-v1.4.0.run install   --device /dev/disk/by-id/your-external-backup-disk   --protect-mount /mnt/data --timeshift
```

Omit `--timeshift` when unused. Protect `/` and every important internal mount.
The device path is for discovery; stable serial/USB/UUID identity is stored in
local configuration. Portable v1.0.0 through v1.3.1 upgrades use the verified
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
enabled or accepted. [Release notes](docs/RELEASE-NOTES-v1.4.0.md) describe the
precise release scope and limitations.

## Development

```bash
make check
make release-acceptance
```

Release validation pins both accepted reference runtime manifests by SHA256.
The reference initializer remains at its accepted v1.3.1 bytes while portable
release metadata reports v1.4.0. Packaging and tests use normal lint/format gates.
Built-asset acceptance includes the explicit Hibernate route and exact rollback;
all installation tests use synthetic fixtures and isolated installation roots. Private `/reports/` is excluded from
Git, release payloads and public scans; it is never uploaded as an asset.

Licensed under [MIT](LICENSE). Report storage-safety issues using
[SECURITY.md](SECURITY.md).
