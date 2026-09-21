# v1.4.1 post-firmware power qualification

This document publishes normalized facts only. Raw journal, UUID, account,
session, serial, local path and modem/SIM evidence remains private.

## Reference firmware boundary

The tested HP EliteBook 840 G11 was updated from W70 01.09.02 to W70 01.10.00.
The bundle also changed Intel Management Engine from 18.0.18.2619 to
18.0.21.2801 and USB-C Power Delivery firmware from 2.7.0 to 2.9.0.

Before the update, multiple controlled kernel s2idle cycles completed entry and
exit but reported zero sustained hardware S0ix residency. A long lid-close cycle
also reported zero residency. GNSS-off and Bluetooth-off A/B checks did not
restore Package C10/S0ix, and operational modem/storage recovery remained healthy.

The vendor bundle updated several platform components together, so this evidence
does **not** identify BIOS, ME or PD as the sole causal component. It establishes
the tested firmware stack as the qualification boundary.

## Post-update Suspend

The first controlled Suspend after the firmware update passed:

- native kernel Suspend entry and exit: PASS
- hardware last_hw_sleep: 22,954,544 microseconds
- PMC SLP_S0 delta: 22,954,544 microseconds
- kernel/hardware residency agreement: PASS
- both previously running project-owned USB Clone profiles restored: PASS
- failed systemd units: 0

## Post-update Hibernate

Static S4 requalification passed before the physical cycle:

- 293 total automated tests in the current suite: PASS
- 80 S4-focused tests: PASS
- 37 pinned runtime/readiness files: PASS
- initramfs resume, active swap, resume device and resume offset: PASS
- DATA, external-storage audit, LockLock and T700 gates: PASS
- isolated deployment and exact rollback: PASS

The physical S4 cycle then passed:

- image creation and complete power-off: PASS
- manual power-on after the powered-off interval: PASS
- resume from the saved image with the same boot identity: PASS
- transaction terminal state: COMPLETE
- both saved USB Clone profiles restored to their saved UDCs: PASS
- saved cellular connectivity recovered automatically: PASS
- failed systemd units: 0

## Runtime fixes included in v1.4.1

v1.4.1 also publishes the code requalified in those cycles: qualification-only
PMC disagreement is separated from operational device health; project-owned USB
Clone stop operations create durable if-was-running receipts and restore only
the exact validated profile; S4 restores USB Clone after storage and before other
consumers; and root-owned restoration resolves the trusted repository from the
configured notification user's home rather than from /root.

Qualification remains limited to the tested reference configuration. Other
hardware, firmware and long-term endurance require their own acceptance.
