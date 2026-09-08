# Changelog

All notable changes are documented here. This project follows Semantic
Versioning.

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
