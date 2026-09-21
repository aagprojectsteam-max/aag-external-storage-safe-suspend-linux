# External Storage Safe Suspend v1.4.1

v1.4.1 is a requalification and recovery-correctness patch release for the
reference transaction stack. It preserves the v1.4.0 S4 architecture while
publishing runtime fixes accepted on 2026-09-21.

## Changes

- Separate long-cycle PMC/hardware-residency qualification from operational
  resume health.
- Add durable USB Clone if-was-running receipts and exact post-resume restore.
- Restore saved USB Clone profiles in the S4 finish sequence after storage
  recovery and before other consumers.
- Resolve the trusted USB Clone repository from the configured user when a
  root-owned transaction performs restoration.
- Add regression coverage for image-write abort, cold-boot reconciliation,
  retryable clone restoration and root-home/user-home separation.

## Qualification

The reference HP EliteBook 840 G11 was requalified on HP W70 01.10.00,
Intel ME 18.0.21.2801 and USB-C/PD 2.9.0. The first controlled post-update
Suspend recorded 22.954544 seconds of matching hardware and PMC S0ix residency.

One subsequent physical Hibernate powered off and resumed the same image/boot,
reached terminal COMPLETE, restored both saved USB Clone profiles, recovered
the saved cellular connection and left zero failed systemd units.

The current automated suite contains 293 passing tests. The S4-focused set
contains 80 passing tests, and the live readiness/deployment qualification pins
37 files. See [post-firmware qualification](POST-BIOS-QUALIFICATION-v1.4.1.md).

## Scope

This release does not enable hybrid sleep or suspend-then-hibernate. It does not
claim universal firmware compatibility or identify one component of the HP
firmware bundle as the sole cause of the earlier zero-S0ix state. Historical
v1.4.0 evidence remains unchanged.
