# Updater threat model

## Trust boundary

The root installer trusts only a locally supplied release payload after its
embedded gzip SHA256 and every extracted file hash pass. Users must also verify
the whole `.run` against `SHA256SUMS` obtained from the same authenticated GitHub
release. v1.x deliberately omits automatic download-and-execute: a checksum can
be substituted alongside a malicious asset if the release account is fully
compromised, and no independent signing root is deployed yet.

Downloaded files, command arguments, the environment, installed-state JSON,
rollback metadata, payload caches, existing filesystem objects, and symlinks
are treated as untrusted. The installer uses a fixed `/var/tmp` `mktemp`
directory, an embedded payload boundary, deterministic archives containing no
symlinks, normalized absolute managed paths, root-only state, `lstat` checks,
same-directory atomic replacements, `fsync`, explicit modes/ownership, and no
shell expansion of state-derived paths.

## Addressed attacks

- Compromised or truncated download: whole-asset external SHA256 plus embedded
  payload SHA256 and per-file payload hashes.
- Checksum substitution: clearly documented residual risk; no in-program
  downloader and no claim that same-origin checksums are an independent
  signature.
- Path traversal and symlink redirection: normalized absolute allowlisted
  destinations, rejection of `..`, NULs, symlink payloads, symlink targets, and
  symlinked destination-directory components.
- TOCTOU: root-only transaction directories, file-descriptor writes,
  same-directory atomic replacement, and continuous systemd job observation.
- Privilege confusion and unsafe temporary files: root requirement, fixed
  executable paths, `mktemp`, `0600`/`0700` state, and no eval or shell command
  assembled from JSON.
- Malicious local state and version spoofing: product/schema checks, path
  constraints, per-file hashes, known v1.0.0 static identities, semantic config
  validation, and snapshot/cache containment under the project state root.
- Rollback poisoning: root-only snapshot, target-version binding, compatibility
  flag, per-file snapshot hashes, and transactional rollback.
- Concurrent or interrupted mutation: project flock, active sleep/backup/job
  guard, pending states, signal-to-exception handling, automatic exact rollback,
  and durable failure evidence.

An attacker already able to replace arbitrary root-owned executables or mutate
kernel/systemd behavior is outside the local privilege boundary. The updater
still fails closed on detectable state or graph inconsistency.
