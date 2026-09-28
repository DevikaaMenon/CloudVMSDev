"""Zone access policies and schedules.

A zone has a ``default_action`` (alert | allow) and an ordered list of
policies. A policy applies to some object types (groups such as "vehicle" or
classes such as "two_wheeler"), is in force during its schedule (empty =
always) and either *allows* or *alerts*. For an object inside the zone at a
given local time:

    1. collect enabled policies in force that match the object's class;
    2. any matching "allow" wins (explicit permission);
    3. otherwise any matching "alert" produces an incident;
    4. otherwise the zone's default action applies.

Examples
    * "No vehicles on the footpath":  restricted zone, default allow,
      policy {object_types: [vehicle], action: alert}
    * "Campus perimeter closed at night":  intrusion zone, default allow,
      policy {object_types: [person, vehicle], schedule: 20:00-06:00 daily, action: alert}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from .classes import matches_object_types

SEVERITIES = ("low", "medium", "high", "critical")


def _parse_hm(s: str) -> time:
    h, m = (int(x) for x in str(s).split(":")[:2])
    if h == 24 and m == 0:
        return time(23, 59, 59)
    return time(h, m)


def validate_schedule(schedule: list) -> list[str]:
    problems = []
    if not isinstance(schedule, list):
        return ["schedule must be a list of windows"]
    for i, w in enumerate(schedule):
        try:
            days = w.get("days", list(range(7)))
            if not all(isinstance(d, int) and 0 <= d <= 6 for d in days):
                problems.append(f"window {i + 1}: days must be integers 0 (Mon) .. 6 (Sun)")
            _parse_hm(w["start"])
            _parse_hm(w["end"])
        except (KeyError, ValueError, AttributeError, TypeError):
            problems.append(f"window {i + 1}: needs start and end as HH:MM")
    return problems


def in_schedule(schedule: list | None, local_dt: datetime) -> bool:
    """True if ``local_dt`` falls inside any window. Empty schedule = always.

    Windows whose end is earlier than their start run overnight; the ``days``
    list refers to the day on which the window *starts*.
    """
    if not schedule:
        return True
    t = local_dt.time()
    for w in schedule:
        days = w.get("days", list(range(7)))
        start, end = _parse_hm(w["start"]), _parse_hm(w["end"])
        if start <= end:
            if local_dt.weekday() in days and start <= t <= end:
                return True
        else:  # overnight
            if local_dt.weekday() in days and t >= start:
                return True
            if (local_dt - timedelta(days=1)).weekday() in days and t <= end:
                return True
    return False


@dataclass
class PolicySpec:
    id: int | None
    object_types: list[str]
    schedule: list
    action: str  # alert | allow
    severity: str | None = None
    enabled: bool = True
    name: str = ""


@dataclass
class Decision:
    action: str  # alert | allow
    policy_id: int | None
    severity: str
    reason: str = ""


@dataclass
class PolicySet:
    default_action: str = "allow"
    severity: str = "medium"
    policies: list[PolicySpec] = field(default_factory=list)

    def evaluate(self, cls: str, local_dt: datetime) -> Decision:
        matching = [p for p in self.policies
                    if p.enabled and matches_object_types(p.object_types, cls) and in_schedule(p.schedule, local_dt)]
        for p in matching:
            if p.action == "allow":
                return Decision("allow", p.id, self.severity, f"allowed by policy '{p.name or p.id}'")
        for p in matching:
            if p.action == "alert":
                return Decision("alert", p.id, p.severity or self.severity,
                                f"violates policy '{p.name or p.id}'")
        return Decision(self.default_action, None, self.severity, "zone default")
