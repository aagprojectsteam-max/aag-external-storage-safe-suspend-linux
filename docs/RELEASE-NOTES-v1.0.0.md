# External Storage Safe Suspend v1.0.0

This first public release turns an accepted Ubuntu/RTL9210 storage-suspend
architecture into a clean, configurable package with a self-verifying `.run`
installer, isolated rollback, normalized documentation, and sanitized synthetic
regressions.

The reference platform passed one final 90.077358-second s2idle cycle with one
suspend entry/exit, no post-resume target automounts, a complete terminal audit,
unchanged internal storage, a continued user session, and no poweroff request.

Before installing, read the support matrix and verify `SHA256SUMS`. The
installer does not run a suspend test. Hibernate remains unsupported.

No `.deb` is published in v1.0.0. The self-verifying `.run` asset keeps stable
device discovery, continuous job guarding, Timeshift diversion, validation, and
rollback in one tested transactional code path instead of maintaining two
installers with different failure semantics.
