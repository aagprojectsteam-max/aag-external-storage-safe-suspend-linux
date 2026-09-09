# Integration boundaries

The configuration accepts optional absolute-argv commands at two serialized
boundaries:

- `integration.pre_suspend_command` runs only after the target is safely
  released and before the transaction is committed to native suspend.
- `integration.post_resume_command` runs before the terminal target re-audit.

A nonzero exit fails closed. Shell strings are not accepted. Integrations must
not mount protected internal storage differently, bypass target identity,
remove the transaction marker, force-unmount, issue generic process signals, or
initiate power-state actions.

The plain-Hibernate reference profile adds a separate, explicit T700 boundary.
It verifies generic PCI vendor/device and driver identity, waits for stable
MBIM and WWAN nodes, performs one configured absolute-argv recovery action (or
the built-in single ModemManager recovery), then requires both ModemManager
enumeration and NetworkManager connectivity. It never starts GNSS. Subscriber,
modem, profile, serial, and raw evidence identities remain outside the public
tree; support is limited to the tested reference platform.
