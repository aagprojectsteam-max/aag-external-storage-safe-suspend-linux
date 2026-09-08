from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any

GUEST_NAMES = {
    "qemu-system-x86_64",
    "qemu-kvm",
    "qemu-nbd",
    "nbdkit",
    "virtualbox",
    "virtualboxvm",
    "vmware-vmx",
    "winboat",
    "windows",
}


def parse_proc_stat(raw: str) -> dict[str, Any]:
    right = raw.rfind(")")
    left = raw.find("(")
    if left < 1 or right <= left:
        raise ValueError("malformed proc stat")
    pid = int(raw[:left].strip())
    fields = raw[right + 2 :].split()
    if len(fields) < 20:
        raise ValueError("short proc stat")
    return {
        "pid": pid,
        "comm": raw[left + 1 : right],
        "state": fields[0],
        "ppid": int(fields[1]),
        "utime": int(fields[11]),
        "stime": int(fields[12]),
        "start": fields[19],
    }


def proc_stat(proc: Path, pid: int) -> dict[str, Any] | None:
    try:
        return parse_proc_stat((proc / str(pid) / "stat").read_text())
    except (OSError, ValueError):
        return None


def decode_mount(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def parse_mountinfo(text: str, namespace: str, owner: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 10 or "-" not in fields:
            continue
        separator = fields.index("-")
        if separator + 3 >= len(fields):
            continue
        optionals = fields[6:separator]
        propagation = {
            key: value
            for token in optionals
            if ":" in token
            for key, value in [token.split(":", 1)]
            if key in {"shared", "master", "propagate_from"}
        }
        rows.append(
            {
                "kind": "mount",
                "owner": owner,
                "namespace": namespace,
                "mount_id": fields[0],
                "parent_id": fields[1],
                "dev": fields[2],
                "root": decode_mount(fields[3]),
                "target": decode_mount(fields[4]),
                "options": fields[5],
                "propagation": propagation,
                "fstype": fields[separator + 1],
                "source": decode_mount(fields[separator + 2]),
            }
        )
    return rows


def maps_dev(value: str) -> str | None:
    try:
        major, minor = value.split(":", 1)
        return f"{int(major, 16)}:{int(minor, 16)}"
    except (TypeError, ValueError):
        return None


def _devnum(value: str) -> int:
    major, minor = value.split(":", 1)
    return os.makedev(int(major), int(minor))


def _owner(executable: str | None, other_namespace: bool, raw: bool = False) -> str:
    name = Path(executable or "").name.lower()
    if name in GUEST_NAMES:
        return "GUEST"
    if raw:
        return "RAW"
    return "CONTAINER" if other_namespace else "HOST"


def _record_key(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def canonical_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {_record_key(record): record for record in records}
    return [unique[key] for key in sorted(unique)]


def scan_once(
    generation: dict[str, Any],
    proc: Path = Path("/proc"),
    sys: Path = Path("/sys"),
) -> dict[str, Any]:
    token = generation.get("token") or {}
    devnos = {token.get("disk_devno")} | {
        part.get("devno") for part in token.get("partitions") or []
    }
    devnos.discard(None)
    target_devs = {_devnum(value) for value in devnos}
    records: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    enumerated = 0
    namespace_seen: set[str] = set()
    try:
        host_namespace = os.readlink(proc / "1/ns/mnt")
        pids = sorted(int(path.name) for path in proc.iterdir() if path.name.isdigit())
    except OSError as exc:
        return {
            "AUDIT_COMPLETE": False,
            "EXTERNAL_OWNER": "UNKNOWN",
            "records": [],
            "gaps": [{"kind": "root-enumeration", "errno": exc.errno}],
            "observations": [],
            "enumerated": 0,
        }

    for pid in pids:
        before = proc_stat(proc, pid)
        if not before:
            continue
        pdir = proc / str(pid)
        try:
            namespace = os.readlink(pdir / "ns/mnt")
        except OSError as exc:
            after = proc_stat(proc, pid)
            if after and after["start"] == before["start"] and after["state"] != "Z":
                gaps.append({"kind": "namespace", "pid": pid, "errno": exc.errno})
            else:
                observations.append({"kind": "pid-churn-or-zombie", "pid": pid})
            continue
        other_namespace = namespace != host_namespace
        executable: str | None
        try:
            executable = os.readlink(pdir / "exe")
        except OSError:
            executable = None

        if namespace not in namespace_seen:
            try:
                mounts = parse_mountinfo(
                    (pdir / "mountinfo").read_text(errors="replace"),
                    namespace,
                    "CONTAINER" if other_namespace else "HOST",
                )
                records.extend(row for row in mounts if row["dev"] in devnos)
                namespace_seen.add(namespace)
            except OSError as exc:
                after = proc_stat(proc, pid)
                if after and after["start"] == before["start"] and after["state"] != "Z":
                    gaps.append({"kind": "mountinfo", "pid": pid, "errno": exc.errno})

        enumerated += 1
        for kind in ("cwd", "root", "exe"):
            try:
                value = (pdir / kind).stat()
            except FileNotFoundError:
                continue
            except PermissionError as exc:
                if before["state"] != "Z":
                    gaps.append({"kind": kind, "pid": pid, "errno": exc.errno})
                continue
            if value.st_dev in target_devs:
                records.append(
                    {
                        "kind": kind,
                        "owner": _owner(executable, other_namespace),
                        "pid": pid,
                        "start": before["start"],
                        "dev": f"{os.major(value.st_dev)}:{os.minor(value.st_dev)}",
                    }
                )
            elif stat.S_ISBLK(value.st_mode) and value.st_rdev in target_devs:
                records.append(
                    {
                        "kind": "raw-" + kind,
                        "owner": _owner(executable, other_namespace, raw=True),
                        "pid": pid,
                        "start": before["start"],
                        "dev": f"{os.major(value.st_rdev)}:{os.minor(value.st_rdev)}",
                    }
                )

        try:
            descriptors = list((pdir / "fd").iterdir())
        except (FileNotFoundError, ProcessLookupError):
            descriptors = []
        except PermissionError as exc:
            if before["state"] != "Z":
                gaps.append({"kind": "fd-directory", "pid": pid, "errno": exc.errno})
            descriptors = []
        for descriptor in descriptors:
            try:
                value = descriptor.stat()
            except (FileNotFoundError, ProcessLookupError):
                continue
            except PermissionError as exc:
                if before["state"] != "Z":
                    gaps.append({"kind": "fd", "pid": pid, "errno": exc.errno})
                continue
            if value.st_dev in target_devs:
                records.append(
                    {
                        "kind": "fd",
                        "owner": _owner(executable, other_namespace),
                        "pid": pid,
                        "start": before["start"],
                        "dev": f"{os.major(value.st_dev)}:{os.minor(value.st_dev)}",
                    }
                )
            elif stat.S_ISBLK(value.st_mode) and value.st_rdev in target_devs:
                records.append(
                    {
                        "kind": "raw-fd",
                        "owner": _owner(executable, other_namespace, raw=True),
                        "pid": pid,
                        "start": before["start"],
                        "dev": f"{os.major(value.st_rdev)}:{os.minor(value.st_rdev)}",
                    }
                )

        try:
            maps = (pdir / "maps").read_text(errors="replace")
        except OSError as exc:
            maps = ""
            if before["state"] != "Z" and exc.errno not in {2, 3}:
                gaps.append({"kind": "maps", "pid": pid, "errno": exc.errno})
        for line in maps.splitlines():
            fields = line.split(maxsplit=5)
            normalized = maps_dev(fields[3]) if len(fields) >= 5 else None
            if normalized in devnos:
                records.append(
                    {
                        "kind": "mmap",
                        "owner": _owner(executable, other_namespace),
                        "pid": pid,
                        "start": before["start"],
                        "dev": normalized,
                    }
                )

        after = proc_stat(proc, pid)
        if after and after["start"] != before["start"]:
            observations.append({"kind": "pid-identity-changed", "pid": pid})

    records.extend(_block_stack_records(devnos, target_devs, proc, sys, gaps))
    records = canonical_records(records)
    non_mount = [record for record in records if record["kind"] != "mount"]
    if gaps or enumerated == 0:
        owner = "UNKNOWN"
    elif non_mount:
        owners = sorted({record["owner"] for record in non_mount})
        owner = owners[0] if len(owners) == 1 else "MULTIPLE"
    elif records:
        owner = "MOUNT_ONLY"
    else:
        owner = "NONE"
    return {
        "AUDIT_COMPLETE": not gaps and enumerated > 0,
        "EXTERNAL_OWNER": owner,
        "records": records,
        "gaps": sorted(gaps, key=_record_key),
        "observations": sorted(observations, key=_record_key),
        "enumerated": enumerated,
    }


def _block_stack_records(
    devnos: set[str],
    target_devs: set[int],
    proc: Path,
    sys: Path,
    gaps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    names: set[str] = set()
    for devno in devnos:
        try:
            node = (sys / "dev/block" / devno).resolve(strict=True)
            names.add(node.name)
            for relation in ("holders", "slaves"):
                for link in (node / relation).iterdir():
                    records.append(
                        {
                            "kind": relation,
                            "owner": "RAW",
                            "dev": devno,
                            "related": link.name,
                        }
                    )
        except FileNotFoundError:
            gaps.append({"kind": "sysfs-device-missing", "dev": devno})
        except PermissionError as exc:
            gaps.append({"kind": "sysfs-device-permission", "dev": devno, "errno": exc.errno})
    try:
        blocks = list((sys / "class/block").iterdir())
    except OSError as exc:
        gaps.append({"kind": "sysfs-block-enumeration", "errno": exc.errno})
        return records
    for block in blocks:
        try:
            slaves = {path.name for path in (block / "slaves").iterdir()}
        except (FileNotFoundError, PermissionError):
            slaves = set()
        if slaves & names and block.name not in names:
            prefix = (
                "dm"
                if block.name.startswith("dm-")
                else "nbd"
                if block.name.startswith("nbd")
                else "block-stack"
            )
            records.append(
                {"kind": prefix, "owner": "RAW", "dev": sorted(devnos)[0], "related": block.name}
            )
        backing = block / "loop/backing_file"
        if block.name.startswith("loop") and backing.exists():
            try:
                path = backing.read_text().strip()
                if path:
                    value = (proc / "1/root" / path.lstrip("/")).stat()
                    if value.st_dev in target_devs:
                        records.append(
                            {
                                "kind": "loop-backing",
                                "owner": "RAW",
                                "dev": f"{os.major(value.st_dev)}:{os.minor(value.st_dev)}",
                                "related": block.name,
                            }
                        )
            except (FileNotFoundError, PermissionError) as exc:
                gaps.append({"kind": "loop-backing", "loop": block.name, "errno": exc.errno})
    return records


def semantic_signature(scan: dict[str, Any]) -> str:
    stable = {
        "complete": scan.get("AUDIT_COMPLETE"),
        "owner": scan.get("EXTERNAL_OWNER"),
        "records": [
            {key: value for key, value in record.items() if key not in {"pid", "start"}}
            for record in scan.get("records") or []
        ],
        "gaps": scan.get("gaps") or [],
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


def delta(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    first_records = {_record_key(row): row for row in first.get("records") or []}
    second_records = {_record_key(row): row for row in second.get("records") or []}
    classes: set[str] = set()
    if first.get("gaps") != second.get("gaps"):
        classes.add("PERMISSION_OR_OBSERVABILITY_CHANGED")
    if first.get("EXTERNAL_OWNER") != second.get("EXTERNAL_OWNER"):
        classes.add("OWNERSHIP_CHANGED")
    added = [second_records[key] for key in second_records.keys() - first_records.keys()]
    removed = [first_records[key] for key in first_records.keys() - second_records.keys()]
    if any(row.get("kind") == "mount" for row in added + removed):
        classes.add("MOUNT_CHANGED")
    if any(row.get("kind") != "mount" for row in added + removed):
        classes.add("REFERENCE_CHANGED")
    first_pids = {
        row.get("pid") for row in first.get("records") or [] if row.get("pid") is not None
    }
    second_pids = {
        row.get("pid") for row in second.get("records") or [] if row.get("pid") is not None
    }
    if second_pids - first_pids:
        classes.add("PID_STARTED")
    if first_pids - second_pids:
        classes.add("PID_EXITED")
    return {
        "classes": sorted(classes),
        "meaning_changed": semantic_signature(first) != semantic_signature(second),
        "added": added,
        "removed": removed,
    }


def audit_three(generation: dict[str, Any], pause: float = 0.1) -> dict[str, Any]:
    scans = [scan_once(generation)]
    time.sleep(pause)
    scans.append(scan_once(generation))
    time.sleep(pause)
    scans.append(scan_once(generation))
    deltas = [delta(scans[0], scans[1]), delta(scans[1], scans[2])]
    result = dict(scans[-1])
    result.update(
        {
            "scans": scans,
            "deltas": deltas,
            "semantic_stable_scan_2_to_3": not deltas[1]["meaning_changed"],
        }
    )
    return result


def unsafe_mount_topology(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mounts = [row for row in records if row.get("kind") == "mount"]
    host_shared = {
        str(row.get("propagation", {}).get("shared"))
        for row in mounts
        if row.get("owner") == "HOST" and row.get("propagation", {}).get("shared")
    }
    unsafe: list[dict[str, Any]] = []
    for row in mounts:
        if row.get("owner") == "HOST":
            continue
        propagation = row.get("propagation") or {}
        ancestors = {str(propagation.get("master")), str(propagation.get("propagate_from"))}
        ancestors.discard("None")
        if not ancestors & host_shared:
            unsafe.append(row)
    return unsafe


def disposition(audit: dict[str, Any], allow_mounts: bool) -> tuple[str, str]:
    if not audit.get("AUDIT_COMPLETE"):
        return "FAIL", "audit-incomplete"
    if not audit.get("semantic_stable_scan_2_to_3"):
        return "RETRY", "audit-not-semantically-stable"
    non_mount = [row for row in audit.get("records") or [] if row.get("kind") != "mount"]
    if non_mount:
        return "FAIL", "genuine-or-unknown-owner"
    if unsafe_mount_topology(audit.get("records") or []):
        return "FAIL", "independent-mount-namespace"
    mounts = [row for row in audit.get("records") or [] if row.get("kind") == "mount"]
    if mounts and allow_mounts:
        return "UNMOUNT", "stable-host-mounts-and-propagated-mirrors-only"
    if mounts:
        return "RETRY", "target-still-mounted"
    return "PASS", "no-owner-and-unmounted"
