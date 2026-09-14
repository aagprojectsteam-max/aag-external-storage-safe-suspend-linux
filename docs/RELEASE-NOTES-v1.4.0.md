# External Storage Safe Suspend v1.4.0

v1.4.0 publishes the qualified reference Hibernate transaction and its automatic
WWAN recovery. The tested S4 cycle resumed the image, restored usable cellular
Internet and recovered the desktop, keyboard, mouse and touchpad without reboot.
The accepted Suspend v1.3.1 runtime and its physical regression status are preserved.

## Hibernate recovery

The prior S4 image resumed successfully, but recovery collided with a late
storage-service teardown lock and aborted before restoring the modem. The finish
owner now waits for native, storage and boot callbacks to release ownership before
acquiring the operation lock. Native return and boot reconciliation delegate to
that one owner. The retired Hibernate services and timer hook cannot compete.

The owner waits for stable device/control-port enumeration, consumes one durable
recovery attempt and performs bounded ModemManager/saved-profile recovery. Modem
enumeration is checked before connection activation. A reboot after failed image
resume preserves the original failure evidence instead of relabeling it as an
ordinary cold boot. Failure never becomes success merely through a timer.

Internal DATA, external safe-release policy, eligible consumer receipts, GNSS
on-demand policy and LockLock controls retain their accepted behavior. No PCI
rescan, driver reload/rebind, firmware reset, automatic GNSS start or modem power
policy change is added. Suspend-then-Hibernate and hybrid sleep remain unaccepted.

## Physical qualification

- Exactly one controlled S4 cycle of commit
  `5cc43022551ad521f13af1eea72099b854e45230`; actual kernel/image resume confirmed.
- Exactly one WWAN recovery owner invocation; no lock collision and no reboot.
- Automatic cellular reconnection, IP/routing, uncached DNS and HTTPS HEAD through
  the WWAN interface passed; device enumeration alone was not sufficient.
- DATA/UGREEN and consumers matched the saved policy; LockLock and the desktop
  session recovered. The user explicitly confirmed all input devices worked.
- Transaction COMPLETE, no stale fence/held WWAN lock, retry loop, stale failure
  state or fail-safe poweroff; online filesystem checks passed.
- All 35 source/installed entries matched. The implementation had 283 passing
  automated tests before this physical qualification. Release validation adds
  packaging and compatibility checks without another S4 cycle.

Transient modem exceptions, PM timeout and non-fatal PCIe warnings still occurred.
Automatic recovery restored mobile service in approximately two minutes. The
release qualifies that recovery, not an error-free firmware resume. The preceding
WWAN failure/manual reboot remains classified separately and is not erased.

Qualification covers the tested Ubuntu/GNOME reference stack with FM350/T700,
AC power, sufficient available memory and the saved connected mobile profile.
The Windows guest was cleanly shut down to satisfy the memory gate. Other
hardware, firmware, long-term endurance, GNSS-enabled S4 and required automatic
external-partition remounts are outside this physical result. User-stopped or
explicit never-restart consumers stay stopped. Only normalized findings are
public; private policies, raw evidence and device/account identifiers stay local.

## Installation, upgrade and rollback

Verify `SHA256SUMS` before running the `.run` installer. Portable v1.0.0 through
v1.3.1 installations retain the existing upgrade interface and portable graph.
They do not automatically acquire the reference stack or this S4 qualification.

For an existing qualified reference stack, the explicit `hibernate` installer
route wraps the unchanged `scripts/deploy_hibernate.py`. It requires reviewed
private host configuration, passing gates, protected baseline hashes and an idle
transaction. It saves exact rollback files and modes and validates/reloads systemd
definitions; it performs no sleep or reboot. The accepted production installation
does not need a reinstall merely to publish this release.

The `transaction` route preserves the accepted v1.3.1 reference initializer and
runtime bytes; the portable package version is v1.4.0. Both runtime manifests are
checked during release validation. Built-asset checks cover both reference routes,
clean portable installation, supported upgrades, exact rollback and corrupt-payload
refusal. The accepted public v1.3.1 release and private snapshots remain rollback
baselines; existing public tags and assets are not replaced.

See [installation](INSTALLATION.md), [Hibernate transaction](hibernate-transaction.md),
[upgrade](UPGRADING.md) and [rollback](ROLLBACK.md) for the separate profiles.
