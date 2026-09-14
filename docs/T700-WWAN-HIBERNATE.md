# FM350 / T700 WWAN recovery after Hibernate

## Qualified v1.4.0 integration

On the tested reference stack, **`aag-hibernate-transaction-finish.service` is
the only S4 WWAN recovery owner**. Native and boot callbacks delegate restoration
to it; retired helper paths and a competing system-sleep timer do not schedule
another recovery. Duplicate ownership could collide on the operation lock,
restart a manager against stale ports, or consume the same recovery attempt twice.

The preceding physical S4 failure resumed the image, but its one recovery
invocation encountered a lock collision and aborted before modem restoration.
The modem remained unusable until a manual reboot. That cycle remains
`PARTIAL_PASS_WWAN_FAILURE`; the successful requalification does not relabel it.

The corrected owner:

1. Waits for native, storage teardown and boot callbacks to become inactive
   before acquiring the operation lock, including late storage teardown jobs.
2. Waits through native driver recovery for the configured PCI identity,
   `mtk_t7xx` binding, MBIM control port and WWAN interface to form a stable
   device generation.
3. When restoration is needed, consumes one durable recovery token and performs
   one bounded ModemManager restart under the reviewed policy. A stable already
   healthy modem can be accepted without restarting it.
4. Waits for modem enumeration before bounded activation of the saved
   NetworkManager profile, when the pre-sleep policy requires reconnection.
5. Verifies restoration before transaction completion and preserves evidence
   if usable service does not return.

The tested settling policy used a 110-second window and ten seconds of stable
generation. Transient modem exceptions, PM timeouts and non-fatal PCIe reports
still occurred; automatic recovery restored usable service in approximately two
minutes. These bounds are reference configuration, not universal modem timings.
The correction establishes functional recovery, not elimination of the firmware
exception or instantaneous connectivity.

## What acceptance proved

| Check after image resume | Final result |
|---|---|
| Recovery invocations / lock collision / modem-restore stage | 1 / NONE / REACHED |
| FM350/T700 PCI device and driver binding | PASS |
| Control ports and WWAN interfaces | PASS |
| ModemManager and NetworkManager modem state | PASS |
| Saved WWAN connection, IP address and routing | PASS |
| DNS and small HTTPS check through the cellular interface | PASS |
| GNSS | Expected OFF, on demand |
| Manual reboot required / stale held WWAN lock | NO / NO |

Device enumeration and NetworkManager visibility alone are insufficient. Full
acceptance requires **S4 → image resume → automatic recovery → operational modem
→ usable mobile connectivity → no manual reboot**. The
[normalized acceptance record](ACCEPTANCE.md) contains the complete outcome.

The owner preserves the saved radio/profile policy, does not enable a previously
disabled radio, and never starts GNSS or sends AT commands. PCI reset, rebind,
rescan and driver reload were not required. Ordinary Suspend continues through
its accepted v1.3.1 lifecycle.

## Failure inspection and integration boundary

If WWAN does not recover, preserve live driver/device/control-port, manager,
connection, routing, service and lock evidence before changing the system.
Do not use reboot as proof of successful S4 recovery. Keep the result failed
until the cause and recovery are verified; see [troubleshooting](TROUBLESHOOTING.md).
Private journals, profile names, modem/SIM identifiers and network addresses
stay outside the public repository.

The portable coordinator retains a separate Hibernate-only T700 service with
stable-generation checks and an optional reviewed absolute-argv recovery hook.
That contract is described in [integration boundaries](INTEGRATIONS.md#portable-profile-hooks).
It is not a second owner to install alongside the reference finish service.
