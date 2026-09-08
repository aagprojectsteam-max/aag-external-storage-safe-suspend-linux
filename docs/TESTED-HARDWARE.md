# Tested hardware and support matrix

## Tested on

| Component          | Accepted reference                  |
| ------------------ | ----------------------------------- |
| Operating system   | Ubuntu Desktop 26.04 LTS            |
| Desktop/session    | GNOME on Wayland                    |
| Sleep mode         | s2idle                              |
| External storage   | NVMe through a UGREEN USB enclosure |
| Bridge             | Realtek RTL9210 (`0bda:9210`)       |
| Target filesystems | ext4                                |
| Backup integration | Timeshift wrapper/fence enabled     |

Serial numbers, filesystem UUIDs, hostnames, usernames, and other
machine-specific identifiers are intentionally omitted.

## Supported by design

- One explicitly selected USB-attached whole disk.
- A different kernel device name after re-enumeration.
- Multiple filesystem-bearing partitions, each with a unique UUID.
- Configured internal mount identity protection.
- Unrelated USB media appearing during the same resume window.
- Optional generic pre-suspend and post-resume hooks.

## Untested

- Other Linux distributions and init systems.
- X11 sessions or non-GNOME desktop automounters.
- Other bridge chipsets and enclosure firmware.
- Encrypted, LVM, ZFS, btrfs subvolume, RAID, or other stacked target layouts.
- Thunderbolt/PCIe external storage.
- Multiple independently protected external backup disks.
- Hibernate and suspend-then-hibernate.

## Qualification requirement

Passing the offline suite is necessary but not sufficient for new hardware.
Validate current backups, inspect `aag-safe-suspend validate`, and perform one
supervised ordinary suspend/resume acceptance cycle on a hard ventilated surface
before relying on a new platform.
