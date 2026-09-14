# Upgrade test matrix

## v1.4.0 accepted validation

The final automated suite passed **284 tests**, with passing main/tag CI and
release validation. Built-asset checks exercised the portable installer, reference
`transaction` route and explicit reference `hibernate` route in isolated roots.
Actual public v1.3.1 bytes were used for portable upgrade and exact rollback.
Both accepted reference runtime manifests remained pinned; live source/installed
parity was 35 of 35. Anonymous verification covered 12 assets across the current
release and preserved v1.3.0/v1.3.1 baselines.

S4 regressions cover callback lock handoff, late storage teardown, exactly one
recovery owner, modem enumeration before profile activation, durable consumer
restoration, abort/cold-boot reconciliation and post-reboot evidence preservation.
The [physical acceptance](ACCEPTANCE.md) is separate from isolated packaging
validation. Historical version-specific counts below retain their original scope.

All upgrade tests use isolated filesystem roots and synthetic identities. They
perform no suspend, hibernate, reboot, poweroff, mount, unmount, or user-data
operation.

| Case | Gate |
|---|---|
| Fresh install | installer round trip and release acceptance |
| v1.0.0 to simulated v1.0.1 / v1.1.0 | version classifier plus exact public-v1.0.0 bootstrap fixture |
| v1.1.1 to v1.2.1 | sanitized managed-state fixture, config/wiring migrations, commit and exact rollback |
| v1.2.0 to v1.2.1 | ordinary wiring revision 2 to 3, semantic graph validation and exact rollback |
| Same version / repair | no-op detection and transactional managed-file repair |
| Unsupported downgrade | explicit refusal test |
| Modified managed file / supported config / unknown legacy edit | ownership-class tests |
| Missing/corrupt state / partial/pending operation | seven-state status matrix |
| Stale/live lock | kernel-flock tests |
| Active sleep / protected backup | pre-mutation refusal tests |
| CUPS / real power / R1 / recovery / UGREEN / T700 job | semantic job-classifier corpus |
| Systemd and udev mismatch | graph tests and health mismatch tests |
| Migration failure / post-install failure | injected precommit failures |
| Automatic rollback / rollback failure | exact-byte restoration and durable failure marker |
| SIGTERM | installer converts termination to the rollback exception path |
| Disk full / read-only failure | atomic-write exception path and rollback injection |
| Package checksum / release metadata mismatch | corrupt self-extractor and manifest validator |
| Path traversal / symlink attack / rollback poisoning | normalized-path and unsafe-parent tests |

`make release-acceptance` runs the full unit suite, source checks, privacy scan,
deterministic build, compatibility-manifest validation, self-extractor
corruption test, isolated install, same-version detection, and uninstall
rollback check. CI and tag publication both require this target to pass.

Hibernate additions cover valid and invalid resume device/offset, changed
swapfile identity, missing initramfs resume support, insufficient capacity, the
non-double-counted memory model, clean/managed/unknown external ownership,
generation change, terminal automount, cold boot detection, delayed T700 stable
identity, late ModemManager/NetworkManager readiness, ordinary-suspend graph
isolation, and transactional rollback. Fixtures contain no live device identity
or raw qualification evidence.

The v1.2.1 ordinary regression corpus covers bound project-owned mass-storage
gadgets, active guest refusal without hot-unplug, unmanaged and orphan virtual
devices, empty loaded dummy-HCD controllers, pre-fence cleanup, safe retry after
external owner-verified shutdown, preservation of existing T700 latches,
semantic dependency-set normalization, command/drop-in change rejection,
Hibernate isolation, UGREEN/internal-storage invariants, idempotence, injected
failure cleanup, upgrade, and rollback. No test invokes a power or USB action.

## v1.3.0 publication validation

The candidate adds isolated reference payload installation, all mapped hashes
and permissions, exact upgrade/rollback, rollback corruption refusal, symlink
escape refusal, private-report exclusion and qualified-runtime hash enforcement.
The built `.run transaction` dispatch is exercised as well as the existing
portable installer. A separately downloaded public v1.2.1 asset provides the
real previous-version install for isolated upgrade and rollback verification.
Live production deployment and physical suspend are not repeated for packaging.

The full v1.3.0 suite contains 209 passing tests. The actual public v1.2.1
upgrade/rollback test verifies 31 prior files and permissions, removes four
new module files on rollback, checks exact configuration preservation and
validates version/health before and after restoration. A deterministic cache
collision regression and symlink-refusal test cover the maintenance correction.
