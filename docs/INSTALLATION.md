# Installation profiles in v1.4.0

The verified `.run` asset contains the portable coordinator and the qualified
reference transaction adapter. Choose one graph owner; the installer does not
convert portable deployments into the reference stack.

## Qualified existing reference stack

This profile requires an already reviewed AAG V2 storage guard/recovery auditor,
T700 helper and its core/configuration, expected systemd units, and the LockLock
interface. These dependencies are local integration contracts; their private
configuration and unreviewed legacy implementations are not public payload.
The adapter pins the recovery and T700 helpers by SHA256 and fails on drift.
It is not a clean-room replacement for these prerequisites on arbitrary Linux.

A private root-owned host configuration supplies:

- `recovery_adapter` and `t700_adapter`: inspected absolute `path` and `digest`.
- `workloads`: declarative detection, safe-stop, verification and restart policy.
  AAG Knowledge can use its local safe-stop API; keep its private paths local.
- `usbclone`: the inspected helper `command`, its `sha256`, and exact `profiles`
  mapping to backing files. Never copy another machine's image paths.
- Optional `critical_stops`: exact supported guest/container shutdown mechanisms.
- Optional notification user/UID, confined to local policy.

Use the deployment checklist and schema visible in
[the adapter contract](TRANSACTION-ADAPTER.md). The config is not interchangeable
with the portable JSON format. Have the existing owner review dependencies and
policy; hashing an arbitrary script does not establish its safety or interface.

After checksum verification, run the packaged `transaction` command with
`--config <private-host-config> --report <private-report-directory> --check`.
The check saves local host audits; it does not attest that every policy is
universally safe. Deployment without `--check` applies the accepted mapping,
backs up exact files and modes, verifies units and hashes, reloads definitions,
and starts only the relevant AAG reconciliation service. Idle LockLock can be
reloaded when compatibility code changes. No suspend or reboot is requested.
The deployed configuration is mode 0600; code is 0644, entrypoint 0755.

The currently accepted production installation need not be changed merely to
publish or download this release.

## Portable fresh install or upgrade

The established `install --device <whole-disk-by-id> --protect-mount /mnt/data`
interface remains. Add `--timeshift` only if needed. Existing v1.0.0 through
v1.3.1 installations use the asset without arguments to upgrade in place.
Supported configuration and the existing portable ordinary/Hibernate contracts
are preserved. Automatic generic blocker termination is a feature of the
explicit reference transaction profile, not the portable read-only gadget gate.

Review [upgrade](UPGRADING.md), [rollback](ROLLBACK.md), and the README's scoped
validation before first use on another machine.

## Isolated package verification

Portable `--root <temporary-prefix> --test-mode install --config <synthetic-config>`
validates the complete portable installer without calling the live system.
The `transaction` command also accepts an explicit non-root `--root` with
`--test-mode`. It installs the same reference mapping into that prefix, applies
the same narrow LockLock transformation to a fixture, checks file hashes/modes,
and records an exact rollback snapshot. It never runs host adapters or systemd.

Its result is labeled `ISOLATED_PAYLOAD_ONLY`: it proves packaging and rollback,
not fresh physical hardware acceptance or compatibility of an arbitrary host's
legacy stack. The automated and built-asset acceptance cover both profiles.

## Qualified reference Hibernate extension

After the reference prerequisites are established, use the verified v1.4.0 asset:

```bash
sudo ./aag-external-storage-safe-suspend-linux-v1.4.0.run hibernate --config <private-hibernate-config> --gates <verified-gates-json> --baseline <accepted-baseline-json> --report <private-report-directory>
```

The baseline JSON is a list of installed absolute paths and expected SHA256
hashes. The gate JSON and runtime configuration must match the reviewed simulation
and regression results for this host. Do not fabricate gates to enable deployment.
This route invokes the unchanged qualified S4 deployer; it never requests sleep.
Use `hibernate-readiness` afterward to inspect the live GO gates. The detailed
[Hibernate contract](hibernate-transaction.md) defines the hardware, memory,
resume image, owner and restoration scope. An existing accepted installation
needs no redeployment for release publication.

The Hibernate route also accepts `--root <temporary-prefix> --test-mode` for
isolated payload/rollback validation. That result is not physical qualification
of the target host. Never use the portable install command over a reference graph.
