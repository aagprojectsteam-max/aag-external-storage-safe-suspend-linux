#!/usr/bin/python3
"""Conservative text, identifier, and entropy scan for publication content."""

from __future__ import annotations

import argparse
import math
import re
from collections import Counter
from pathlib import Path

EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "release-work",
}
TEXT_SUFFIXES = {
    "",
    ".conf",
    ".example",
    ".in",
    ".json",
    ".md",
    ".py",
    ".service",
    ".sh",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
}
PATTERNS = {
    "private-home-path": re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+(?:/|\b)"),
    "private-username": re.compile(r"\baag[-]linux\b", re.IGNORECASE),
    "canonical-uuid": re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
        r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
    ),
    "email-address": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "ipv4-address": re.compile(
        r"(?<![0-9])(?:25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})"
        r"(?:\.(?:25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})){3}(?![0-9])"
    ),
    "mac-address": re.compile(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b"),
    "private-key": re.compile("BEGIN [A-Z ]*" + "PRIVATE KEY"),
    "github-token": re.compile(
        r"\bgh[pousr]_[A-Za-z0-9]{20,}\b|\bgithub[_]pat_[A-Za-z0-9_]{20,}\b"
    ),
    "openai-token": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "slack-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}\b"),
    "credential-assignment": re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|password|passwd|secret)\s*[:=]\s*['\"]?[^\s'\"<]{8,}"
    ),
}
TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_+/=-]{32,}(?![A-Za-z0-9])")


def entropy(value: str) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def candidate_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            raise RuntimeError(f"symlink is not allowed in public tree: {relative}")
        if path.is_file():
            files.append(path)
    return sorted(files)


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    for path in candidate_files(root):
        relative = path.relative_to(root)
        if path.stat().st_size > 2 * 1024 * 1024:
            findings.append(f"large-unreviewed-file:{relative}:{path.stat().st_size}")
            continue
        raw = path.read_bytes()
        if b"\0" in raw:
            findings.append(f"binary-file:{relative}")
            continue
        if path.suffix not in TEXT_SUFFIXES and path.name not in {"Makefile", "LICENSE"}:
            findings.append(f"unexpected-file-type:{relative}")
        text = raw.decode("utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), 1):
            for name, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append(f"{name}:{relative}:{line_number}")
            if relative == Path("scripts/privacy_scan.py"):
                continue
            if "sha256" in line.lower() or re.fullmatch(r"[0-9a-f]{64}\s+\S+", line):
                continue
            for match in TOKEN.finditer(line):
                value = match.group(0)
                if value.startswith(("http", "aag-external-storage-safe-suspend")):
                    continue
                if len(set(value)) >= 12 and entropy(value) >= 4.35:
                    findings.append(f"high-entropy-token:{relative}:{line_number}")
    return sorted(set(findings))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    root = args.root.resolve()
    findings = scan(root)
    if findings:
        print("PUBLICATION_SCAN=FAIL")
        for finding in findings:
            print(finding)
        return 1
    print(f"PUBLICATION_SCAN=PASS files={len(candidate_files(root))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
