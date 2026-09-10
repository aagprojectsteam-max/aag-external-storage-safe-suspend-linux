# External Storage Safe Suspend v1.2.1

Version 1.2.1 is a backward-compatible production regression correction for
ordinary suspend.

## Fixed

- Refuse ordinary suspend before the storage fence and integration hooks when a
  ConfigFS gadget is bound to a dummy-HCD virtual USB device.
- Distinguish the proven bound-device condition from loaded, empty dummy-HCD
  controllers, which remain allowed.
- Never hot-unplug a gadget, backing image, or active WinBoat/QEMU guest.
- Leave a pre-fence refusal at `IDLE`, allowing a later safe request after the
  external owner releases the gadget; failures after the fence remain
  fail-closed.
- Canonicalize semantically unordered systemd dependency fields and runtime
  command serialization while continuing to reject real dependency, ordering,
  command, target, or drop-in changes.

## Compatibility and isolation

- Direct upgrades from v1.0.0 through v1.2.0 remain supported. The v1.2.0 to
  v1.2.1 path applies only wiring revision 2 to 3 and has an exact rollback.
- Existing T700 review latches remain owned by T700 and are never blindly
  deleted or acknowledged. The gate prevents the accepted USBClone condition
  from reaching an ordinary T700 hook and creating a new latch.
- Plain Hibernate and its T700 WWAN recovery graph are semantically unchanged.
- UGREEN owner/release safety, internal protected mounts, GNSS policy, user
  sessions, Timeshift fencing, and the hot-bag fail-safe remain intact.

The final reference cycle recorded one 72.361356-second kernel s2idle interval
and 68.945860 seconds of matching hardware and PMC low-power residency. The
USBClone gate, UGREEN terminal state, internal storage, T700 verifier, and user
session all passed; no new latch, poweroff request, or cycle-attributable
kernel/storage error occurred. Only normalized results and synthetic tests are
included in this release.
