# Source classification policy

The public tree was reconstructed from the final accepted architecture rather
than copied from engineering evidence. Candidate inputs were classified before
use:

- **CURRENT_PRODUCTION:** installed reference components used to confirm
  behavior, never copied with private configuration.
- **CURRENT_SOURCE:** reviewed final algorithms used as design inputs and
  generalized into this tree.
- **TEST:** accepted regressions used to create synthetic fixtures.
- **DOCUMENTATION:** requirements and final design records summarized without
  private identifiers.
- **HISTORICAL / REJECTED:** earlier experiments and obsolete architectures,
  excluded from supported code.
- **PRIVATE_EVIDENCE / MACHINE_SPECIFIC / DO_NOT_PUBLISH:** raw journals,
  transaction trees, root scans, identifiers, private paths, and unrelated
  application state, excluded entirely.

The detailed per-artifact inventory remains local because listing private
filenames would itself disclose evidence. The repository contains no symlink or
copy back to the source/evidence directories.
