# v1.1.1 publication verification

Release v1.1.1 was published from commit
`3d769abc3328b663ef20e41007c29bfa02e23b2d` by the gated Release workflow.
Lint, 81 isolated tests, privacy scanning, compatibility validation, release
acceptance, and tag/version validation passed before publication.

An unauthenticated HTTPS download from the public release produced:

- `.run`: `fa2ff6dac48d3835cdd000e2855a877008e6c85f3f112bee8b9ee68dfbfeaf78`
- `.tar.gz`: `88ad03ed49ff978e01cca51fec81ef291eea2a9a0c1e89847742ec321e0cb900`
- `SHA256SUMS`: `06e826b1b373a1b588ae978cc386670a8feab26846d469625f556f0e44aef072`
- `release-manifest.json`: `81d9c44a358e0dd3c393a809e0e3454935988032781f5b22aa85b5b66ed318be`

`sha256sum -c` passed. All four downloads matched the final local release
candidate byte for byte. The downloaded installer passed embedded and per-file
checksum verification, isolated install, installed version/status, same-version
detection, uninstall, Timeshift restoration, and corrupt-payload refusal.

A separate anonymous clean clone of tag `v1.1.1` rebuilt all four files. They
matched the public downloads byte for byte under a different checkout umask,
confirming the canonical-mode reproducibility fix.
