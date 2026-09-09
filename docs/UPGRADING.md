# In-place upgrades

## Normal update path

Download the target release's `.run`, `SHA256SUMS`, and
`release-manifest.json` from the same GitHub release. Verify the installer, then
run it directly. An existing supported installation is detected automatically;
`install` is optional for this path.

```bash
sha256sum --ignore-missing -c SHA256SUMS
chmod +x aag-external-storage-safe-suspend-linux-v1.2.0.run
sudo ./aag-external-storage-safe-suspend-linux-v1.2.0.run
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

## v1.1.1 to v1.2.0

The direct in-place path is supported and tested. It verifies the v1.1.1
installed-state manifest, creates an exact rollback snapshot, migrates config
schema 1 to 2 while preserving the selected device and existing supported
settings, installs Hibernate-only wiring, reloads systemd and udev metadata,
runs health checks, and commits v1.2.0 last. Failure before commit restores the
exact v1.1.1 tree and configuration.

Hibernate defaults to disabled during an ordinary migration. On the documented
reference platform, opt in during the same transaction with:

```bash
sudo ./aag-external-storage-safe-suspend-linux-v1.2.0.run install \
  --enable-reference-hibernate
sudo aag-safe-suspend health-check
```

This does not initiate Hibernate. Do not enable the option on an unqualified
platform merely because the kernel exposes `disk` in `/sys/power/state`.

The accepted reference machine may also contain the final pre-release
qualification helpers beside its public v1.1.1 installation. v1.2.0 adopts
only the complete known final file set at exact published hashes, snapshots it
for rollback, removes the obsolete duplicate wiring, and installs the managed
public equivalents. Exact adoption also preserves that machine's already
accepted plain-Hibernate and T700 policy as enabled. A partial or locally
changed qualification set fails before mutation. Private qualification state
and evidence directories are never read, copied, or removed.

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
byte unless an explicitly declared schema migration is required. The v1.2.0
schema-1-to-2 migration preserves existing supported values and adds disabled
Hibernate defaults; exact prior bytes remain in the rollback snapshot. The
target-only udev rule is regenerated from the validated configuration. Project
code, units, wrappers, and the generated rule are
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
Running an older installer is a downgrade, never an upgrade, and v1.x refuses
it with `DOWNGRADE_REFUSED_WITH_REASON`. A future release may declare a narrow
supported downgrade only when config, state, wiring, and rollback migrations
are explicitly reversible.

## First upgrade from public v1.0.0

v1.2.0 retains the bootstrap identities of the public v1.0.0 tag. It requires
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
