# Safety model

## Qualified reference stack: v1.4.1

The reference policy is **protect data → safely stop/release ordinary userspace
blockers → enter sleep**. Known checkpoint or stop APIs are preferred. A normal
userspace blocker does not require a hard-coded application allowlist: exact
resource ownership, process identity and privilege checks determine whether
bounded TERM, wait and a last-resort safe KILL are eligible. Independent storage
release verification remains mandatory.

Critical system components, databases, filesystem infrastructure and guests are
never blindly killed. A guest requires a supported configured shutdown path.
Ambiguous identity, missing observations or an unsafe release remains a refusal;
no force/lazy unmount or storage-fence bypass is authorized by this policy.
Restart follows saved consumer receipts and explicit policy, including `never`.

Stale failure reconciliation and transaction-scoped retries prevent repeated lid
requests from consuming unrelated retry state. Automatic poweroff is not normal
recovery for an application blocker. Timer expiry or an exhausted retry alone
does not justify shutdown; real thermal/battery safety supervision remains active.
S4 refusal and recovery do not invoke the ordinary retry/poweroff monitor.

See [architecture](ARCHITECTURE.md), [the adapter contract](TRANSACTION-ADAPTER.md)
and [Hibernate](HIBERNATE.md). These are the qualified reference policies.

## Separate portable profile policy

The decision classes, optional emergency configuration and time policy below
belong to the older portable coordinator. Its conservative no-generic-kill
policy and awake-window option are not the corrected reference adapter policy.

## Decision classes

### False blocker

A proven propagated mount mirror, stable zombie, or PID-only churn is classified
without signalling a process. It is not allowed to prevent sleep merely because
another namespace exposes the same host mount.

### Managed blocker

A configured backup job receives an explicit graceful quiesce request, if one
is configured. The coordinator waits while CPU, I/O, block, or descendant
progress changes, then re-audits. A soft wait is not a kill deadline. The
separate maximum awake window bounds physical exposure but defaults to 900
seconds, not a universal short timeout.

### Unknown or unsafe blocker

An owner, raw/stacked block relationship, independent mount namespace,
permission gap, changing identity, unstable audit, or failed clean unmount
fails closed. There is no force unmount, lazy unmount, generic process kill, or
unknown-owner termination.

For virtual USB, the known regression class is a bound ConfigFS mass-storage
gadget on dummy-HCD. Empty loaded controllers are not classified as causal. A
bound unmanaged gadget, a different bound function, or a dummy-HCD USB device
without an observable ConfigFS owner remains fail-closed as unproven. The gate
does not perform teardown, so an active WinBoat/QEMU guest cannot be hot-unplugged.

### Emergency fallback

Recovery samples firmware thermal trip points and NVMe thresholds while also
checking the lid twice. Opening the lid releases a safely blocked transaction.
The default `emergency_action` is `none`; it records and holds even at an
emergency boundary. Administrators may explicitly choose `poweroff`, which is
considered only after a critical trip, sustained warning, prolonged thermal
unobservability, or the configured maximum awake window, followed by another
lid check. Orderly poweroff is a last physical-safety fallback, never the normal
suspend path.

## Threat model

The implementation protects against misidentifying `/dev/sdX`, mount namespace
mirrors, PID reuse, short-lived process churn, post-resume automount races,
external bridge re-enumeration, installer races, and partial installer failure.
It preserves explicitly configured internal filesystems and unrelated USB
automount behavior.

Installer graph snapshots are compared semantically: unordered dependency and
ordering fields are sets, while commands and drop-in membership/order remain
significant. This avoids representation-only refusals without accepting a real
graph change.

It does not make failing media reliable; provide UPS protection; repair kernel,
ACPI, USB, or controller firmware; prove application-level backup consistency;
or recover from every machine hang. A current, independently verified backup is
still required.

## Time policy

Timeouts are ordered and configuration-validated:

- `soft_wait_seconds`: initial grace before lack of progress matters.
- `progress_stale_seconds`: how long unchanged progress may persist.
- `maximum_wait_seconds`: absolute awake safety ceiling.
- resume enumeration and audit windows: bounded waits for hardware and a stable
  terminal state.

Changing these values requires considering workload duration, battery/AC
conditions, chassis cooling, and the consequence of remaining awake with a
closed lid. A timeout never authorizes a forceful filesystem or process action.
