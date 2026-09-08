from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, coordinator, identity
from . import config as configuration
from .job_guard import main as job_guard_main


def main() -> int:
    parser = argparse.ArgumentParser(prog="aag-safe-suspend")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("begin")
    commands.add_parser("prepare")
    commands.add_parser("teardown")
    commands.add_parser("resume")
    commands.add_parser("recover")
    commands.add_parser("validate")
    commands.add_parser("status")
    render = commands.add_parser("render-udev")
    render.add_argument("--config", type=Path, required=True)
    probe = commands.add_parser("probe")
    probe.add_argument("--device", required=True)
    probe.add_argument("--protect-mount", action="append", default=["/"])
    backup = commands.add_parser("backup-run")
    backup.add_argument("kind", choices=["timeshift", "timeshift-gtk"])
    backup.add_argument("argv", nargs=argparse.REMAINDER)
    commands.add_parser("job-guard")
    args, remainder = parser.parse_known_args()

    actions = {
        "begin": coordinator.begin,
        "prepare": coordinator.prepare,
        "teardown": coordinator.teardown,
        "resume": coordinator.resume,
        "recover": coordinator.recover,
    }
    if args.command in actions:
        return actions[args.command]()
    if args.command == "validate":
        print(json.dumps(coordinator.validate_installation(), sort_keys=True, indent=2))
        return 0
    if args.command == "status":
        print(json.dumps(coordinator.status(), sort_keys=True, indent=2))
        return 0
    if args.command == "render-udev":
        value = configuration.load(args.config)
        print(identity.udev_rule(value, coordinator.MARKER_PATH), end="")
        return 0
    if args.command == "probe":
        mounts = list(dict.fromkeys(args.protect_mount))
        print(configuration.dump(identity.discover(args.device, mounts)), end="")
        return 0
    if args.command == "backup-run":
        argv = args.argv[1:] if args.argv and args.argv[0] == "--" else args.argv
        return coordinator.backup_run(args.kind, argv)
    os.environ["AAG_JOB_GUARD_EMBEDDED"] = "1"
    sys.argv = ["aag-systemd-job-guard", *remainder]
    return job_guard_main()


if __name__ == "__main__":
    raise SystemExit(main())
