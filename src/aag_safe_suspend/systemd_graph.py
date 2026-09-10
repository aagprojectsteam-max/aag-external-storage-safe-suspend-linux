"""Canonical semantic checks for the power-management systemd graph."""

from __future__ import annotations

import copy
import re
from typing import Any

SET_PROPERTIES = frozenset(
    {
        "Requires",
        "Requisite",
        "Wants",
        "BindsTo",
        "PartOf",
        "Upholds",
        "Conflicts",
        "Before",
        "After",
        "OnFailure",
        "OnSuccess",
        "PropagatesReloadTo",
        "ReloadPropagatedFrom",
        "JoinsNamespaceOf",
        "TriggeredBy",
        "Triggers",
        "ConsistsOf",
    }
)
ORDERED_PROPERTIES = frozenset({"DropInPaths"})
SCALAR_PROPERTIES = frozenset(
    {
        "FragmentPath",
        "SourcePath",
        "UnitFileState",
        "StopWhenUnneeded",
        "RefuseManualStart",
        "RefuseManualStop",
        "AllowIsolate",
        "DefaultDependencies",
        "OnFailureJobMode",
        "JobTimeoutUSec",
        "JobRunningTimeoutUSec",
        "JobTimeoutAction",
        "JobTimeoutRebootArgument",
        "Type",
        "TimeoutStartUSec",
        "TimeoutStopUSec",
        "TimeoutAbortUSec",
        "RuntimeMaxUSec",
        "RemainAfterExit",
        "GuessMainPID",
        "OOMPolicy",
        "KillMode",
    }
)
COMMAND_PROPERTIES = frozenset(
    {
        "ExecCondition",
        "ExecStartPre",
        "ExecStart",
        "ExecStartPost",
        "ExecReload",
        "ExecStop",
        "ExecStopPost",
    }
)
CAPTURE_PROPERTIES = tuple(
    sorted(SET_PROPERTIES | ORDERED_PROPERTIES | SCALAR_PROPERTIES | COMMAND_PROPERTIES)
)

OLD_PREPARE = "aag-external-storage-safe-suspend.service"
ORDINARY_PREPARE = "aag-external-storage-safe-ordinary-suspend.service"
RESUME = "aag-external-storage-safe-suspend-resume.service"
FAILURE = "aag-external-storage-safe-suspend-failure.service"
ORDINARY_DROPIN = (
    "/etc/systemd/system/systemd-suspend.service.d/70-aag-external-storage-safe-suspend.conf"
)


class GraphError(RuntimeError):
    pass


