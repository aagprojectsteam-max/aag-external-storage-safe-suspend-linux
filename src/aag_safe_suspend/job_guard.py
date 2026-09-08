from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

POWER_PATTERNS = (
    "suspend",
    "sleep",
    "hibernate",
    "hybrid-sleep",
    "suspend-then-hibernate",
    "poweroff",
    "shutdown",
    "reboot",
    "halt",
    "kexec",
)
STORAGE_PATTERNS = (
    ".mount",
    ".automount",
    "udisks",
    "cryptsetup",
    "lvm",
    "dm-",
    "mdadm",
    "zfs",
    "btrfs",
    "timeshift",
    "backup",
    "snapshot",
)
PROJECT_PATTERNS = ("aag-external-storage-safe-suspend",)
ROUTINE_ALLOW = (
    re.compile(r"^(apt-daily|apt-daily-upgrade|apt-news|esm-cache|motd-news|man-db)\.service$"),
    re.compile(r"^(cups|cups-browsed|fwupd-refresh|sysstat-collect|logrotate)\.service$"),
    re.compile(r"^(upower|rtkit-daemon|systemd-tmpfiles-clean)\.service$"),
)


def classify(job: dict[str, str]) -> tuple[str, bool, str]:
    unit = job.get("unit", "").lower()
    operation = job.get("type", "").lower()
    if not unit or operation not in {"start", "stop", "restart", "reload", "try-restart"}:
        return "UNKNOWN", True, "malformed-or-unknown-operation"
    if any(pattern in unit for pattern in POWER_PATTERNS):
        return "POWER_TRANSACTION", True, "can race a power-state transaction"
    if any(pattern in unit for pattern in PROJECT_PATTERNS):
        return "PROJECT_TRANSACTION", True, "can invalidate installer-owned state"
    if any(pattern in unit for pattern in STORAGE_PATTERNS):
        return "STORAGE_TRANSACTION", True, "can change target storage ownership or mounts"
    if unit.startswith(
        ("systemd-suspend", "systemd-hibernate", "systemd-poweroff", "systemd-reboot")
    ):
        return "POWER_TRANSACTION", True, "system power transaction"
    if any(pattern.fullmatch(unit) for pattern in ROUTINE_ALLOW):
        return "ROUTINE_UNRELATED", False, "known bounded routine unit"
    if unit.endswith((".scope", ".slice", ".device", ".socket", ".path", ".timer")):
        return "OTHER_RELEVANT", True, "unexpected non-service job during mutation"
    return "UNKNOWN", True, "unreviewed service fails closed"


def parse_jobs(text: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) < 4 or not fields[0].isdigit():
            result.append({"id": "", "unit": "", "type": "", "state": "", "raw": line})
            continue
        result.append(
            {
                "id": fields[0],
                "unit": fields[1],
                "type": fields[2],
                "state": fields[3],
            }
        )
    return result


def snapshot() -> list[dict[str, Any]]:
    result = subprocess.run(
        ["/usr/bin/systemctl", "list-jobs", "--no-legend", "--no-pager"],
        text=True,
        capture_output=True,
        check=True,
        timeout=5,
        env={**os.environ, "LC_ALL": "C"},
    )
    rows: list[dict[str, Any]] = []
    for job in parse_jobs(result.stdout):
        category, conflict, reason = classify(job)
        rows.append({**job, "classification": category, "conflicting": conflict, "reason": reason})
    return rows


def assert_clear(stage: str) -> list[dict[str, Any]]:
    rows = snapshot()
    conflicts = [row for row in rows if row["conflicting"]]
    if conflicts:
        raise RuntimeError(
            f"conflicting systemd job at {stage}: {json.dumps(conflicts, sort_keys=True)}"
        )
    return rows


def watch(output: Path, parent: int, stop_file: Path | None = None, interval: float = 0.02) -> int:
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    (output / "READY.json").write_text(json.dumps({"time": time.time(), "parent": parent}) + "\n")
    while Path(f"/proc/{parent}").exists() and not (stop_file and stop_file.exists()):
        try:
            rows = snapshot()
        except Exception as exc:
            (output / "VIOLATION.json").write_text(
                json.dumps(
                    {"time": time.time(), "reason": f"job-enumeration-failed:{exc}"}, sort_keys=True
                )
                + "\n"
            )
            return 1
        now = time.time()
        for row in rows:
            key = (row.get("id", ""), row.get("unit", ""), row.get("type", ""))
            prior = seen.get(key)
            if prior:
                prior["last_seen"] = now
                prior["samples"] += 1
            else:
                seen[key] = {**row, "first_seen": now, "last_seen": now, "samples": 1}
            if row["conflicting"]:
                (output / "VIOLATION.json").write_text(
                    json.dumps(seen[key], sort_keys=True, indent=2) + "\n"
                )
        time.sleep(interval)
    (output / "OBSERVED.json").write_text(
        json.dumps(
            sorted(seen.values(), key=lambda row: (row["first_seen"], row["id"])),
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    (output / "STOPPED.json").write_text(json.dumps({"time": time.time()}) + "\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    watcher = sub.add_parser("watch")
    watcher.add_argument("--output", type=Path, required=True)
    watcher.add_argument("--parent", type=int, required=True)
    watcher.add_argument("--stop-file", type=Path)
    args = parser.parse_args()
    if args.command == "snapshot":
        rows = snapshot()
        print(json.dumps(rows, sort_keys=True, indent=2))
        return 1 if any(row["conflicting"] for row in rows) else 0
    return watch(args.output, args.parent, args.stop_file)


if __name__ == "__main__":
    raise SystemExit(main())
