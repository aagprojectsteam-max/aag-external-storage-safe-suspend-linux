# External Storage Safe Suspend v1.1.1

This patch completes the v1.1 upgrade framework's health and reproducibility
gates. It remains directly upgradeable from public v1.0.0 and v1.1.0.

- `health-check` now invokes the live, non-suspending external identity,
  protected-mount, generated-rule, systemd graph, Timeshift fence, state, and
  rollback checks.
- Installed state cross-checks its configured device and release identity.
- Release archives canonicalize modes across checkout umasks.
- Tag publication selects the versioned assets and notes generically.

Suspend runtime behavior and unit wiring are unchanged. No physical suspend
test is required.
