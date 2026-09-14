# Exact rollback

Keep the verified installer and the local backup created before installation.
Do not roll back merely because publication occurred; the accepted production
installation was left unchanged by publication.

## Qualified transaction adapter

The deployment prints `ROLLBACK=<backup-directory>` and saves its exact
manifest. Keep it private: it can include original configuration and machine
paths. With the same verified asset, use:

```bash
sudo ./aag-external-storage-safe-suspend-linux-v1.3.0.run transaction --report /var/lib/aag-sleep-transaction/rollback-report --rollback <recorded-backup-directory>
```

Before live rollback, keep the lid open and establish that no suspend/recovery
transaction or relevant job is active. Keep LockLock controls idle. The accepted
deployer restores only its recorded files, original modes/ownership and boot
enablement link, then reloads systemd definitions. If LockLock code was restored,
reload only its affected daemon while controls are idle. No reboot is needed.
When deployment occurred in stages, restore manifests newest to oldest to reach
the original installation. Never replay archived runtime state or manually
remove a live storage fence.

The isolated `transaction --root <temporary-prefix> --test-mode` route restores
its `isolated-manifest.json` snapshot only to the same prefix. It verifies
snapshot hashes and rejects unexpected paths or destination symlinks. This is
package validation and does not operate on live production dependencies.

## Portable coordinator

Use `sudo aag-safe-suspend rollback`. The canonical installed state must point
to a hash-verified compatible exact previous snapshot; relevant jobs and active
fences refuse the operation. Configuration, managed files, versions and unit
wiring return to that recorded state. Running an older installer is an unsupported
downgrade, not a substitute for rollback. The previous v1.2.1 release remains
available as historical reference.

`./aag-external-storage-safe-suspend-linux-v1.3.0.run uninstall` applies to the
portable profile only. It is not the removal command for the reference adapter.
v1.3.0 invalidates derived bytecode for replaced managed Python modules so a
rapid same-size rollback reports and executes the restored version. It validates
cache ownership and rejects symlinked cache directories/entries.

Private forensic originals and user storage are not publication assets and are
never removed as part of publishing or verifying a rollback.

## Reference Hibernate extension

Use the verified v1.4.0 asset's `hibernate --report <private-report-directory>
--rollback <recorded-s4-backup-directory>` route. It restores the exact S4
manifest's bytes, permissions and ownership, validates the graph and reloads
systemd; it does not perform a power transition. The operation requires an idle
power ledger. Apply staged S4 snapshots newest first before rolling back the
underlying reference transaction adapter. Do not replay archived runtime state or
remove a live failure fence to force rollback. Preserve the accepted v1.3.1 release
and the original baseline snapshot. The same route supports isolated roots with
`--root <temporary-prefix> --test-mode`.
