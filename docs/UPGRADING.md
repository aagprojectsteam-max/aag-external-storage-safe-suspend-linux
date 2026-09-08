# In-place upgrades

## Normal update path

Download the target release's `.run`, `SHA256SUMS`, and
`release-manifest.json` from the same GitHub release. Verify the installer, then
run it directly. An existing supported installation is detected automatically;
`install` is optional for this path.

```bash
sha256sum --ignore-missing -c SHA256SUMS
chmod +x aag-external-storage-safe-suspend-linux-v1.1.1.run
sudo ./aag-external-storage-safe-suspend-linux-v1.1.1.run
```

The installer reports `FRESH_INSTALL`, `SAME_VERSION`, `UPGRADE`, or a precise
refusal. It never requires uninstall before a supported upgrade. The
`aag-safe-suspend upgrade` command intentionally does not download or execute
code in v1.x; it prints the verified manual procedure. There is no background
polling or telemetry.

## Version, state, and health

```bash
aag-safe-suspend --version
sudo aag-safe-suspend status
sudo aag-safe-suspend health-check
aag-safe-suspend update-check
```

`status` and `health-check` return one of `NOT_INSTALLED`, `HEALTHY`,
`MODIFIED`, `PARTIAL`, `UPGRADE_PENDING`, `ROLLBACK_PENDING`, or `BROKEN`.
Health exit statuses are respectively 2, 0, 3, 4, 5, 6, and 7. The update
check makes one read-only HTTPS request to GitHub Releases and installs nothing.

Canonical state is root-owned mode `0600` at
`/var/lib/aag-external-storage-safe-suspend/install-state.json`; its parent is
root-owned mode `0700`. It records product/release identity, installed version,
installer/config/state/migration schemas, the selected external identity,
project file hashes and classifications, migration IDs, a pristine local
repair cache, and bounded rollback metadata. It contains no host identifier,
username, evidence dump, or telemetry identifier.

## Transaction and failure behavior

The order is precheck, project flock, current-state verification, source
verification, rollback snapshot, declared migrations, atomic file replacement,
systemd daemon reload, udev rules reload, merged-graph and rule verification,
post-upgrade health check, then the new state commit. The installer never asks
systemd to suspend, hibernate, reboot, shut down, or power off.

Any pre-commit error restores every mutated project-owned path to its exact
prior bytes and modes. Transaction evidence is retained under the project state
directory. A rollback failure is made explicit rather than falsely reporting
success. Snapshot and pristine-payload generations are bounded; arbitrary
directories and user data are never swept or deleted.

The project lock is an advisory kernel flock on a persistent, root-only lock
file. A dead process releases the flock automatically, so stale lock-file text
cannot brick maintenance. The same lock serializes install, uninstall, repair,
rollback, and migrations.

## Configuration preservation and repair

`/etc/aag-external-storage-safe-suspend/config.json` is
`SUPPORTED_USER_CONFIG`. A valid local configuration is preserved byte for
byte during an ordinary upgrade, and the target-only udev rule is regenerated
from it. Project code, units, wrappers, and the generated rule are
`PROJECT_MANAGED`. A changed managed file is an
`UNKNOWN_LOCAL_MODIFICATION` and blocks ordinary upgrade.

To repair the current release from its root-owned pristine cache:

```bash
sudo aag-safe-suspend repair
```

Alternatively, rerun the verified same-version asset with `--repair`. Repair is
transactional, preserves valid user configuration, reconciles its generated
rule, and replaces only missing or corrupt project-managed files.

## Rollback and downgrade policy

```bash
sudo aag-safe-suspend rollback
```

Rollback is allowed only when the installed state names a hash-verified,
schema-compatible exact previous snapshot. It is itself transactional and runs
the same active-transaction guard. There is no arbitrary version selection.
Running an older installer is a downgrade, never an upgrade, and v1.1.x refuses
it with `DOWNGRADE_REFUSED_WITH_REASON`. A future release may declare a narrow
supported downgrade only when config, state, wiring, and rollback migrations
are explicitly reversible.

## First upgrade from public v1.0.0

v1.1.x contains the bootstrap identities of the public v1.0.0 tag. It requires
the root-only v1.0.0 `active.json`, matches every fixed runtime, wrapper, unit,
and drop-in against those known hashes, validates the existing configuration,
and matches the generated rule to the v1.0.0 recorded baseline. It then creates
the canonical state inside the same transaction and continues the upgrade.
Unknown edits, missing legacy files, corrupt state, or an identity mismatch
fail before mutation. Uninstall/reinstall is not required.

## Active transaction refusal

Upgrade, repair, rollback, and uninstall refuse an armed suspend/hibernate
fence, non-idle recovery state, protected backup registration, another project
maintenance operation, or a semantically relevant systemd job. Power, storage,
R1/sleep-recovery, UGREEN, and T700 jobs fail closed. Known bounded unrelated
jobs such as a CUPS reload do not create a false refusal.
