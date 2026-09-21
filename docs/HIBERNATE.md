# Production-qualified plain Hibernate / S4

v1.4.1 qualifies the reference Hibernate transaction on the
[tested platform](TESTED-HARDWARE.md), with image resume, session recovery and
usable cellular connectivity without a manual reboot. v1.4.1 requalifies the
updated shared Suspend runtime together with the S4 extension. Plain Hibernate is
explicitly enabled and separately qualified; it is not a lid-close or Suspend-failure fallback. Hybrid sleep and
suspend-then-hibernate remain disabled and unaccepted.

## Prerequisites and readiness

The reference extension needs the reviewed storage guard, transaction adapter,
FM350/T700 integration and LockLock contracts described in
[installation](INSTALLATION.md#qualified-reference-hibernate-extension).
It also requires:

- Current kernel support for hibernation and the selected S4 mode.
- Sufficient available memory and free swap for the configured image policy,
  including its safety margins; nominal swap capacity alone is not a GO gate.
- An active, protected internal swapfile with verified identity and extents.
- A correct resume device and, for this swapfile, the correct resume offset,
  consistent with the running kernel configuration and current swap mapping.
- Matching initramfs resume support and configuration.
- Healthy DATA, safe UGREEN preparation, a clean transaction ledger, compatible
  pinned integrations and exactly one recovery owner with no held stale lock.
- Safe thermal state and passing workload, storage and memory gates immediately
  before image creation.

The production reference used **64 GiB RAM and a 72 GiB internal ext4 swapfile**.
These sizes describe the test; 72 GiB is not a universal requirement. Capacity
must be evaluated against the actual kernel, image policy and current workload.
The accepted cycle required a clean shutdown of the Windows guest for the memory
gate. The installer does not resize swap, configure a resume target or rebuild
initramfs for an arbitrary host.

On an already configured reference stack, the read-only readiness command is:

```bash
sudo /usr/local/libexec/aag-power-transaction hibernate-readiness
```

A GO result is required, but does not itself prove a physical image round trip.
The separate `hibernate-once` action requests one real native Hibernate and
requires a controlled physical qualification with evidence armed beforehand.
No additional cycle is needed just to publish or read an accepted release.

## Transaction and completion

The reference transaction follows this order:

1. Record transaction identity, original boot identity and saved consumer/WWAN
   policy; arm durable evidence and the storage fence.
2. Resolve eligible blockers, prepare DATA/UGREEN and recheck the live GO gates.
3. Request native S4 image creation and power-off.
4. Confirm image resume from kernel/native/session evidence; distinguish it
   from a cold boot. Command success alone is insufficient.
5. Reconcile device and storage state, then run automatic WWAN restoration
   through `aag-hibernate-transaction-finish.service`.
6. Restore required consumers according to durable receipts and saved policy.
7. Verify terminal health, record `COMPLETE`, and release the fence to `IDLE`.

Entry and power-off observations are recorded retrospectively when the evidence
establishes a real image round trip. v1.4.1 also carries durable receipts for
project-owned USB Clone profiles that were running before S4. Restoration resolves
the configured user's trusted repository, validates the exact profile/backing and
saved UDC, and starts it only after storage recovery; an identity change or failed
restart remains explicit and retryable.

Internal DATA remains mounted and
identity-checked. UGREEN returns to a verified safely released state; this
policy does not force-remount external partitions after re-enumeration. Required
consumers needing such an automatic mount are refused until a safe restoration
contract exists. User-stopped workloads and `never` restart policies stay stopped.

## Recovery, abort and boot reconciliation

The finish service is the sole S4 restoration owner. It waits for native,
storage and boot callbacks to release their operation lock, then uses one durable
WWAN recovery token. It waits for stable device generation and modem enumeration
before bounded saved-profile activation. See [FM350/T700](T700-WWAN-HIBERNATE.md).

Pre-image refusal reconciles preparation without a Hibernate retry. Durable boot
reconciliation distinguishes `COLD_BOOT_FALLBACK` from
`REBOOT_AFTER_RESUME_FAILURE`, preserves the preceding ledger, and uses current
device identities and saved policy. A real restoration failure keeps explicit
failure evidence and a transaction-local fence until safe reconciliation succeeds.
A reboot cannot turn a failed peripheral restoration into acceptance.

## Accepted result and limits

The final cycle passed image and user-confirmed session/input recovery, automatic
mobile DNS/HTTPS, DATA/UGREEN, consumer policy, LockLock, filesystem health and
terminal cleanup without reboot. The earlier S4 WWAN failure remains a separate
failed result. Transient modem/PCIe errors occurred during recovery; usable
service returned automatically in approximately two minutes. Device enumeration
or NetworkManager visibility alone is not full Hibernate acceptance.

See the [final acceptance matrix](ACCEPTANCE.md) and
[detailed transaction contract](hibernate-transaction.md). GNSS stays on demand;
PCI reset/rebind/rescan and driver reload are not part of the accepted recovery.
Other hardware, firmware and long-term endurance are not qualified by this result.

## Separate portable Hibernate integration

The portable coordinator retains its older opt-in `install
--enable-reference-hibernate` interface, `aag-safe-suspend health-check` gates and
`aag-safe-suspend hibernate` entry point. That is a different graph and recovery
contract, described under [portable architecture](ARCHITECTURE.md#portable-profile-architecture).
A portable upgrade does not install the v1.4.1 qualified reference transaction
extension. Do not mix the portable and reference commands or recovery owners.
