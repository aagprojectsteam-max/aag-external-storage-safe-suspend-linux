# External Storage Safe Suspend v1.1.0

This feature release adds production-grade in-place upgrades without changing
the accepted suspend architecture.

- Bootstraps an exact public v1.0.0 installation into canonical installed state.
- Adds transactional upgrade, automatic rollback, compatible deliberate
  rollback, same-version repair, durable locking, and bounded snapshots.
- Adds `--version`, `status`, `health-check`, `update-check`, `repair`, and
  `rollback` maintenance commands.
- Preserves valid supported configuration and rejects unknown managed-file
  modifications.
- Publishes machine-readable compatibility metadata and gates releases on the
  synthetic upgrade, rollback, security, privacy, and artifact test suite.

No runtime suspend unit or coordinator behavior changed, and no physical sleep
test is required for this release.
