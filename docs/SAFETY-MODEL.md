# Safety model

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
