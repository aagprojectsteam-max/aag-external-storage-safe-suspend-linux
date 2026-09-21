# Publication verification — v1.4.1

Release v1.4.1 was published on 2026-09-21 from the merged `main` tree.

- Pull request: #1
- Merge commit: `34504e51ed18d6b4281857d9a4e9a2cf41534ebe`
- Annotated tag object: `d888181a0c93c63e3cae6ae035df5a7be0e29de2`
- Tag dereference: `34504e51ed18d6b4281857d9a4e9a2cf41534ebe`
- Release: `v1.4.1`
- Release state: published, not draft, not prerelease

## Public release assets

The four uploaded assets were downloaded again from the GitHub Release and
compared with the deterministic locally accepted build.

- `aag-external-storage-safe-suspend-linux-v1.4.1.run`
  - SHA256: `4f155db07da73a86f4fb3948109ad0885f64510d53932f6421fe7f29af885a53`
- `aag-external-storage-safe-suspend-linux-v1.4.1.tar.gz`
  - SHA256: `517401ec69d5fd17dbf12824e5dce07d7ca32db226614d23d595a163018ec1c0`
- `SHA256SUMS`
  - SHA256: `c99a9ea4a772c4abe547734c52774232f719a712b4ea2886c50cc80cd615641d`
- `release-manifest.json`
  - SHA256: `1d4b9c6228da9393b259c9ff6031310c01c992e10e9061e5f14665fdd8b85d21`

All four public-download hashes matched the accepted local artifacts exactly.

## Publication gates

- PR CI run 31: PASS
- `main` push CI: PASS
- `v1.4.1` tag CI: PASS
- Release workflow: PASS
- Ruff 0.16.6 lint and format gate: PASS
- Automated test suite: 293 PASS
- Privacy scan: PASS
- Git history / large-blob scan: PASS
- Qualified-runtime hash validation: PASS
- Deterministic consecutive release build: PASS
- Release manifest validation: PASS
- Self-extracting installer and exact rollback: PASS
- Reference transaction payload/rollback: PASS
- Reference Hibernate payload/rollback: PASS
- Actual public v1.4.0 to v1.4.1 upgrade and exact rollback: PASS
- Publication validation power-state actions: NONE
The first Release-workflow publication attempt collided with a manually created
release object after all validation gates had passed. The release object alone
was removed, the tag was preserved, and the failed workflow job was rerun. The
rerun completed successfully and published the final verified assets above.

Raw host evidence, account data, private paths and machine identifiers were not
published as release assets.
