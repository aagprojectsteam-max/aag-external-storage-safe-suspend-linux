# Changelog

All notable changes are documented here. This project follows Semantic
Versioning.

## 1.4.0 — 2026-09-14

- Publish production-qualified reference Hibernate/S4 with one WWAN recovery
  owner (`aag-hibernate-transaction-finish.service`), durable
  image/consumer evidence and explicit failure preservation across a later reboot.
- Fix recovery lock handoff after late storage teardown and wait for modem
  enumeration before bounded saved-profile activation.
- Qualify one physical S4 cycle with usable cellular DNS/HTTPS, normal session
  recovery, COMPLETE state and no reboot. Preserve the preceding WWAN failure.
- Restore required consumers completely according to saved receipts and restart
  policy; retain safe UGREEN release and internal DATA identity/mount protection.
- Reconcile pre-image aborts and cold boots without another power action;
  distinguish reboot after failed resume and preserve the preceding evidence.
- Record final acceptance with 284 automated tests, passing CI, 35-of-35
  source/installed parity and verified rollback. Scope qualification to the tested
  configuration; automatic modem recovery took approximately two minutes.
- Add an explicit packaged `hibernate` deployment/rollback route and pin the
  physically tested S4 runtime. Keep the v1.3.1 Suspend reference bytes intact.
- Validate portable upgrade/rollback from actual v1.3.1 release bytes and both
  reference package routes in isolated roots; publish no private host evidence.

## 1.3.1 — 2026-09-14

- Verify collected transient user services through a reachable user manager
  before accepting their durable checkpoint and stopped state.
- Preserve the accepted Suspend architecture and saved never-restart policies.

## 1.3.0 — 2026-09-14

- Publish the qualified reference-host transaction owner with stale-failure
  reconciliation, transaction-scoped retries and repeated lid-request control.
- Add generic safe ordinary-userspace blocker handling, preferred checkpoint
  APIs, critical-component exclusions and supported guest shutdown integration.
- Preserve DATA/UGREEN release auditing, verify actual kernel sleep and hardware
  residency, and require validated restoration before COMPLETE.
- Remove timer/retry exhaustion as an ordinary fail-safe poweroff trigger while
  preserving real thermal/battery safety supervision.
- Package the accepted adapter behind an explicit `transaction` installer route;
  keep portable v1.x interfaces and schemas compatible without automatic conversion.
- Pin qualified runtime bytes, exclude private reports from release payloads,
  and add isolated payload/permission/upgrade/rollback/privacy tests.
- Fix stale Python bytecode after rapid portable upgrade/rollback by invalidating
  only the replaced managed module caches; test exact version recovery.
- Record one successful physical lid-close/open cycle with 112.615150 seconds
  of confirmed sleep and 21 passing FM350/T700 checks; publish no raw evidence.

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
