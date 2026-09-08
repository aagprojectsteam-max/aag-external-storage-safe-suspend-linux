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
