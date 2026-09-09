# Security policy

## Supported versions

Security and storage-safety fixes are provided for v1.2.x. Public v1.0.0 through
v1.1.1 can be upgraded directly to v1.2.0 without uninstalling. The release
also retains the verified public v1.0.0 bootstrap path.

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

The updater's privilege and artifact trust boundaries are documented in the
[updater threat model](docs/UPDATER-THREAT-MODEL.md).
