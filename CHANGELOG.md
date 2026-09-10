# Changelog

All notable changes are documented here. This project follows Semantic
Versioning.

## 1.2.1 — 2026-09-10

- Add an ordinary-suspend-only, read-only gate for active ConfigFS gadgets and
  dummy-HCD virtual USB devices before the storage fence and integration hooks.
- Allow loaded dummy-HCD controllers when no virtual USB device is bound; never
  hot-unplug a gadget or storage backing file from an active guest.
- Keep pre-fence refusals at `IDLE` so a safe retry is not poisoned by a stale
  recovery state, while preserving fail-closed handling after the fence arms.
- Add canonical semantic systemd graph validation that treats dependency-set
  order and runtime command fields as noise but rejects actual edge, command,
  drop-in, or drop-in-order changes.
- Add direct v1.2.0 upgrade and exact rollback coverage, failure cleanup,
  idempotence, Hibernate-isolation, UGREEN-invariant, and T700-latch tests.

## 1.2.0 — 2026-09-09

- Add physically accepted, reference-platform-only plain Hibernate support.
- Add fail-closed resume mapping, swapfile, initramfs, capacity, memory and
  current-kernel readiness gates.
- Add Hibernate-only storage ordering, cold-boot detection, abort reconciliation
  and terminal audit without changing the ordinary-suspend graph.
- Add delayed stable-generation T700 recovery with bounded ModemManager and
  NetworkManager verification while leaving GNSS on demand.
- Add transactional config/wiring migrations and direct v1.1.1 upgrade and
  exact rollback coverage.

## 1.1.1 — 2026-09-08

- Extend health checks to execute the live, non-suspending target identity and
  protected-mount validation and cross-check device/release state.
- Canonicalize release archive modes across checkout umasks and generalize the
  tag publication workflow for future versions.

## 1.1.0 — 2026-09-08

- Add canonical installed state and exact public-v1.0.0 bootstrap.
- Add transactional in-place upgrades, repair, bounded rollback, maintenance
  locking, health/status classification, and opt-in update checks.
- Add release compatibility metadata, threat model, upgrade matrix, and gated
  release publication.

## 1.0.0 — 2026-09-08

- First public release.
- Adds a systemd suspend coordinator for one explicitly selected external
  backup device while preserving configured internal mounts.
- Uses stable device identity and generation validation instead of `/dev/sdX`.
- Adds transaction-scoped, target-only udisks automount suppression and a
  terminal post-resume re-audit.
- Adds mount-namespace, process-reference, raw-device, device-mapper, NBD,
  loop, container, and guest ownership checks.
- Adds a Timeshift start fence and progress-aware managed-backup waiting.
- Adds semantic systemd job guarding for installation and rollback.
- Adds an isolated installer/rollback test harness and sanitized synthetic
  regression corpus.
