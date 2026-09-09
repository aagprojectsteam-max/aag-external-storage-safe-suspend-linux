# External Storage Safe Suspend v1.2.0

Version 1.2.0 is the feature release for physically accepted plain Hibernate on
the documented reference platform.

## Added

- Fail-closed resume-device, resume-offset, swapfile-identity, initramfs,
  capacity, memory-safety and kernel-support gates.
- Hibernate-only systemd preparation, abort, delayed T700 recovery, terminal
  storage verification and one-cycle cold-boot detection.
- The accepted corrected T700 policy: a 110-second native recovery window, ten
  consecutive seconds of stable PCI/driver/MBIM/WWAN identity, one bounded
  recovery action, and explicit ModemManager and NetworkManager verification.
- Non-destructive Hibernate readiness fields in `health-check` and scoped
  support information in `status`.
- Transactional config-schema 1 to 2 and wiring-revision 1 to 2 migrations,
  including direct v1.1.1 to v1.2.0 upgrade and exact rollback coverage.
- Exact-hash adoption and rollback of the final accepted reference
  qualification helpers, without reading or packaging private evidence.

## Scope

Plain Hibernate is supported only on the verified reference platform and is
opt-in with `--enable-reference-hibernate`. Suspend-then-hibernate and hybrid
sleep are neither enabled nor accepted. Ordinary suspend remains on the
unchanged `suspend-contract-v1` graph.

The release contains normalized documentation and synthetic fixtures only. It
contains no raw qualification evidence or private hardware, account, session,
or subscriber identifiers.
