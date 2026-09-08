# Publication verification

Release v1.0.0 was published from commit
`80450ff825c5e493df552bf1c8a260d46e6b4021` and independently retrieved through
the public GitHub API on 2026-09-08.

## Release assets

- `aag-external-storage-safe-suspend-linux-v1.0.0.run`:
  `9c884f2c55fc6cbe08a4782f1d8bf54edbaf07a4fa8a80fd7b779c753f726da3`
- `aag-external-storage-safe-suspend-linux-v1.0.0.tar.gz`:
  `3fceb7de8fe1c8c047d1a11779364b3440f5768cb59badd9e2efeaf049d5c56c`
- `SHA256SUMS`:
  `a7d30ecd1fa0db2ac6b335cb825223a35662c6f6e5f78f5c797baf1097e81297`
- `RELEASE-MANIFEST.json`:
  `fdfa3208d9336832ee9ac54298619277261beaa3af53426dfe6059103dcdd201`

## Verified gates

- 55 sanitized offline tests passed.
- The generated udev rule passed `udevadm verify`.
- The isolated merged unit graph passed `systemd-analyze verify`.
- Install, idempotent reinstall, injected abort recovery, uninstall, and
  interrupted-uninstall restoration passed in isolated roots.
- The self-extracting payload rejected a deliberate checksum corruption.
- Consecutive release builds were byte-identical.
- Ruff, ShellCheck, and Markdown lint passed.
- Custom pattern/entropy/privacy scanning, Gitleaks 8.30.1, TruffleHog 3.97.4,
  and a private-evidence-derived denylist reported no findings.
- Reachable Git history contained one release commit and no blob larger than
  32,728 bytes at the release gate.
- Anonymous HTTPS clone, rendered README retrieval, all four asset downloads,
  `SHA256SUMS`, the internal payload manifest, and downloaded-installer
  `verify-source` passed.
- GitHub CI passed for both the `main` branch and the `v1.0.0` tag.

No raw machine evidence was published, and no suspend, hibernate, reboot,
shutdown, poweroff, internal-storage, modem, or GNSS action was performed during
publication validation.
