# Troubleshooting

## v1.4.0 reference transaction stack

Identify the profile first. The reference adapter records the episode, stage,
first failure, process stop receipts, kernel entry/exit and actual sleep. A
successful systemd request alone is not a completed sleep. Inspect the relevant
AAG storage, native suspend, resume-check and failure-recovery units and the
private transaction state. Preserve and sanitize evidence before sharing it.

Stale FAILURE_PENDING is reconciled by the owner. Do not delete a live fence,
T700 latch or automount marker, or start repeated suspend requests to clear it.
A live critical blocker with no supported safe shutdown remains an explicit
exception. Ordinary userspace does not require an app allowlist; failed safe
release is diagnosed from exact resource ownership.

`CHECKPOINT_API_INCOMPLETE` means the preferred API was unavailable; the exact
resource-audit fallback still runs. Do not report a successful checkpoint merely
because suspend completed. Guest/USB Clone profiles and workloads configured
with never-auto-restart intentionally remain stopped after resume.

A closed-lid wake is not a lid-open pass. Measured hardware sleep and validated
storage/device recovery are required; a retry remains bounded to its episode.
Genuine thermal/battery conditions retain safety supervision; ordinary timer
expiry or exhausted retries no longer cause normal recovery poweroff.

## Reference Hibernate refuses a GO gate

Run only the read-only check on an already configured reference stack:

```bash
sudo /usr/local/libexec/aag-power-transaction hibernate-readiness
```

Inspect the named kernel, memory/image, swap/resume mapping, initramfs, storage,
LockLock or ownership gate. Nominal swap size or an earlier passing test does not
replace current readiness. See [Hibernate prerequisites](HIBERNATE.md).
Do not bypass a failed gate or invoke repeated physical cycles to diagnose it.

## Reference S4 image resumed but WWAN is unusable

Keep the failed outcome explicit and preserve live evidence before any reboot or
recovery change. Check, in order:

1. Kernel/native image-resume evidence and original boot identity, followed by
   session recovery; a desktop appearing after cold boot is not image proof.
2. The exact invocation and result of
   `aag-hibernate-transaction-finish.service`, including whether it acquired the
   operation lock and reached modem restoration.
3. Active native/storage/boot callbacks and the actual kernel lock holder.
   A persistent lock file alone is not a held lock; do not delete it to bypass
   ownership or start another recovery service.
4. FM350/T700 PCI identity, driver binding, current WWAN interface generation,
   control ports, ModemManager and NetworkManager state.
5. The saved connection's restoration requirement, current connection, IP,
   routing, and a small DNS/HTTPS check through the cellular interface. Working
   Wi-Fi does not prove mobile recovery.
6. DATA, UGREEN safe-release state, required consumers, LockLock, filesystem
   health, terminal transaction state and stale fences.

Transient modem recovery errors are known on this reference platform; the
accepted automatic path took approximately two minutes. A visible device alone
is insufficient. If the bounded owner fails, preserve the private observations
and diagnose the exact failure without PCI resets, driver reloads, AT commands,
GNSS activation or a second physical S4 experiment. A reboot restoring service
would still leave the preceding Hibernate acceptance failed.

The preceding lock-collision defect is fixed in v1.4.0. Late storage teardown
must finish before the sole owner acquires the lock; modem enumeration must
finish before profile activation. Compare those stages with the saved evidence.
See [WWAN recovery](T700-WWAN-HIBERNATE.md) for the qualified behavior.

## LockLock lid behavior differs from expectation

