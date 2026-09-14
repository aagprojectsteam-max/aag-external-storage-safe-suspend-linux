# External Storage Safe Suspend v1.3.1

A checkpointable transient user service can save its durable state and exit,
then be collected by systemd before the suspend adapter verifies the stop.
Previously `is-active` exit code 4 was reported as an inaccessible manager,
leaving the checkpoint receipt at STOP_REQUESTED and invoking the safe fallback.

The adapter now queries the same user's service manager to distinguish confirmed
absence from an observation failure. It accepts only the complete absent,
inactive, dead, zero-PID result from a successful query. The application must
still confirm durable state preservation. Missing manager access, an incomplete
response or an active replacement unit cannot produce a successful checkpoint.

The v1.3.0 suspend architecture, generic blocker fallback, critical-process
protections, per-transaction retry budget, fail-safe criteria, DATA/UGREEN
policy and T700/LockLock interfaces are preserved. Services with an explicit
never-auto-restart policy remain stopped after resume. No new application API
is required to suspend.

Validation includes 215 automated tests and an owned real transient user-service
reproduction: the original adapter left a durable stop unverified; the corrected
adapter verifies it and records LEFT_STOPPED_BY_POLICY. The previously published
v1.3.0 physical acceptance remains preserved with its original scope and excluded
user-interrupted cycle. No universal hardware or Hibernate qualification is
implied by the checkpoint fix.

Both portable and reviewed reference profiles remain in the installer and source
archive. Reference installations still require inspected V2, T700 and LockLock
dependencies and their private SHA256-pinned configuration. The installer does
not convert a portable deployment into that reference stack. Preserve the prior
release and the exact deployment snapshot for rollback.
