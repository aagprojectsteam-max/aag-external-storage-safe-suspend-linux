# Normalized acceptance record

The private engineering evidence was reviewed and normalized; raw logs, serials,
UUIDs, paths, process dumps, and unrelated application data are not published.

Final physical acceptance on the reference platform produced:

| Gate                                         | Result            |
| -------------------------------------------- | ----------------- |
| One PM suspend entry and exit                | PASS              |
| Hardware s2idle residency                    | PASS              |
| Hardware sleep duration                      | 90.077358 seconds |
| Targeted UGREEN automount suppression        | PASS              |
| Post-resume target automounts                | 0                 |
| Terminal target re-audit                     | PASS              |
| All target filesystems unmounted             | PASS              |
| Backup-start fence returned IDLE             | PASS              |
| Internal data identity and mount             | UNCHANGED         |
| Separate modem/GNSS integration boundary     | UNCHANGED         |
| User session                                 | CONTINUED         |
| Orderly poweroff requests                    | 0                 |
| Stale historical incident latch reintroduced | NO                |
| New real blocker                             | NONE              |

These are observations from one accepted reference machine. They do not claim a
universal hardware guarantee. The public reconstruction adds only sanitized,
synthetic fixtures; no raw private evidence is shipped.

## Plain-Hibernate acceptance

One separately authorized reference-platform cycle produced:

| Gate | Result |
| --- | --- |
| Kernel Hibernate image created | PASS |
| Complete power-off before manual power-on | PASS |
| Resume from the image, not cold boot | PASS |
| Existing desktop session restored | PASS |
| Resume device, offset and swapfile identity | PASS |
| Image capacity and corrected memory model | PASS |
| External target cleanly released and terminally re-audited | PASS |
| Target-only automount policy and fence return to IDLE | PASS |
| Protected internal data identity and mount | UNCHANGED |
| Filesystem and kernel I/O error evidence | NONE |
| T700 stable-generation recovery correction | INSTALLED AND VERIFIED |
| ModemManager and NetworkManager WWAN state | RECOVERED |
| GNSS on-demand policy | UNCHANGED |
| Ordinary-suspend effective graph | UNCHANGED |
| Physical Hibernate cycles | 1 |

The original early WWAN recovery failure remains part of the private engineering
record. The public implementation contains the final delayed stable-generation
correction only. Plain Hibernate is accepted on the reference platform;
suspend-then-hibernate and hybrid sleep are not accepted.
