# External Storage Safe Suspend v1.3.0

This stable minor release publishes the accepted AAG sleep transaction adapter
and adds a verified `transaction` route to the existing `.run` installer.
Existing portable v1.x installation commands and schemas remain compatible.

## Problem and changes

A storage-preparation refusal could leave failure state unreconciled. Repeated
lid-triggered suspend requests could then exhaust the retry budget and reach
the fail-safe poweroff path.

The adapter makes preparation, blocker release, retry, native sleep and verified
resume one owned transaction. It reconciles stale `FAILURE_PENDING`, prevents
stale callbacks from changing new transactions, coalesces repeated lid requests,
and requires actual kernel/counter/residency evidence before claiming sleep.
Ordinary recoverable blockers no longer reach poweroff through timer expiry or
retry consumption alone; measured physical-safety conditions retain supervision.

Known checkpoint APIs are preferred. Exact ordinary userspace blockers can be
safely terminated without an application allowlist, using identity/privilege
checks and independent release verification. Critical system components and VMs
are never blindly killed; supported guest ACPI shutdown precedes safe USB Clone
release. Internal DATA and external UGREEN safety auditing remain intact.

A real previous-release rollback test also exposed stale Python bytecode after
same-size, same-second file replacement. The portable installer and maintenance
writer now invalidate only the affected managed module's derived caches, so
upgrade, repair and rollback execute the restored version. Symlinked caches are
refused. The qualified sleep runtime and unit bytes are unchanged.

## Validation

- Accepted runtime: 201 automated tests passed before the physical cycle; the
  release candidate passes all 209 tests, including eight additional packaging
  and maintenance regression tests.
- One physical lid-close/open transaction and 112.615150 seconds of confirmed
  hardware/PMC low-power sleep, followed by successful resume.
- DATA/UGREEN restored to the accepted state; all 21 FM350/T700 checks passed.
- No retry loop, stale `FAILURE_PENDING`, stale fence or fail-safe poweroff.
- All 15 source/installed manifest entries matched; normal desktop recovery.
- Clean portable install, upgrade and exact rollback validated in isolated roots;
  reference payload mapping, modes and rollback validated separately.
- Qualified runtime and unit bytes remain pinned to the accepted implementation.

Raw journals, personal identifiers, host policies and forensic evidence are not
included. Prior interrupted cycles are excluded from the physical result.

## Install and upgrade

Verify `SHA256SUMS` before executing the `.run` asset. For the existing qualified
V2/T700/LockLock stack, run `./aag-external-storage-safe-suspend-linux-v1.3.0.run
transaction --config <private-host-config> --report <private-report-directory>`
as root after reviewing the prerequisite and `--check` results. This is an
explicit reference adapter, not an automatic conversion of portable installs.

For portable v1.0.0 through v1.2.1, run the verified new asset without arguments.
Fresh portable installations retain `install --device ... --protect-mount ...`.
An accepted production reference deployment need not be reinstalled to publish
this release. See [installation](https://github.com/aagprojectsteam-max/aag-external-storage-safe-suspend-linux/blob/v1.3.0/docs/INSTALLATION.md) and [upgrading](https://github.com/aagprojectsteam-max/aag-external-storage-safe-suspend-linux/blob/v1.3.0/docs/UPGRADING.md).

## Rollback and limitations

Portable rollback uses `sudo aag-safe-suspend rollback` with its exact compatible
snapshot. Reference rollback uses the deployment backup manifest, newest first
when a chain exists. Preserve local evidence and backups; see [rollback](https://github.com/aagprojectsteam-max/aag-external-storage-safe-suspend-linux/blob/v1.3.0/docs/ROLLBACK.md).

Physical acceptance covers the tested reference stack and one cycle under the
existing load. The optional checkpoint API was unavailable; its resource-audit
fallback succeeded. Safely stopped guests/profiles with manual-restart policy
stay stopped. Cellular reconnection passed; mobile Internet traffic was not
separately tested. Long-duration, thermal-emergency and Hibernate behavior were
not requalified. Unknown critical components still need a supported safe stop.
There is no universal guarantee for all Linux suspend failures.
