#!/usr/bin/python3
"""Scan reachable Git history for private text and unexpectedly large blobs."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BLOB = 2 * 1024 * 1024
HISTORY_PATTERNS = {
    "private-user": re.compile(r"\baag[-]linux\b", re.IGNORECASE),
    "private-home": re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+(?:/|\b)"),
    "canonical-uuid": re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
        r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
    ),
    "private-key": re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    "openai-token": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}


def git(*argv: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", *argv],
        cwd=ROOT,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout


def reachable_blobs() -> list[tuple[str, int, str]]:
    objects = git("rev-list", "--objects", "--all").splitlines()
    identities = [line.split(" ", 1)[0] for line in objects]
    paths = {line.split(" ", 1)[0]: line.split(" ", 1)[1] for line in objects if " " in line}
    if not identities:
        return []
    details = git(
        "cat-file",
        "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        input_text="\n".join(identities) + "\n",
    )
    return [
        (object_id, int(size), paths.get(object_id, "<unresolved>"))
        for object_id, kind, size in (line.split() for line in details.splitlines())
        if kind == "blob"
    ]


def history_findings() -> list[str]:
    findings: list[str] = []
    commits = git("rev-list", "--all").splitlines()
    for commit in commits:
        listing = git(
            "grep", "-I", "-n", "-e", ".", commit, "--", ".", ":(exclude)scripts/repository_scan.py"
        )
        for line in listing.splitlines():
            for name, pattern in HISTORY_PATTERNS.items():
                if pattern.search(line):
                    findings.append(f"{name}:{line.split(':', 1)[0]}")
    return sorted(set(findings))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    large = [(path, size) for _oid, size, path in reachable_blobs() if size > MAX_BLOB]
    history = history_findings() if args.history else []
    if large or history:
        print("REPOSITORY_SCAN=FAIL")
        for path, size in large:
            print(f"large-blob:{path}:{size}")
        for finding in history:
            print(finding)
        return 1
    print("LARGE_BLOB_SCAN=PASS")
    print("GIT_HISTORY_SCAN=PASS" if args.history else "GIT_HISTORY_SCAN=NOT_REQUESTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
