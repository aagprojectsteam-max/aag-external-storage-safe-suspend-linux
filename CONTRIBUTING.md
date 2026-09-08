# Contributing

Thank you for improving the project. Storage and power-management changes are
high consequence, so every change must preserve fail-closed behavior.

Before opening a pull request:

1. Run `make check` on a supported Python version.
2. Add sanitized synthetic fixtures for every changed decision path.
3. Run `make release-check` if installer, systemd, udev, or packaging behavior
   changed.
4. Explain how stable identity, protected internal mounts, owner auditing,
   rollback, and power-state invariants are preserved.
5. State what was tested physically and what remains simulated or untested.

Do not submit machine evidence, serials, UUIDs, user paths, journal dumps,
credentials, modem data, or private application names. Tests must not depend on
`/dev/sdX`, timing sleeps as synchronization, force/lazy unmount, broad process
killing, or global desktop automount changes.

Changes that can issue suspend, hibernate, reboot, shutdown, or poweroff are not
accepted in the installer or test suite. A physical acceptance plan belongs in
the pull-request description and must remain a separate, explicit human action.
