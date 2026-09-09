# Publication verification: v1.2.0

Release v1.2.0 was published from commit
`fc252aaf331f95674bf113c245f47c6d3b9ba5ee` on 2026-09-09. The tag and
release are immutable references to the feature release that adds scoped plain
Hibernate support for the physically accepted reference platform.

## Public release assets

- `aag-external-storage-safe-suspend-linux-v1.2.0.run`:
  `a82f3e2a25c02e4c73b3a5d0b64c5f629d741baea7cd073b683d1d20cb6ffe68`
- `aag-external-storage-safe-suspend-linux-v1.2.0.tar.gz`:
  `b80c8d28ce5ecd07f2c39a6a8ceb659a594ccc3c722a9729331836636fe4a43f`
- `SHA256SUMS`:
  `477f7397939954cff5f2d4aebfb1f0adf40f56b236119a607630397613faa5b9`
- `release-manifest.json`:
  `0732d9f2cf6339c8a7d5ce9342c5187996e7bbf905b2cd29052887970af75491`

## Verified release gates

- All 99 sanitized tests passed, including Hibernate readiness and failure
  cases, T700 stable-generation recovery, ordinary-suspend graph invariance,
  upgrade, and rollback.
- The isolated merged systemd fixture passed.
- The self-extracting asset passed its internal payload manifest and rejected
  deliberate corruption.
- A synthetic managed v1.1.1 installation upgraded transactionally to v1.2.0
  and rolled back exactly using the downloaded public installer.
- Consecutive local release builds were byte-identical.
- The release manifest, asset hashes, version, tag, supported upgrade range,
  and scoped Hibernate status were mutually consistent.
- Ruff, the project privacy scan, reachable-history scan, large-blob scan,
  Gitleaks 8.30.1, and TruffleHog 3.97.4 passed with no findings.
- GitHub CI passed for both `main` and the `v1.2.0` tag; the independent
  tag-gated release workflow also passed.
- An anonymous HTTPS clone resolved the tag to the release commit. Anonymous
  README and Hibernate-document retrieval, all four release-asset downloads,
  `SHA256SUMS`, installer source verification, and the isolated downloaded
  upgrade/rollback test passed.

Only normalized acceptance statements and synthetic fixtures are public. No
raw qualification evidence, journal, private identifier, account data, or
machine-specific configuration was published. Publication performed no
suspend, Hibernate, reboot, poweroff, modem restart, driver reload, PCI reset,
AT command, or production installation change.
