# Security policy

## Supported version

Security and storage-safety fixes are provided for the latest release.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting feature on this repository.
Do not open a public issue for a flaw that could cause filesystem corruption,
data loss, unintended power actions, privilege escalation, or secret exposure.

Include the release version, Ubuntu version, systemd version, desktop/session,
bridge chipset, filesystem types, relevant sanitized unit state, and the exact
failure boundary. Remove serial numbers, UUIDs, usernames, hostnames, network
addresses, modem identifiers, credentials, document paths, and unrelated
journal lines before sharing evidence.

The maintainers will acknowledge a complete report when practical, reproduce
it with synthetic fixtures first, and coordinate disclosure after a fix and
rollback path exist. Never attach raw `/proc`, environment, browser-profile,
snapshot, or whole-journal dumps.

## Safety boundary

This software is defense in depth, not a substitute for current backups,
filesystem checks, firmware updates, or hardware qualification. It cannot
guarantee recovery from every kernel, firmware, controller, cable, or power
failure. The default emergency policy never powers off the system.
