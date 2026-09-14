# GNOME and udisks automount race

## The race

An external bridge may disappear during suspend and reappear after resume.
udisks and a desktop volume monitor can interpret that generation as newly
attached media and automount one or more partitions after the pre-sleep release.
A resume verifier that assumes the disk stayed unmounted can then release its
fence too early.

## Targeted prevention

The installer generates one udev rule per configured filesystem UUID. A rule
matches all of these properties:

- block partition and add/change event;
- underlying serial;
- USB bridge vendor/product pair, when available;
- exact filesystem UUID; and
- existence of the boot-scoped transaction marker.

Only then does it set `UDISKS_AUTO=0`. It does not change GNOME settings, stop
udisks, or affect an unrelated disk. The marker exists before the target can be
released and remains across disconnect/re-enumeration until terminal audit.
An image resume restores `/run` as part of the saved kernel/session state, so
the marker remains relevant across S4. A cold boot clears `/run`; in the
qualified reference S4 stack, durable boot reconciliation preserves the old
ledger and re-establishes its transaction-local fence when recovery is required.
A reboot is not proof of successful restoration and must not be used to bypass
the fence. See [the S4 contract](hibernate-transaction.md).

## Defense in depth

Prevention is not treated as proof. The resume verifier resolves the current
generation, discovers every exact target filesystem, scans relevant namespaces
and owners, cleanly unmounts a harmless host automount, then re-resolves and
re-audits. Automount during the audit, a changed generation, or a genuine owner
causes retry or fail-closed behavior. The fence is released only with every
intended target filesystem unmounted or the target absent for the complete
bounded enumeration window.
