# Architecture

## Boundary and invariants

The coordinator is inserted into the existing `systemd-suspend.service`
transaction. It does not replace logind, GNOME, or systemd's native suspend
implementation. Its hard invariants are:

1. Every configured internal mount retains its expected filesystem UUID.
2. Only the external disk matching the configured stable identity is actionable.
3. No target filesystem is released while an owner, independent mount
   namespace, or observability gap exists.
4. Every unmount is a normal `umount` of a revalidated host mount point.
5. The resume fence remains armed until the target is absent after a bounded
   enumeration window or present, fully identified, owner-free, and unmounted.
6. Installation and rollback abort around relevant concurrent systemd jobs and
   never initiate a power transition.

## Installed components

- `aag-external-storage-safe-ordinary-suspend.service` performs the read-only
  ConfigFS/dummy-HCD gate, then arms and prepares the ordinary storage
  transaction. Keeping this as the ordinary entry point places the gate before
  every configured pre-suspend integration without adding it to Hibernate.
- `aag-external-storage-safe-suspend.service` arms the fence, validates internal
  storage, resolves the external generation, waits for managed work, performs
  the pre-sleep audit, and releases target filesystems for the separate plain
  Hibernate path.
- A drop-in makes `systemd-suspend.service` require the pre-sleep service and
  queues the resume verifier after the native suspend service returns.
- `aag-external-storage-safe-suspend-resume.service` handles USB
  disconnect/re-enumeration and performs the terminal re-audit.
- `aag-external-storage-safe-suspend-failure.service` keeps the transaction
  fail-closed and evaluates the configured physical-safety policy.
- One generated udev rule sets `UDISKS_AUTO=0` for each exact target filesystem
  while the transaction marker exists.
- Optional Timeshift `dpkg-divert` wrappers serialize new backup starts against
  the fence and register active jobs by PID plus process start time.

## Stable identity and generation

Installation discovers the selected whole disk and records its underlying
serial, USB vendor/product pair, and exact filesystem UUID set. Runtime
resolution first matches those durable attributes and then binds the result to
sysfs, major:minor numbers, udev properties, and `diskseq`. Device names and
mount paths are used only after that binding. Re-resolution surrounds every
audit and unmount, so disconnect/re-enumeration or name reuse invalidates the
action rather than redirecting it.

## Owner audit

Three scans cover mountinfo in each visible mount namespace; cwd, root,
executable, open descriptors and raw block descriptors; memory maps; sysfs
holders/slaves; device-mapper, NBD and loop relationships; and known guest
processes. PID plus `/proc` start time prevents PID reuse confusion. Stable
zombies and harmless PID churn do not create owners. A host mount propagated
into a Snap or systemd sandbox is a mirror, while an independent namespace is a
real blocker. Permission or enumeration gaps fail closed.

## Suspend sequence

Before the marker is created, the ordinary-only gate enumerates bound ConfigFS
gadgets and USB devices instantiated below dummy-HCD controllers. Empty loaded
controllers pass. A bound or orphan virtual device refuses without writing UDC,
detaching USB, signalling a guest, or invoking an integration hook. A
pre-fence refusal leaves the transaction `IDLE`, so a later safe retry is not
poisoned by a recovery latch.

The marker is then created before pre-sleep work. Optional managed backup quiescing
uses an explicit argv command. CPU, process I/O, block counters, and descendants
drive the progress window; the overall awake safety ceiling is separately
bounded. After a stable audit, host target mounts are cleanly unmounted,
identity is checked again, and a final audit must show no ownership. Only then
does the native systemd suspend job proceed.

## Resume sequence

The resume verifier starts after native `systemd-suspend.service` exits. The
target-only udev rule has already been active for the full transaction. It
allows a bounded re-enumeration window, resolves the current generation using
stable identity, and repeats the complete audit. A harmless target automount is
cleanly removed and re-audited; a new writer or identity change holds the fence.
The marker is deleted only after terminal success. `/run` makes stale
suppression impossible across reboot.

## Plain-Hibernate sequence

The reference-platform plain-Hibernate path is an opt-in sibling transaction,
not a change to `systemd-suspend.service`. A dedicated
`systemd-hibernate.service` drop-in requires the same accepted external-storage
preparation service, then runs a final current resume-mapping and capacity gate
before native image creation. After native S4 return, the mode-specific T700
service waits for a stable regenerated device identity and verifies WWAN. The
Hibernate terminal service then calls the existing external-storage resume
audit and releases the fence.

An armed, root-owned durable episode distinguishes an image resume from a cold
boot. Its one-cycle boot detector is enabled only around an explicit
`aag-safe-suspend hibernate` transaction and disables itself after either a
successful resume or a detected cold boot. Abort reconciliation never retries
Hibernate or initiates another power action.

## Integration boundary

Optional pre-suspend and post-resume argv hooks are serialized inside the
transaction and must return success. They are an integration boundary, not a
license to weaken the storage audit. VM and unrelated vendor policy remain
separate projects. The optional reference T700 integration is confined to plain
Hibernate; it does not alter ordinary suspend, send AT commands, or start GNSS.

The USBClone gate is earlier than the ordinary integration boundary. This
prevents the proven bound-gadget condition from reaching an ordinary T700 hook.
It does not clear T700 review state: a pre-existing latch remains owned by the
T700 verifier and must be reviewed through that subsystem.

## Installed state and upgrades

The v1.2.1 ordinary runtime architecture is
`ordinary-usbclone-gate-v1+suspend-contract-v1`; the separate
`plain-hibernate-reference-v1` contract is unchanged. Maintenance is a separate
transaction around project-owned installation paths. Canonical
root-only state records schemas, release identity, file ownership classes and
hashes, selected configuration identity, migrations, pristine repair payload,
and one compatible rollback pointer. The public v1.0.0 legacy state is accepted
only after matching its published fixed file identities.

An upgrade verifies active runtime and systemd jobs before mutation, holds a
project flock throughout, snapshots exact prior files, applies declarative
schema migrations, atomically replaces allowlisted files, reloads systemd and
udev metadata, validates the merged graph and rule, and commits the new state
last. Precommit failure reverses the mutation list. Repair and deliberate
rollback use the same active-transaction guard and local transactional writer.
Config schema 1 to 2 adds disabled Hibernate defaults while preserving existing
supported values. Wiring revision 2 adds only Hibernate-specific units and the
`systemd-hibernate.service` drop-in; rollback restores the prior exact unit and
configuration snapshots.
