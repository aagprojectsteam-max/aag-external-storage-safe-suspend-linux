# USBClone and ordinary suspend

## Proven regression class

The accepted incident involved ConfigFS mass-storage gadgets bound to
dummy-HCD controllers during ordinary s2idle. Both virtual USB devices timed out
during resume configuration, and both the hardware and PMC sustained-residency
counters remained at zero. The T700 verifier correctly created a review latch.
A later lid-close request reached its T700 pre-hook, saw that existing latch,
and refused before native `systemd-sleep`; subsequent requests encountered the
held recovery fence rather than a kernel suspend entry.

UGREEN had already completed its owner audit and clean release in the first
failed transaction. Plain Hibernate was not active and its graph was unchanged.
Neither was causal.

The same number of loaded dummy-HCD controllers, with no bound gadget or virtual
USB device, achieved sustained hardware and PMC residency in accepted cycles.
The module or empty controllers alone are therefore not treated as the cause.

## v1.2.1 behavior

Ordinary suspend now uses a dedicated preparation service. Its first command is
a read-only ConfigFS/dummy-HCD inventory:

1. Empty ConfigFS gadgets and loaded controllers without a virtual device pass.
2. A bound project-owned mass-storage gadget refuses with
   `ACTIVE_USBCLONE_MASS_STORAGE_GADGET`.
3. Unmanaged gadgets, bound functions outside the proven class, and orphan
   dummy-HCD devices also refuse as unproven conditions; they are not mislabeled
   as the known root cause.
4. Only after the gate passes does the coordinator arm the storage fence and run
   configured ordinary integration hooks.

The gate has no USB teardown path. It does not write `UDC`, remove ConfigFS
objects, detach a device, inspect or modify a backing image, signal QEMU, or stop
a WinBoat guest. Guest shutdown and ownership release remain explicit external
operations.

## T700 and retry semantics

Placing the gate before the ordinary fence prevents the known bound-gadget
condition from reaching a configured T700 pre-hook. If the gate refuses, the
service teardown observes `IDLE` and does not create a recovery latch. A later
request can proceed after an owner-verified external shutdown removes the bound
device.

The release never deletes or acknowledges T700 `blocked.json`. An existing
review latch still refuses through T700's own policy. On the accepted reference
machine, one historical latch was retired only after its origin, exact result,
and correction preconditions were independently proven. That machine-specific
reconciliation is not generalized into the public installer.

## Graph and mode isolation

The installer snapshots the effective ordinary and Hibernate unit properties.
Dependency and ordering fields are compared as sets; runtime timestamps, PIDs,
and result fields in serialized commands are ignored. Command argv/path/error
semantics and drop-in membership/order remain significant. Upgrading v1.2.0 to
v1.2.1 must replace only the ordinary preparation dependency with the new
ordinary-only service and leave the Hibernate graph semantically equal.

All installer, upgrade, retry, cleanup, idempotence, and rollback coverage uses
isolated roots and synthetic ConfigFS/sysfs fixtures. It performs no suspend,
Hibernate, reboot, poweroff, USB detach, mount, unmount, or guest action.
