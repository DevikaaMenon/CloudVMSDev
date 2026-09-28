"""Turns rule candidates into incidents: validation, deduplication, merging, severity.

Deduplication strategy (per camera):
* one incident per *zone session* of a root track (a track entering a zone
  and staying inside for a minute yields ONE event, not one per frame);
* a cooldown per (zone, event type, root track) so an object hovering on
  the boundary or re-identified after an ID switch doesn't re-alert;
* a merge window per (zone, event type): several objects entering together
  are attached to the same incident as extra objects.
The resulting ``dedup_key`` is unique in the database, which makes event
creation idempotent if a job is ever delivered twice.
"""
from __future__ import annotations

from dataclasses import dataclass

from .policy import SEVERITIES
from .datatypes import EventCandidate


@dataclass
class EngineConfig:
    cooldown_seconds: float = 60.0
    merge_window_seconds: float = 10.0
    escalate_at_objects: int = 3

    @classmethod
    def from_dict(cls, d: dict | None) -> "EngineConfig":
        d = d or {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Decision:
    kind: str  # "new" | "merge" | "drop"
    dedup_key: str
    candidate: EventCandidate
    merge_into: str | None = None  # dedup_key of the open event
    reason: str = ""


def escalate(severity: str, steps: int = 1) -> str:
    i = SEVERITIES.index(severity) if severity in SEVERITIES else 1
    return SEVERITIES[min(len(SEVERITIES) - 1, i + steps)]


class EventEngine:
    def __init__(self, camera_id: int, config: EngineConfig | None = None):
        self.camera_id = camera_id
        self.cfg = config or EngineConfig()
        self._last_by_track: dict[tuple, float] = {}  # (zone, type, root) -> ts
        self._open_by_zone: dict[tuple, tuple[float, str, int]] = {}  # (zone, type) -> (ts, key, objects)
        self._seen_sessions: set[str] = set()

    def decide(self, c: EventCandidate) -> Decision:
        key = f"{self.camera_id}:{c.event_type}:{c.session_key}"
        if key in self._seen_sessions:
            return Decision("drop", key, c, reason="duplicate session")
        track_key = (c.zone_id, c.event_type, c.root_uid)
        if c.root_uid is not None:
            last = self._last_by_track.get(track_key)
            if last is not None and c.ts - last < self.cfg.cooldown_seconds:
                self._seen_sessions.add(key)
                return Decision("drop", key, c, reason="cooldown")
        zone_key = (c.zone_id, c.event_type)
        open_ev = self._open_by_zone.get(zone_key)
        self._seen_sessions.add(key)
        if c.root_uid is not None:
            self._last_by_track[track_key] = c.ts
        if open_ev and c.ts - open_ev[0] <= self.cfg.merge_window_seconds and c.root_uid is not None:
            n = open_ev[2] + 1
            self._open_by_zone[zone_key] = (open_ev[0], open_ev[1], n)
            return Decision("merge", key, c, merge_into=open_ev[1], reason=f"{n} objects")
        self._open_by_zone[zone_key] = (c.ts, key, 1)
        return Decision("new", key, c)

    def objects_in(self, dedup_key: str) -> int:
        for ts, k, n in self._open_by_zone.values():
            if k == dedup_key:
                return n
        return 1

    def prune(self, now: float) -> None:
        horizon = max(self.cfg.cooldown_seconds, self.cfg.merge_window_seconds) * 2
        self._last_by_track = {k: v for k, v in self._last_by_track.items() if now - v < horizon}
        if len(self._seen_sessions) > 5000:
            self._seen_sessions = set(list(self._seen_sessions)[-2000:])
