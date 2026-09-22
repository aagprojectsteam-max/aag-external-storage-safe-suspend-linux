"""Best-effort resume notifications and structured local logs.

This module must never become a power-state owner.  All logging and desktop
notifications are advisory: failures here are swallowed so they cannot turn a
healthy Suspend/Hibernate restoration into a failed power transaction.
"""
from __future__ import annotations

import datetime as dt
import grp
import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path

LOG_DIR = Path("/var/log/aag-power-resume")
KEEP_LOGS = 128


def _safe_episode(value) -> str:
    text = str(value or "unknown")
    if re.fullmatch(r"[A-Za-z0-9_.-]+", text):
        return text
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


def _group_id() -> int:
    try:
        return grp.getgrnam("adm").gr_gid
    except KeyError:
        return 0


def ensure_log_dir() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o750)
    if LOG_DIR.is_symlink() or not stat.S_ISDIR(LOG_DIR.stat().st_mode):
        raise OSError("unsafe resume log directory")
    os.chmod(LOG_DIR, 0o750)
    if os.geteuid() == 0:
        os.chown(LOG_DIR, 0, _group_id())
    return LOG_DIR


def log_path(kind: str, episode) -> Path:
    return LOG_DIR / f"{kind}-{_safe_episode(episode)}.jsonl"


def _append(kind: str, episode, event: str, **detail):
    try:
        ensure_log_dir()
        path = log_path(kind, episode)
        row = {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "epoch": time.time(),
            "monotonic": time.monotonic(),
            "type": kind,
            "episode": str(episode or ""),
            "event": event,
            **detail,
        }
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o640)
        try:
            os.write(fd, (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode())
            os.fsync(fd)
            os.fchmod(fd, 0o640)
            if os.geteuid() == 0:
                os.fchown(fd, 0, _group_id())
        finally:
            os.close(fd)
        return path
    except Exception:
        return None


def _notify(config: dict, title: str, body: str, urgency: str = "normal") -> bool:
    user = config.get("notification_user")
    uid = config.get("notification_uid")
    if not user or uid is None:
        return False
    bus = Path(f"/run/user/{uid}/bus")
    if not bus.exists():
        return False
    try:
        result = subprocess.run(
            [
                "/usr/sbin/runuser", "-u", str(user), "--",
                "/usr/bin/env",
                f"XDG_RUNTIME_DIR=/run/user/{uid}",
                f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
                "/usr/bin/notify-send",
                "-a", "AAG Power",
                "-u", urgency,
                title,
                body,
            ],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _label(kind: str) -> str:
    return "מצב תרדמה" if kind == "hibernate" else "מצב שינה"


def start(config: dict, kind: str, episode, **detail) -> dict:
    label = _label(kind)
    title = f"AAG — שחזור המערכת מ{label} החל"
    body = "AAG משחזר כעת את רכיבי המערכת והשירותים. מומלץ להמתין להודעת הסיום."
    sent = _notify(config, title, body)
    path = _append(kind, episode, "RESUME_STARTED", notification_sent=sent, **detail)
    return {"started_at": time.time(), "log_path": str(path) if path else None}


def stage(kind: str, episode, phase: str, status: str, **detail):
    return _append(kind, episode, "RESUME_STAGE", phase=phase, status=status, **detail)


def success(config: dict, kind: str, episode, *, started_at=None, **detail):
    duration = None
    try:
        if started_at is not None:
            duration = max(0.0, time.time() - float(started_at))
    except (TypeError, ValueError):
        duration = None
    label = _label(kind)
    title = f"AAG — השחזור מ{label} הושלם בהצלחה"
    body = "כל בדיקות השחזור הסתיימו בהצלחה. המערכת והשירותים חזרו לפעולה תקינה."
    if duration is not None:
        body += f" זמן השחזור: {duration:.1f} שניות."
    sent = _notify(config, title, body)
    path = _append(
        kind,
        episode,
        "RESUME_COMPLETE",
        result="PASS",
        duration_seconds=duration,
        notification_sent=sent,
        **detail,
    )
    prune()
    return path


def failure(config: dict, kind: str, episode, *, phase: str, reason: str, started_at=None, **detail):
    duration = None
    try:
        if started_at is not None:
            duration = max(0.0, time.time() - float(started_at))
    except (TypeError, ValueError):
        duration = None
    label = _label(kind)
    title = f"AAG — בעיה בשחזור מ{label}"
    body = (
        f"תהליך השחזור לא הושלם בשלב {phase}. "
        f"פרטי התקלה נשמרו ב־{LOG_DIR}."
    )
    sent = _notify(config, title, body, urgency="critical")
    path = _append(
        kind,
        episode,
        "RESUME_FAILED",
        result="FAIL",
        phase=phase,
        reason=str(reason)[:4000],
        duration_seconds=duration,
        notification_sent=sent,
        **detail,
    )
    prune()
    return path


def prune() -> None:
    try:
        ensure_log_dir()
        files = [
            p for p in LOG_DIR.glob("*.jsonl")
            if not p.is_symlink() and p.is_file()
        ]
        files.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for path in files[KEEP_LOGS:]:
            path.unlink()
    except Exception:
        return
