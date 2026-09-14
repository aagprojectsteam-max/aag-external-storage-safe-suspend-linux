# Normalized acceptance record

The private engineering evidence was reviewed and normalized; raw logs, serials,
UUIDs, paths, process dumps, and unrelated application data are not published.

## Final production qualification: v1.4.0

The public release is `v1.4.0`, release commit
`74a0b48`; the local production checkpoint is retained separately. A later
documentation-only commit on `main` does not change this immutable release identity.

| Gate | Accepted result |
|---|---|
| Automated suite / CI | 284 PASS / PASS |
| Physical normal lid-close Suspend | PASS; one transaction and real low-power sleep |
| Long Suspend | PASS |
| Ordinary userspace blocker handling | PASS |
| Stale FAILURE_PENDING / retry loop | NONE / NONE |
| Ordinary fail-safe poweroff policy | Corrected; poweroff not used in accepted cycle |
| Real S4 image resume | PASS |
| Desktop, keyboard, mouse and touchpad | PASS; confirmed by user |
| S4 WWAN recovery owner | aag-hibernate-transaction-finish.service |
| S4 WWAN recovery invocations / lock collision | 1 / NONE |
| FM350/T700 device, driver, control ports and manager state | PASS |
| Saved WWAN connection, IP and routing | PASS |
| DNS / small HTTPS check through mobile interface | PASS |
| Manual reboot required after S4 | NO |
| DATA identity, mount and health | PASS |
| UGREEN safe-release/restore policy | PASS; verified safely released partitions |
| Required consumers | PASS; restored according to saved policy |
| LockLock OFF/ON Suspend integration / S4 state | PASS / PASS |
| S4 terminal transaction / stale fence / stale WWAN lock | COMPLETE / NONE / NONE |
| Filesystem health | PASS; online checks |
| Full Hibernate acceptance | PASS |
| Accepted Suspend v1.3.1 baseline | PRESERVED |
| Source/installed SHA256 parity | 35 OF 35 |
| Exact rollback | VERIFIED; v1.3.1 baseline retained |
| Anonymous public release verification | PASS; 12 assets across v1.3.0, v1.3.1 and v1.4.0 |

The final suite has 284 tests; the 283-test count in the release's pre-physical
record precedes the additional release metadata regression. Packaging verified
both reference routes and actual public v1.3.1 portable upgrade/exact rollback
in isolated roots. It did not require another physical cycle.

The preceding real S4 image resume with WWAN lock collision remains
`PARTIAL_PASS_WWAN_FAILURE`, with a manual reboot required for modem recovery.
User-interrupted Suspend attempts remain `USER_INTERRUPTED` /
`INVALID_FOR_ACCEPTANCE`. Neither contributes a pass to this matrix.

Qualification is limited to the tested reference configuration. Transient modem
errors occurred after S4, but one recovery owner restored real mobile service in
approximately two minutes without reboot. The Windows guest was shut down for
the memory gate. UGREEN qualification is safe release, not a promise of automatic
external-partition remounts; consumer restoration respects `never` policies.
GNSS remains on demand. Long-term endurance and arbitrary hardware/firmware are
not qualified. See [tested hardware](TESTED-HARDWARE.md) and
[Hibernate recovery](T700-WWAN-HIBERNATE.md).

## Historical portable Suspend acceptance

The original portable physical acceptance on the reference platform produced:

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

## Historical portable plain-Hibernate acceptance

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

## Historical portable ordinary USBClone regression acceptance

The accepted reference regression was reconstructed from private evidence and
published only as normalized facts. A prior ordinary cycle with two bound
ConfigFS/dummy-HCD mass-storage gadgets produced virtual USB resume timeouts and
zero sustained hardware/PMC residency. The resulting T700 review latch correctly
blocked the next ordinary attempt before kernel suspend entry. UGREEN completed
its release and Hibernate was not part of that transaction, so neither was
causal. Loaded empty dummy-HCD controllers were independently accepted and are
not classified as the cause.

After the ordinary-only gate and reviewed one-time latch reconciliation, the
single final physical acceptance produced:

| Gate | Result |
| --- | --- |
| Native systemd suspend service | SUCCESS |
| Kernel PM entry and exit | ONE EACH |
| Kernel s2idle interval | 72.361356 seconds |
| Hardware low-power residency | 68.945860 seconds |
| PMC low-power residency | 68.945860 seconds |
| Ordinary USBClone gate | PASS |
| UGREEN release and terminal audit | PASS |
| Protected internal data | UNCHANGED |
| T700 pre/resume/verifier | PASS |
| New T700 review latch | NONE |
| Failure/retry/poweroff path | NOT INVOKED |
| User session | CONTINUED |
| Cycle-attributable kernel/storage errors | NONE |

Exact timestamps, boot/session identifiers, device identities, private paths,
and raw journal evidence are intentionally not public.
