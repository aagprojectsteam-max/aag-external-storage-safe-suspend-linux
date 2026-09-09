# Upgrade test matrix

All upgrade tests use isolated filesystem roots and synthetic identities. They
perform no suspend, hibernate, reboot, poweroff, mount, unmount, or user-data
operation.

| Case | Gate |
|---|---|
| Fresh install | installer round trip and release acceptance |
| v1.0.0 to simulated v1.0.1 / v1.1.0 | version classifier plus exact public-v1.0.0 bootstrap fixture |
| v1.1.1 to v1.2.0 | sanitized managed-state fixture, config/wiring migrations, commit and exact rollback |
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
