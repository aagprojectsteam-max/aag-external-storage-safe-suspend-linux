# Engineering Handoff — External Storage Safe Suspend / Hibernate for Linux

## Purpose

This is the master narrative for a project whose evidence is spread across architecture, installation, acceptance, hibernate and publication-verification documents. It records why the system exists, the failure modes discovered on real hardware, the safety architecture, validation layers and the rules for future power-state changes.

## Original problem

A Linux laptop was using important working data on an external NVMe enclosure. Ordinary lid-close suspend could leave the external device vulnerable to disconnect/power-state problems, open-file consumers and unsafe shutdowns. The goal became a coordinated power-state pipeline: quiesce users of the external filesystem, cleanly unmount it, enter the requested sleep state, then restore storage and consumers after resume — with a fail-safe if the transition cannot be completed safely.

## Hardware/environment that drove the design

Development involved a Ugreen RTL9210 NVMe enclosure with a WD NVMe drive and Ubuntu/GNOME on an HP EliteBook. The enclosure had accumulated unsafe-shutdown history. A D3cold-disable experiment did not by itself prevent the problematic disconnect behavior, which is why the final design does not rely on one PCI/USB power knob.

## Architecture

The project coordinates filesystem consumers, mount state and system sleep rather than treating suspend as a single `systemctl suspend` call. The core rule is: **do not enter the risky power transition while the protected external filesystem is still actively owned/mounted in an unsafe state.** Resume performs the reverse sequence and restores consumers only after storage is ready.

The system records evidence/receipts so a failed transition can be diagnosed. A fail-safe exists for cases where the requested sleep transition cannot be confirmed. Ownership and idempotency are important because repeated resume/recovery must not compound damage.

See `docs/ARCHITECTURE.md`, `docs/INSTALLATION.md`, `docs/INTEGRATIONS.md` and the source/systemd units for the exact current implementation.

## Major failures and lessons

### External enclosure disconnect / unsafe shutdowns

The project began after observing unsafe-shutdown counts and unreliable external-storage behavior around power transitions. Hardware power-state tweaks alone were insufficient; filesystem and consumer coordination was required.

### Inhibitors / fail-safe interaction

An early hot-bag incident showed that a lid-close policy or inhibitor can prevent the intended power transition while the machine remains active in a bag. The fail-safe path was revised so safety does not depend on assuming the requested suspend succeeded.

### Residual namespace/current-working-directory ownership

A later regression showed that even after obvious applications are stopped, a residual process namespace/CWD can keep the external filesystem busy. The project added handling/diagnostics for this class of hidden consumer rather than force-unmounting blindly.

### Recorder/evidence correctness

Acceptance is not just “the laptop woke up.” The recorder/evidence path itself was fixed and validated so the project can prove unmount, transition and restore ordering.

### Abrupt power-loss investigation

A later abrupt shutdown was investigated without falsely attributing it to the storage fail-safe. The evidence did not prove fail-safe causation; thermal/firmware/EC causes remained plausible. This is an important precedent: correlate timestamps and transition evidence before blaming this project for every power loss.

## Suspend acceptance

A validated production cycle demonstrated clean DATA unmount, suspend, clean resume and consumer restoration. Lid-close behavior was rehearsed and accepted after recorder and residual-ownership fixes. Exact acceptance details belong in `docs/ACCEPTANCE.md` and versioned publication-verification documents.

## Hibernate

The project later expanded to full hibernate support. Hibernate has its own requirements and evidence and must not be inferred from suspend success. `docs/HIBERNATE.md` is the authoritative setup/behavior reference. The public line subsequently reached **v1.4.0** with full hibernate/session-restoration acceptance recorded during the project lifecycle.

## Installation and lifecycle

Use `docs/INSTALLATION.md` and release artifacts rather than copying development-machine units manually. Integrations with desktop automount/udisks are documented in `docs/GNOME-UDISKS-AUTOMOUNT.md`. Any installer/uninstaller change must preserve unrelated mounts, services and user data.

## Testing and evidence hierarchy

Automated tests validate state-machine logic, parsing, ownership and packaging. Rehearsals validate orchestration without assuming a real power transition. Real suspend/hibernate acceptance validates the physical transition. Publication verification validates the public tag/assets. These are distinct layers.

For a power-state change, require: pre-transition protected filesystem state; consumer quiescence; clean unmount; request issued; actual transition confirmation; resume identity; remount/readiness; consumer restoration; evidence recorder integrity; and fail-safe behavior for simulated/real failure cases where safe to test.

## Publication

The repository contains README, CHANGELOG, SECURITY, CONTRIBUTING, tests, packaging/release tooling and multiple versioned `PUBLICATION-VERIFICATION-*` documents. Those versioned reports should remain immutable historical evidence. New releases should add/update the current verification record without rewriting old evidence.

## Repository map

- `README.md` — public overview.
- `docs/ARCHITECTURE.md` — safety/state architecture.
- `docs/INSTALLATION.md` — installation.
- `docs/ACCEPTANCE.md` — production acceptance.
- `docs/HIBERNATE.md` — hibernate-specific behavior.
- `docs/INTEGRATIONS.md` — consumer/integration notes.
- `docs/GNOME-UDISKS-AUTOMOUNT.md` — desktop automount interaction.
- `docs/PUBLICATION-VERIFICATION*.md` — public release evidence.
- `src/`, `systemd/`, `scripts/` — implementation.
- `tests/` — automated regression coverage.

## Maintenance rules

Never force-unmount the protected filesystem merely to make a test pass. Never report success from request acceptance alone; confirm the transition. Preserve a rollback path before changing systemd/logind/sleep hooks. Re-test both failure and success ordering after changing inhibitors, timeouts, mount discovery, consumer lists or hibernate configuration. Keep evidence on the internal disk so diagnostics survive an external-drive failure.

## Historical integrity rule

Do not erase failed experiments such as D3cold-only mitigation or the inhibitor/residual-CWD regressions. They explain why the current system is layered. Do not attribute an unrelated abrupt shutdown to this project without transition evidence.

## Current handoff status

As of the 2026-09-15 documentation audit, this repository already had one of the portfolio's strongest documentation sets. This handoff adds the missing chronological/maintenance narrative connecting architecture, acceptance, hibernate and publication evidence.