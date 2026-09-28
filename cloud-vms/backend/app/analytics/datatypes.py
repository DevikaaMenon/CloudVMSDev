"""Plain data types passed between detector, tracker, rules and event engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .classes import group_of


@dataclass
class Detection:
    bbox: tuple[float, float, float, float]  # x_min, y_min, x_max, y_max in pixels
    confidence: float
    cls: str  # canonical class
    subtype: str = ""  # original model label
    model: str = ""

    @property
    def group(self) -> str:
        return group_of(self.cls)


@dataclass
class TrackView:
    """Read-only snapshot of a track handed to the rules."""
    uid: int
    root_uid: int
    cls: str
    group: str
    subtype: str
    bbox: tuple[float, float, float, float]
    confidence: float
    mean_confidence: float
    state: str  # NEW | ACTIVE | LOST | TERMINATED
    hits: int
    first_ts: float
    last_ts: float
    matched: bool  # observed in the current frame

    def anchor(self, mode: str = "bottom_center") -> tuple[float, float]:
        x1, y1, x2, y2 = self.bbox
        if mode == "center":
            return ((x1 + x2) / 2, (y1 + y2) / 2)
        return ((x1 + x2) / 2, y2)  # ground contact point


@dataclass
class CrossingRecord:
    zone_id: int
    track_uid: int
    root_uid: int
    cls: str
    group: str
    subtype: str
    direction: str  # in | out
    ts: float


@dataclass
class EventCandidate:
    event_type: str
    zone_id: Optional[int]
    track_uid: Optional[int]
    root_uid: Optional[int]
    cls: str
    ts: float  # source timestamp (epoch seconds)
    confidence: float
    bbox: Optional[tuple[float, float, float, float]]
    severity: str
    title: str
    session_key: str  # identifies the zone session that produced it
    policy_id: Optional[int] = None
    metadata: dict = field(default_factory=dict)
