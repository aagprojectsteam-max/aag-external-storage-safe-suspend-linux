# Integration boundaries

## Qualified reference integrations

v1.4.1 coordinates the reviewed AAG storage guard, recovery auditor, workload
adapters, FM350/T700 and LockLock through private pinned contracts. The reference
blocker policy supports generic eligible ordinary userspace and supported guest
shutdown, with independent storage release verification. See
[the safety model](SAFETY-MODEL.md). The portable hook restrictions below do not
replace that reference policy.

S4 WWAN recovery belongs solely to
`aag-hibernate-transaction-finish.service`. Suspend retains its accepted v1.3.1
integration. Do not add a portable recovery hook or competing timer to the
reference S4 graph. GNSS remains on demand. See [FM350/T700](T700-WWAN-HIBERNATE.md).

## LockLock and Input Lock

| LockLock lid-ignore | Required behavior |
|---|---|
| OFF | Normal coordinated lid-close Suspend |
| ON | Lid-close ignore is intentional |
| Turned OFF after ON | Normal lid-close behavior returns |

Lid-ignore is distinct from keyboard, mouse and touchpad lock controls. The
power transaction respects intentional ignore, owns its temporary recovery
inhibitor, and reconciles stale state so it cannot poison a later transaction.
The final qualification passed OFF/ON Suspend interoperability, S4 state and
user-confirmed desktop/input recovery. This repository documents the boundary;
LockLock's own project remains the source for its complete control interface.

## Portable profile hooks

The configuration accepts optional absolute-argv commands at two serialized
boundaries:

- `integration.pre_suspend_command` runs only after the target is safely
  released and before the transaction is committed to native suspend.
- `integration.post_resume_command` runs before the terminal target re-audit.

A nonzero exit fails closed. Shell strings are not accepted. Integrations must
not mount protected internal storage differently, bypass target identity,
remove the transaction marker, force-unmount, issue generic process signals, or
initiate power-state actions.

For ordinary suspend, the ConfigFS/dummy-HCD preflight completes before either
integration boundary or the transaction fence. A refusal therefore cannot call
a configured T700 pre-hook and cannot manufacture a new T700 review latch. The
gate does not acknowledge or delete an existing T700 latch; preserving that
separate verifier's fail-closed ownership is an invariant.

The plain-Hibernate reference profile adds a separate, explicit T700 boundary.
It verifies generic PCI vendor/device and driver identity, waits for stable
MBIM and WWAN nodes, performs one configured absolute-argv recovery action (or
the built-in single ModemManager recovery), then requires both ModemManager
enumeration and NetworkManager connectivity. It never starts GNSS. Subscriber,
modem, profile, serial, and raw evidence identities remain outside the public
tree; support is limited to the tested reference platform.
