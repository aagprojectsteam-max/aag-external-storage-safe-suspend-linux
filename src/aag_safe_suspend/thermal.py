from __future__ import annotations

import time
from pathlib import Path
from typing import Any


def _integer(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _trips(zone: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    for type_path in zone.glob("trip_point_*_type"):
        try:
            kind = type_path.read_text().strip().lower()
        except OSError:
            continue
        if kind not in {"active", "passive", "hot", "critical"}:
            continue
        value = _integer(type_path.with_name(type_path.name.replace("_type", "_temp")))
        if value and value > 0:
            result[kind] = min(value, result.get(kind, value))
    return result


def sample(sys: Path = Path("/sys")) -> dict[str, Any]:
    zones: list[dict[str, Any]] = []
    try:
        paths = sorted((sys / "class/thermal").glob("thermal_zone*"))
    except OSError:
        paths = []
    for zone in paths:
        try:
            name = (zone / "type").read_text().strip()
        except OSError:
            continue
        temperature = _integer(zone / "temp")
        trips = _trips(zone)
        if temperature is not None and trips:
            zones.append({"type": name, "temp": temperature, "trips": trips})

    nvmes: list[dict[str, Any]] = []
    try:
        hwmons = sorted((sys / "class/hwmon").glob("hwmon*"))
    except OSError:
        hwmons = []
    for hwmon in hwmons:
        try:
            if (hwmon / "name").read_text().strip() != "nvme":
                continue
        except OSError:
            continue
        temperature = _integer(hwmon / "temp1_input")
        if temperature is not None:
            nvmes.append(
                {
                    "temp": temperature,
                    "max": _integer(hwmon / "temp1_max"),
                    "critical": _integer(hwmon / "temp1_crit"),
                }
            )

    state = "NORMAL"
    reasons: list[str] = []
    observable = bool(zones or any(row.get("max") or row.get("critical") for row in nvmes))
    for row in zones:
        temp = row["temp"]
        trips = row["trips"]
        if trips.get("critical") is not None and temp >= trips["critical"]:
            state = "EMERGENCY"
            reasons.append(f"{row['type']}:critical")
        elif trips.get("hot") is not None and temp >= trips["hot"] and state != "EMERGENCY":
            state = "WARNING"
            reasons.append(f"{row['type']}:hot")
        elif (
            min((trips.get("active", 10**9), trips.get("passive", 10**9))) <= temp
            and state == "NORMAL"
        ):
            state = "WARM"
            reasons.append(f"{row['type']}:active-or-passive")
    for row in nvmes:
        if row.get("critical") and row["temp"] >= row["critical"]:
            state = "EMERGENCY"
            reasons.append("nvme:critical")
        elif row.get("max") and row["temp"] >= row["max"] and state != "EMERGENCY":
            state = "WARNING"
            reasons.append("nvme:max")
    if not observable:
        state = "UNKNOWN"
        reasons.append("no-firmware-or-nvme-trip-points")
    return {
        "time": time.time(),
        "state": state,
        "reasons": reasons,
        "zones": zones,
        "nvme": nvmes,
    }
