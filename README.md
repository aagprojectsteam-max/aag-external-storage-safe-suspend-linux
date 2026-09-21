# External Storage Safe Suspend for Linux

[![CI](https://github.com/aagprojectsteam-max/aag-external-storage-safe-suspend-linux/actions/workflows/ci.yml/badge.svg)](https://github.com/aagprojectsteam-max/aag-external-storage-safe-suspend-linux/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**v1.4.1 is production-qualified on the tested reference platform.** This project
coordinates Linux Suspend, physical lid-close sleep and plain Hibernate/S4 with
external-storage safety, DATA/UGREEN preparation and restoration, userspace
blocker handling, transaction recovery, FM350/T700 cellular recovery and AAG
LockLock interoperability. v1.4.1 requalifies the updated reference Suspend and S4 runtime.

Qualification applies to the [tested configuration](docs/TESTED-HARDWARE.md).
The generic Linux design and portable installer do not establish compatibility
with arbitrary hardware, firmware or existing power-management integrations.

The v1.4.1 requalification also records a platform-firmware boundary on the reference HP EliteBook. Repeated kernel s2idle cycles had zero S0ix residency on the preceding firmware. After updating to HP W70 01.10.00 with Intel ME 18.0.21.2801 and USB-C/PD 2.9.0, the first controlled Suspend recorded 22.954544 seconds of matching hardware and PMC S0ix residency. A subsequent physical Hibernate powered off, resumed the same boot image, reached COMPLETE, restored both saved USB Clone profiles and recovered cellular service. This is a qualification of the tested firmware stack, not a universal firmware claim.

## Why this exists

External USB/NVMe devices may disconnect and re-enumerate across a power
transition. Entering sleep while applications still own their storage can cause
a failed transition or risk data corruption. The reference stack coordinates:

**workload handling → storage preparation → power transition → verified resume
→ device/storage restoration → consumer restoration → transaction completion.**

For Hibernate, the transition includes image creation, complete S4 power-off and
confirmed image resume. Storage, WWAN and required consumers must recover before
the transaction can become `COMPLETE`; a successful image resume alone is not
full acceptance.

## Choose the installation profile

| Profile | Requirements and behavior |
|---|---|
| Qualified reference transaction stack | Existing reviewed AAG V2 storage guard, recovery auditor, FM350/T700 integration and LockLock; private configuration pins adapters by SHA256. Provides the accepted lid-close/Suspend behavior. |
| Reference Hibernate extension | Explicit v1.4.1 `hibernate` installer route on that reference stack, with private pinned configuration, passing readiness gates and exact rollback. Adds the separately qualified S4 lifecycle. |
| Portable storage coordinator | Public v1.x install/upgrade interface for a selected external disk. Retains its conservative read-only USBClone gate and separate opt-in portable Hibernate integration. It does not automatically become the qualified transaction stack. |

The `.run` and source archive contain the reference stack and portable profile.
They are distinct owners of the native suspend graph: do not layer them together.
The reference Hibernate extension belongs to the reference stack. Unreviewed
legacy helpers and machine configuration are not bundled. See
[installation and prerequisites](docs/INSTALLATION.md).

## Transaction behavior

A previous architecture could retain stale failure state after storage
preparation failed. Repeated lid requests could consume shared retry state and
ultimately reach fail-safe poweroff. Transaction ownership, stale-state
reconciliation and transaction-scoped retries correct that failure path.

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

## Hibernate and cellular recovery

Plain S4 requires kernel hibernation support, adequate available memory and
swap/image capacity, a correct resume device and offset where applicable,
matching initramfs resume support, safe storage preparation and compatible
recovery integrations. The reference used **64 GiB RAM and a 72 GiB swapfile**;
these are validation context, not universal sizing requirements. Read the
[Hibernate guide](docs/HIBERNATE.md) before configuring another host.

`aag-hibernate-transaction-finish.service` is the **single S4 WWAN recovery
owner**. It waits for callback lock release and stable modem enumeration before
bounded restoration of the saved cellular policy. Competing recovery services
could race on locks or device generations. Acceptance verified device and driver
return, control ports, NetworkManager, the saved WWAN connection and real mobile
DNS/HTTPS without reboot. [FM350/T700 recovery](docs/T700-WWAN-HIBERNATE.md)
retains GNSS on demand.

## LockLock interoperability

| Lid-ignore state | Expected lid-close behavior |
|---|---|
| OFF | Normal coordinated lid-close Suspend |
| ON | Lid closure intentionally ignored |
| Disabled after ON | Normal lid-close behavior restored |

These refer to LockLock's lid-ignore control, independently of its input-lock
controls. Stale inhibitors or state must not poison later transactions. See
[the integration boundary](docs/INTEGRATIONS.md#locklock-and-input-lock).

## Final accepted validation

| Check | Accepted result |
|---|---|
| Automated tests / CI | **293 PASS / PASS** |
| Physical lid-close Suspend / long Suspend | PASS / PASS |
| Real S4 image resume / desktop and input recovery | PASS / PASS |
| DATA / UGREEN / required consumer restoration by saved policy | PASS |
| FM350/T700 / mobile connectivity without reboot | PASS |
| LockLock OFF/ON interoperability | PASS |
| Terminal transaction / stale fence / stale WWAN lock | COMPLETE / NONE / NONE |
| Source/installed parity | **37 of 37** |
| Exact rollback / anonymous public asset verification | VERIFIED / 12 assets |

The [normalized acceptance record](docs/ACCEPTANCE.md) separates current
qualification from historical cycles. The preceding S4 WWAN failure requiring a
manual reboot remains a failure; user-interrupted Suspend cycles remain invalid
for acceptance.

**Known limitations:** transient modem recovery errors occurred after S4, but
usable service recovered automatically in approximately two minutes. This is
functional recovery, not instantaneous recovery or a firmware-error-free claim.
The memory gate required the Windows guest to be shut down. UGREEN passed its
safe-release policy; required automatic external-partition remounts are not
qualified. Other hardware/firmware and long-term endurance remain unqualified.
Detailed forensic reports and private identifiers stay local.

## Verify and install

Download the `.run`, `SHA256SUMS`, and `release-manifest.json` from the
[v1.4.1 release](https://github.com/aagprojectsteam-max/aag-external-storage-safe-suspend-linux/releases/tag/v1.4.1):

```bash
sha256sum --ignore-missing -c SHA256SUMS
chmod +x aag-external-storage-safe-suspend-linux-v1.4.1.run
```

For an already reviewed reference stack, use its private configuration and a
private deployment-report directory:

```bash
sudo ./aag-external-storage-safe-suspend-linux-v1.4.1.run transaction   --config /etc/aag-sleep-transaction/config.json   --report /var/lib/aag-sleep-transaction/deployment --check
sudo ./aag-external-storage-safe-suspend-linux-v1.4.1.run transaction   --config /etc/aag-sleep-transaction/config.json   --report /var/lib/aag-sleep-transaction/deployment
```

An already accepted production deployment does not need reinstalling solely
because these bytes were published. A new host needs an inspected configuration
and compatible pinned dependencies first; the portable config format cannot be
used for the reference adapter.

For a fresh **portable** installation, the existing interface remains:

```bash
sudo ./aag-external-storage-safe-suspend-linux-v1.4.1.run install   --device /dev/disk/by-id/your-external-backup-disk   --protect-mount /mnt/data --timeshift
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
enabled or accepted. [Release notes](docs/RELEASE-NOTES-v1.4.1.md) describe the
precise release scope and limitations.

## Development

```bash
make check
make release-acceptance
```

Release validation pins both accepted reference runtime manifests by SHA256.
The reference initializer remains at its accepted v1.3.1 bytes while portable
release metadata reports v1.4.1. Packaging and tests use normal lint/format gates.
Built-asset acceptance includes the explicit Hibernate route and exact rollback;
all installation tests use synthetic fixtures and isolated installation roots. Private `/reports/` is excluded from
Git, release payloads and public scans; it is never uploaded as an asset.

Licensed under [MIT](LICENSE). Report storage-safety issues using
[SECURITY.md](SECURITY.md).
