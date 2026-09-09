# Plain Hibernate on the reference platform

## Support statement

Version 1.2.0 supports plain Hibernate on the physically verified reference
platform. This is a scoped hardware result, not a universal Linux guarantee.
Suspend-then-hibernate and hybrid sleep remain disabled and unaccepted.

The accepted physical cycle created a kernel Hibernate image, reached complete
power-off, resumed only after manual power-on, restored the existing desktop
session, preserved internal storage, cleanly handled the external target, and
recovered the reference T700 WWAN path using the final delayed-generation
correction. Only normalized results and synthetic fixtures are public.

## Readiness

Enable the policy only on the documented reference platform:

```bash
sudo ./aag-external-storage-safe-suspend-linux-v1.2.0.run install \
  --enable-reference-hibernate
sudo aag-safe-suspend health-check
```

The non-destructive health check reports:

- `RESUME_DEVICE_GATE`
- `RESUME_OFFSET_GATE`
- `SWAPFILE_IDENTITY_GATE`
- `INITRAMFS_RESUME_GATE`
- `IMAGE_CAPACITY_GATE`
- `MEMORY_SAFETY_GATE`
- `CURRENT_KERNEL_STATE_SUPPORT`
- `T700_HIBERNATE_RECOVERY`
- `UGREEN_HIBERNATE_POLICY`

All applicable gates must pass. The swapfile must be active, root-owned mode
`0600`, on the internal ext4 resume filesystem, and match the current kernel
command line, `/sys/power/resume`, `/sys/power/resume_offset`, its logical
extent-zero physical page, and the current initramfs resume configuration.

The corrected capacity model treats `MemTotal - MemAvailable` as one broad,
non-overlapping pressure estimate. It does not add `AnonPages` or `Shmem` to
`Unevictable`, which could count the same physical folios twice. It also requires
the image policy to cover the non-reclaimable floor plus a 2 GiB margin,
`MemAvailable` to leave 4 GiB beyond the configured image size, and free swap to
cover the greater of 110% of RAM or 125% of image size.

## Transaction

Use only the project entry point:

```bash
sudo aag-safe-suspend hibernate
```

It arms a durable same-boot episode, enables a one-cycle cold-boot detector,
and asks native systemd to enter plain Hibernate. The Hibernate-only drop-in:

1. requires the existing external-storage preparation transaction;
2. rechecks every resume and capacity gate immediately before image creation;
3. refuses unless the external-storage release is committed;
4. lets the kernel perform native image creation and S4 power-off;
5. waits for the optional T700 stable-generation recovery;
6. performs the terminal external-storage identity, owner, mount and protected
   internal-storage audit; and
7. releases the fence and disables the boot detector only after success.

Failure before image creation is a safe refusal. A failed native transaction is
reconciled without retrying Hibernate. A cold boot is recorded and never
reported as an image resume. No path automatically powers the machine back on.

## Deliberate exclusions

The project does not configure a resume target, resize or recreate swap, rebuild
initramfs, enable a firmware wake, start GNSS, send AT commands, reset PCI,
reload `mtk_t7xx`, or enable Hibernate as a suspend-failure fallback. Those are
separate administrator decisions or future milestones.

## Transition from the accepted qualification

The reference machine's final qualification payload is not copied into release
assets. During a later explicit v1.2.0 upgrade, the installer may recognize its
complete final helper/unit set by public hashes, save those exact files in the
bounded rollback snapshot, remove duplicate qualification wiring, and replace
it with the generalized project-managed implementation. Any partial or changed
set is refused. Raw qualification state and evidence remain untouched.
Exact adoption preserves the accepted plain-Hibernate and T700 policy as
enabled; an ordinary v1.1.1 upgrade receives disabled Hibernate defaults.