def parse_show(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or key in parsed:
            raise GraphError("malformed or duplicate systemctl show property")
        parsed[key] = value
    if not parsed:
        raise GraphError("empty systemctl show snapshot")
    return parsed


def parse_commands(value: str) -> list[dict[str, str]]:
    if not value:
        return []
    groups = re.findall(r"\{ (.*?) \}(?= \{|$)", value)
    if not groups or " ".join(f"{{ {group} }}" for group in groups) != value:
        raise GraphError("unrecognized systemd Exec* serialization")
    commands: list[dict[str, str]] = []
    for group in groups:
        fields: dict[str, str] = {}
        for item in group.split(" ; "):
            key, separator, field_value = item.partition("=")
            if not separator or key in fields:
                raise GraphError("malformed systemd Exec* command")
            fields[key] = field_value
        required = {"path", "argv[]", "ignore_errors"}
        if not required <= fields.keys():
            raise GraphError("systemd Exec* command lacks identity fields")
        unknown = (
            set(fields)
            - required
            - {
                "start_time",
                "stop_time",
                "pid",
                "code",
                "status",
            }
        )
        if unknown:
            raise GraphError(f"unknown systemd Exec* fields: {sorted(unknown)}")
        commands.append(
            {
                "path": fields["path"],
                "argv": fields["argv[]"],
                "ignore_errors": fields["ignore_errors"],
            }
        )
    return commands


def canonical(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in parse_show(text).items():
        if key in SET_PROPERTIES:
            members = value.split()
            if len(members) != len(set(members)):
                raise GraphError(f"duplicate member in {key}")
            result[key] = sorted(members)
        elif key in ORDERED_PROPERTIES:
            members = value.split()
            if len(members) != len(set(members)):
                raise GraphError(f"duplicate member in {key}")
            result[key] = members
        elif key in COMMAND_PROPERTIES:
            result[key] = parse_commands(value)
        else:
            result[key] = value
    return result


def delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    changed: dict[str, dict[str, Any]] = {}
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        if key in SET_PROPERTIES and isinstance(old, list) and isinstance(new, list):
            plus = sorted(set(new) - set(old))
            minus = sorted(set(old) - set(new))
            if plus:
                added[key] = plus
            if minus:
                removed[key] = minus
        else:
            changed[key] = {"before": old, "after": new}
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "semantic_equal": not (added or removed or changed),
    }


def require_equal(before: str, after: str, label: str) -> None:
    result = delta(canonical(before), canonical(after))
    if not result["semantic_equal"]:
        raise GraphError(f"{label} semantic graph changed: {result}")


def _replace_member(graph: dict[str, Any], field: str, old: str, new: str) -> None:
    members = set(graph.get(field, []))
    members.discard(old)
    members.add(new)
    graph[field] = sorted(members)


def _add_member(graph: dict[str, Any], field: str, member: str) -> None:
    graph[field] = sorted({*graph.get(field, []), member})


def require_ordinary_install(before: str, after: str, *, prior_release: bool) -> None:
    old = canonical(before)
    new = canonical(after)
    expected = copy.deepcopy(old)
    if prior_release:
        _replace_member(expected, "Requires", OLD_PREPARE, ORDINARY_PREPARE)
        _replace_member(expected, "After", OLD_PREPARE, ORDINARY_PREPARE)
    else:
        _add_member(expected, "Requires", ORDINARY_PREPARE)
        _add_member(expected, "After", ORDINARY_PREPARE)
        _add_member(expected, "Wants", RESUME)
        _add_member(expected, "Before", RESUME)
        _add_member(expected, "OnFailure", FAILURE)

    old_dropins = list(old.get("DropInPaths", []))
    new_dropins = list(new.get("DropInPaths", []))
    if prior_release:
        if new_dropins != old_dropins or ORDINARY_DROPIN not in new_dropins:
            raise GraphError("ordinary project drop-in set/order changed during upgrade")
        expected["DropInPaths"] = old_dropins
    else:
        if new_dropins.count(ORDINARY_DROPIN) != 1:
            raise GraphError("ordinary project drop-in was not added exactly once")
        if [item for item in new_dropins if item != ORDINARY_DROPIN] != old_dropins:
            raise GraphError("unrelated ordinary drop-ins changed during installation")
        expected["DropInPaths"] = new_dropins

    result = delta(expected, new)
    if not result["semantic_equal"]:
        raise GraphError(f"ordinary graph differs from intended transformation: {result}")
    require_ordinary_contract(new)


def require_ordinary_contract(graph: dict[str, Any]) -> None:
    required = {
        "Requires": {ORDINARY_PREPARE},
        "After": {ORDINARY_PREPARE},
        "Wants": {RESUME},
        "Before": {RESUME},
        "OnFailure": {FAILURE},
    }
    for field, members in required.items():
        if not members <= set(graph.get(field, [])):
            raise GraphError(f"ordinary graph lacks required {field} edge")
    if OLD_PREPARE in graph.get("Requires", []) or OLD_PREPARE in graph.get("After", []):
        raise GraphError("ordinary graph still bypasses the USBClone-first preparation unit")
    if ORDINARY_DROPIN not in graph.get("DropInPaths", []):
        raise GraphError("ordinary project drop-in is absent")


def require_hibernate_isolation(graph: dict[str, Any]) -> None:
    for field in SET_PROPERTIES:
        if ORDINARY_PREPARE in graph.get(field, []):
            raise GraphError(f"ordinary USBClone unit leaked into Hibernate {field}")
    if ORDINARY_DROPIN in graph.get("DropInPaths", []):
        raise GraphError("ordinary drop-in leaked into Hibernate")
