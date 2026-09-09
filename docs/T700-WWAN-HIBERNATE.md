# T700 WWAN recovery after plain Hibernate

## Accepted failure and correction

The first automatic post-Hibernate recovery ran against the pre-exception T700
ports. The driver subsequently entered its native exception recovery and
regenerated the MBIM and network ports, leaving the early ModemManager attempt
bound to a stale generation.

The accepted correction is Hibernate-specific:

1. allow the native post-S4 driver recovery window to settle;
2. require the configured T700 PCI identity, `mtk_t7xx` binding, MBIM node and
   WWAN network identity for ten consecutive seconds;
3. invoke one bounded recovery action;
4. require ModemManager to enumerate a modem; and
5. require NetworkManager to report a connected GSM/WWAN device.

The reference settling value is 110 seconds because that was the successfully
observed point on the accepted platform. It is not claimed to be universal for
all modems or firmware. The bounds are explicit configuration, and failure is
reported rather than followed by repeated resets.

The built-in recovery action restarts ModemManager once after the stable device
generation exists. A reviewed absolute-argv `recovery_command` can replace that
single action for an integration with equivalent semantics. The implementation
does not reset PCI, reload the driver, send AT commands, or start GNSS.

This service is ordered only from `systemd-hibernate.service`. It has no
dependency edge to `systemd-suspend.service`; the accepted ordinary-suspend
T700 lifecycle remains unchanged.

No modem serial, IMEI, ICCID, subscriber identity, profile name, or private
journal data is part of the public source or tests.
