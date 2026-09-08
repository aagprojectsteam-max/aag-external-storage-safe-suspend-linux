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

The accepted reference machine also verified coexistence with a separate modem
and GNSS policy. That code and its private evidence are intentionally out of
scope. This repository provides only the generic argv interface and makes no
support claim for a particular modem.
