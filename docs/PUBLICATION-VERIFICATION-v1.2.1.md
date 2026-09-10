# Publication verification: v1.2.1

Release v1.2.1 was published from commit
`0c46b3a34f48e110d5c15ddffd7a9b15dfa36c41` on 2026-09-10. The annotated tag
and release are immutable references to the patch release that corrects the
bound USBClone ordinary-suspend regression while preserving v1.2.0 plain
Hibernate behavior.

## Public release assets

- `aag-external-storage-safe-suspend-linux-v1.2.1.run`:
  `f6d6f54f4c5c38a1c9c7fd76275c78699c3ec5376d111239bfbee41a0af440e7`
- `aag-external-storage-safe-suspend-linux-v1.2.1.tar.gz`:
  `a2acb4e466f48b50c0b9dbc41b8b4c41ce0efd2394f04dcdb1506bde0b05b356`
- `SHA256SUMS`:
  `ccadcefa20ccd8768537fc4bd1d49a3d3f1f5454e1a94dee2ad257a0f4045781`
- `release-manifest.json`:
  `1f1336db33ebd5e76ff19ee6b3f55d8ba4e3e9eda4b13507207a5db9ed56ab4c`

## Verified release gates

- All 115 sanitized tests passed, including bound and empty dummy-HCD cases,
  active-guest refusal without hot-unplug, T700-latch preservation, retry and
  fence behavior, semantic systemd graph checks, Hibernate isolation, UGREEN
  invariants, failure cleanup, rollback, and idempotence.
- The isolated merged systemd fixture and read-only live graph serializer check
  passed. Dependency ordering and runtime command fields normalized without
  accepting real edges, commands, targets, or drop-in changes.
- The self-extracting asset passed its internal payload manifest and rejected
  deliberate corruption. Consecutive builds were byte-identical.
- The actual downloaded v1.2.0 installer upgraded to v1.2.1 in an isolated
  filesystem root. Rollback restored all 28 prior managed files and consumed
  the one-shot rollback marker as designed.
- The release manifest, asset hashes, version, annotated tag, supported upgrade
  range, ordinary USBClone behavior, and scoped Hibernate status were mutually
  consistent.
- Ruff 0.16.6, the project privacy scan, private-path scan, reachable-history
  scan, large-blob scan, Gitleaks 8.30.1, and TruffleHog 3.97.4 passed with no
  findings.
- GitHub CI passed for both `main` and the `v1.2.1` tag; the independent
  tag-gated release workflow also passed. Historical failed runs remain intact.
- An unauthenticated HTTPS clone resolved the tag to the release commit. The
  public release API, all four release-asset downloads, `SHA256SUMS`, installer
  source verification, complete downloaded-asset acceptance suite, and the
  downloaded v1.2.0-to-v1.2.1 upgrade/rollback path passed.

Only normalized acceptance statements and synthetic fixtures are public. No
raw journal, private identifier, account data, machine-specific path, or local
configuration was published. Publication performed no suspend, Hibernate,
reboot, poweroff, lid cycle, USB detach, guest stop, modem restart, driver
reload, PCI reset, AT command, or production installation change.