Inspect the lid-ignore control separately from input locks. ON intentionally
ignores lid closure; OFF restores normal coordinated Suspend. Check inhibitor
ownership and transaction identity if the behavior persists after disabling
ignore. Let the owning subsystem reconcile stale state; do not remove a live
fence or repeatedly close the lid. The accepted
[LockLock boundary](INTEGRATIONS.md#locklock-and-input-lock) preserves subsequent
normal transactions and requires session/input recovery.

## Portable profile

The remaining guidance describes the portable coordinator and its conservative
read-only USBClone gate.

## Installer refuses a systemd job

The installer continuously observes the systemd job queue. Power, storage,
project-owned, and unknown jobs can invalidate the install snapshot and fail
closed. Wait for the named job to finish, investigate it, and rerun only when
you understand its role. Numeric job IDs are transient and are never
whitelisted. Known bounded maintenance services are allowed semantically.

## Target identity is incomplete

Use a whole-disk `/dev/disk/by-id/...` link for initial discovery. Every target
partition must contain a filesystem with a unique UUID. A serial mismatch,
changed partition set, missing USB properties, ambiguous disk, or changed
`diskseq` is a safety refusal, not a reason to substitute `/dev/sdX` in the
configuration.

## Suspend is blocked by an owner

Close software intentionally using the target. Check VMs, containers, loop
images, NBD exports, device-mapper stacks, shells whose cwd is on the disk, and
memory-mapped files. Do not force-unmount or kill an unknown process. Preserve
the incident record under `/var/lib/aag-external-storage-safe-suspend/incidents`
after sanitizing it before any public report.

## Ordinary suspend is blocked by the USBClone gate

`ACTIVE_USBCLONE_MASS_STORAGE_GADGET` means a ConfigFS mass-storage gadget is
still bound to a dummy-HCD controller. Shut down the owning WinBoat/QEMU guest
through its normal interface and verify that the guest and backing-storage
ownership are gone. Do not write an empty string to `UDC`, remove ConfigFS
objects, detach USB, or signal QEMU merely to make suspend pass.

`ACTIVE_UNMANAGED_CONFIGFS_GADGET`,
`ACTIVE_CONFIGFS_GADGET_OUTSIDE_PROVEN_SCOPE`, and
`ACTIVE_DUMMY_HCD_DEVICE_WITHOUT_CONFIGFS_OWNER` are conservative refusals, not
claims that those conditions caused the accepted regression. Loaded dummy-HCD
controllers with no bound device are allowed.

If a separate T700 integration already has a review latch, the project leaves
it intact. Review its originating cycle and use only that subsystem's documented
acknowledgement procedure. Never delete `blocked.json` to bypass the verifier.

## Resume fence remains armed

Run:

```bash
sudo /usr/local/libexec/aag-safe-suspend status
sudo journalctl -u 'aag-external-storage-safe-suspend*' --since today
```

Do not manually remove the marker while the lid is closed or the external disk
may be owned. Open the lid, keep the machine ventilated, and determine whether
the failure service reached a safe lid-open recovery. Redact evidence before
sharing it.

## Timeshift no longer starts

Inspect project state first. A non-IDLE fence intentionally rejects new backup
starts. Also verify diversion ownership with `dpkg-divert --listpackage
/usr/bin/timeshift`. Do not move diverted binaries manually; use the verified
release uninstaller or report a private storage-safety issue.

## Uninstall refuses

Uninstall requires no active fence, IDLE project state, unchanged project-owned
files, and a clear semantic job guard. The refusal preserves the installation.
Resolve the exact conflict rather than deleting project state or using package
force options.

## Plain Hibernate is not ready

Run the non-destructive report:

```bash
sudo aag-safe-suspend health-check
```

Do not invoke Hibernate if any resume-device, resume-offset, swapfile,
initramfs, image-capacity, memory-safety, kernel-state, T700 or UGREEN gate
fails. The project intentionally does not repair swap, boot-loader or initramfs
configuration automatically. Review [the Hibernate guide](HIBERNATE.md) and fix
the actual mapping or capacity issue before trying again.

## WWAN does not return after plain Hibernate

Inspect the Hibernate-only recovery and resume services. The normal policy
waits for a stable T700 device generation and invokes one bounded recovery
action; it does not repeatedly reset the modem, PCI endpoint or driver. A
failure remains explicit. Do not use AT commands or start GNSS as a generic
recovery experiment. See [T700 WWAN Hibernate recovery](T700-WWAN-HIBERNATE.md).
