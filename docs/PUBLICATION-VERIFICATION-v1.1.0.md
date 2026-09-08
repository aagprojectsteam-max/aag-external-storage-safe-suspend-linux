# v1.1.0 publication verification

Release v1.1.0 was published from commit
`0826c4e24f7ff41a6a4a294a34202a573552cefa` by the tag-gated Release workflow
after lint, 81 isolated tests, privacy scanning, compatibility validation, and
self-extracting installer acceptance passed.

An unauthenticated HTTPS download from the public release produced:

- `.run`: `eb8371a63aa9de290525222b115596e4199dc97d81b71a509684a7b9c0f20d67`
- `.tar.gz`: `82cd51260f8e80eafeaa81fcde9dd7d3d30568bf3f6538768f9a25cb93af17d0`
- `SHA256SUMS`: `8d02008c523704cb7e6d8f61c3b03c831a0bc8e2d0862b39aa3c7ab1d879a319`
- `release-manifest.json`: `b1dbb8d1b5f6d03a2379c6afe2da0987428091f7f4acc159e26de9fe7185e113`

`sha256sum -c` passed for both payload assets. The anonymously downloaded
installer then passed source verification, isolated install, installed version
and health commands, same-version detection, uninstall, Timeshift restoration,
and corrupt-payload refusal. A second anonymous clean clone of the tag with a
standard `022` umask reproduced every published file byte for byte.

The follow-up main-branch builder canonicalizes archive modes to remove checkout
umask as a future reproducibility variable; this changes no v1.1.0 runtime file.
