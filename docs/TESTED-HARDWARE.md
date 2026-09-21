# Tested hardware and support matrix

## Production-qualified reference configuration: v1.4.1

| Component | Accepted reference context |
|---|---|
| Operating system / desktop | Ubuntu Desktop 26.04 LTS / GNOME on Wayland |
| Reference laptop | HP EliteBook 840 14 inch G11 |
| Qualified firmware | HP W70 01.10.00; Intel ME 18.0.21.2801; USB-C/PD 2.9.0 |
| Suspend | s2idle; normal physical lid-close and long Suspend qualified |
| Hibernate | Plain S4 with image and session restoration |
| Memory / swap | 64 GiB RAM / 72 GiB internal ext4 swapfile |
| Internal DATA | Identity-checked, mounted ext4 storage |
| External UGREEN | USB-attached NVMe, Realtek RTL9210 bridge (`0bda:9210`) |
| External restoration policy | All target partitions verified safely released; no forced remount |
| Cellular integration | FM350 / MediaTek T700, `mtk_t7xx`, ModemManager and NetworkManager |
| WWAN after S4 | Real mobile DNS/HTTPS restored automatically without reboot |
| AAG LockLock / Input Lock | Lid-ignore OFF/ON integration and S4 state qualified |
| GNSS | OFF on demand; not automatically started |

The memory and swap sizes describe the tested machine, not universal minimums.
The live memory gate required the Windows guest to be shut down. Transient modem
errors recovered automatically in approximately two minutes. See the
[accepted matrix](ACCEPTANCE.md) for the exact validation scope. Hostnames,
usernames, serials, filesystem UUIDs and modem/SIM identifiers are omitted.

## Generic Linux design and portable capabilities

These design capabilities do not establish physical qualification on another host:

- One selected USB-attached whole disk, reidentified after device-name changes.
- Multiple filesystem-bearing partitions with distinct stable identities.
- Protected internal mounts and unrelated USB media left outside target actions.
- Portable optional pre-suspend/post-resume hooks and Timeshift fencing.
- Loaded empty dummy-HCD controllers accepted by the portable gate; bound
  gadgets refused without automatic guest or USB teardown.
- The reference profile separately supports reviewed guest shutdown followed by
  verified ownership release; it never blindly kills a guest or detaches active USB.

The portable and reference graphs are separate installation profiles. The older
portable ext4/Timeshift and USBClone results remain historical acceptance records.
They do not extend the v1.4.1 S4 reference claim to every integration.

## Not qualified by this result

- Other distributions, init systems, desktops, kernel/firmware combinations or
  bridge chipsets.
- Encrypted, LVM, ZFS, btrfs subvolume, RAID or other stacked target layouts.
- Thunderbolt/PCIe external storage or multiple independently protected disks.
- Required automatic external-partition remounts after S4.
- Hybrid sleep, suspend-then-hibernate, arbitrary S4 hardware or long-term endurance.

## Qualifying another platform

Offline tests and a compatible inspected configuration are prerequisites, not
physical proof. Use the readiness checks for the selected
[installation profile](INSTALLATION.md), verify backups, and establish a safe
thermal, workload and storage state before supervised physical qualification.
Hibernate additionally requires all current [resume and capacity gates](HIBERNATE.md).
Accepted production installations do not need repeated cycles for documentation
or release publication.
