# Qualified reference Hibernate transaction

This extension passed one controlled real S4 requalification, including automatic
cellular Internet recovery and normal session/input recovery without reboot.
The physically tested implementation is `5cc43022551ad521f13af1eea72099b854e45230`.
It does not enable Hibernate on portable installations, battery
fallback, lid-close fallback, hybrid sleep or suspend-then-Hibernate.

The accepted v1.3.1 reference payload remains unchanged. Additional systemd
drop-ins invoke `aag-power-transaction` with the exact existing delegate command.
For ordinary Suspend the dispatcher execs that command, with its original
arguments. For Hibernate, a `production.Host` specialization uses the same
preparation methods, workload receipts, runtime fence, durable `current.json`
and terminal transaction archive. Existing untyped records mean ordinary
Suspend; S4 records explicitly carry `transaction_type=HIBERNATE`.

The S4 phase history records request, preparation, blockers, storage release,
image-write request, kernel S4 entry, power-off, boot return, confirmed image
resume, restoration and completion. Entry and power-off are recorded
retrospectively only when the kernel/native/session evidence establishes a real
image round trip. Successful command dispatch and ordinary suspend counters
cannot establish Hibernate acceptance. The origin boot identity is immutable;
a boot without a preceding native return produces `COLD_BOOT_FALLBACK`. A reboot
after a recorded native return or confirmed image resume instead produces
`REBOOT_AFTER_RESUME_FAILURE`. The previous ledger is preserved before boot
reconciliation; a later reboot cannot erase image evidence or accept a failed
peripheral recovery.

Storage preparation remains v1.3.1. Internal DATA stays mounted. The accepted
removable-storage policy restores UGREEN to a verified, safely released state;
it does not force-remount partitions after bridge re-enumeration. A registered
consumer requiring an automatic external mount is refused before preparation
until an explicit safe mount-restoration contract is available. This limitation
does not apply to consumers on internal DATA. Required internal consumers are
restored from durable receipts, including interrupted stop/start intents.
Detection precedes restart; duplicate completion does not start them twice.
User-stopped workloads, manual processes and `never` restart policies remain
stopped.

`aag-hibernate-transaction-finish.service` is the sole S4 recovery owner. The old
four Hibernate units and both old WWAN helper entrypoints are retired; the old
system-sleep hook no longer schedules a competing timer. The owner waits for a
stable PCI/MBIM/network generation and consumes one durable recovery token
before a bounded ModemManager/profile recovery. It never rescans, resets or
rebinds PCI devices, starts GNSS, changes modem power policy or enables a radio
that was disabled before sleep. The pre-logind network snapshot determines
whether a connected profile must be restored.

The finish service waits for native, storage and boot callbacks to become
inactive before acquiring the operation lock. This handles the late
`StopWhenUnneeded` teardown job that can appear after native `OnSuccess` has
already started the finish service. Native return only persists its result;
systemd queues the finish owner after that callback exits. Boot reconciliation
also delegates restoration to that service. No other service runs WWAN
recovery, and no service restart loop is introduced.

The old native drop-in is replaced at its original pathname because an empty
dependency directive in a later drop-in does not remove prior dependencies.
Readiness checks require the effective `OnSuccess` and `OnFailure` sets to
contain exactly the chosen owner. Journal queries normalize boot UUIDs to the
32 hexadecimal characters expected by `journalctl`. WWAN observations are
persisted during settling; after its one permitted ModemManager restart, the
owner waits for modem enumeration before attempting the original NM profile.

The public script `scripts/deploy_hibernate.py` supports an isolated prefix and
exact-file rollback. Live deployment requires protected baseline hashes,
passing simulation/regression gates, an idle power ledger and a private pinned
`hibernate.json` configuration. It does not stop workloads, restart services,
dispatch a power transition or publish a release. It adds the S4 unit graph and
reloads systemd only after validation. Original files and permissions are
preserved in its rollback manifest.

v1.4.1 exposes that same deployer through the verified `.run` asset's `hibernate`
command. Supply `--config <private-hibernate-config> --gates <verified-gates>`
and `--baseline <accepted-installed-baseline>` with a private `--report`
directory. These files must describe this host's reviewed dependencies and
successful gates, not copied claims from another machine. The `transaction`
route retains the exact v1.3.1 reference package initializer while the portable
package reports the new release version. Runtime hashes for both accepted
profiles are pinned in the release payload.

The accepted cycle still exhibited a transient modem exception, PM timeout and
non-fatal PCIe reports. The single owner recovered usable cellular service in
approximately two minutes through one ModemManager restart and bounded saved
profile activation. This qualifies automatic functional recovery on the tested
stack; it does not claim that the modem's firmware exception has disappeared.
The prior cycle that needed a manual reboot remains a separate failed WWAN result.

After deployment, `aag-power-transaction hibernate-readiness` performs read-only
checks. The separately authorized `hibernate-once` action records evidence and
a consumed-per-attempt permit, then requests one native systemd Hibernate.
Memory, selected swap, device/offset, exact initramfs, pinned files, storage,
LockLock and recovery ownership are checked again immediately before the image
write. A failed gate is a clean refusal with preparation reconciliation; it
does not invoke the ordinary suspend retry/poweroff monitor.

Cold-boot reconciliation uses current device identities and saved consumer
policy without replaying previous-boot sysfs state. A real restoration failure
retains an explicit transaction-local fence and evidence until safe idempotent
reconciliation succeeds. No timer relabels that failure as success.
