"""Per-camera analytics pipeline: detections -> tracks -> rules -> incident decisions.

This module is pure Python (no database, no I/O) so the same code runs
inside the live worker, in offline evaluation scripts and in unit tests.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from .classes import RIDEABLE, matches_object_types
from .event_engine import Decision, EngineConfig, EventEngine
from .geometry import intersection_over_first
from .reid import ReIdentifier, make_embedder
from .rules import LineRule, PolygonRule, ZoneSpec, build_rules
from .tracker import LOST, ByteTracker, TrackerConfig
from .datatypes import CrossingRecord, Detection, TrackView


@dataclass
class AnalyticsConfig:
    inference_fps: float = 5.0
    imgsz: int = 640
    detector_conf: float = 0.15  # low on purpose: ByteTrack uses weak boxes in its 2nd stage
    classes: list = field(default_factory=list)  # canonical classes/groups to keep; [] = all
    rider_suppression: bool = True
    rider_ioa: float = 0.3
    reid_enabled: bool = True
    reid_backend: str = "histogram"  # histogram | osnet
    reid_window_seconds: float = 8.0
    reid_threshold: float = 0.8
    tracker: dict = field(default_factory=dict)
    events: dict = field(default_factory=dict)
    evidence_pre_seconds: float = 5.0
    evidence_post_seconds: float = 8.0
    evidence_fps: float = 8.0
    evidence_width: int = 960
    preview_fps: float = 8.0
    preview_width: int = 960
    realtime: bool = True  # file sources: play at recorded speed (simulated live camera)
    loop: bool = True  # file sources: restart at the end
    anchor: str = "bottom_center"

    @classmethod
    def from_dict(cls, d: dict | None) -> "AnalyticsConfig":
        d = d or {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class TrackSummary:
    uid: int
    root_uid: int
    cls: str
    group: str
    subtype: str
    first_ts: float
    last_ts: float
    hits: int
    mean_confidence: float
    trajectory: list  # [[t, x_norm, y_norm], ...]


@dataclass
class FrameResult:
    ts: float
    tracks: list[TrackView]
    lost: list[TrackView]
    crossings: list[CrossingRecord]
    decisions: list[Decision]
    terminated: list[TrackSummary]
    zone_occupancy: dict[int, int]
    line_totals: dict[int, dict]
    detections: list[Detection]
    tracker_ms: float = 0.0


def suppress_riders(dets: list[Detection], min_ioa: float) -> list[Detection]:
    """Drop person boxes that sit on a bicycle / two-wheeler (they're riders, not pedestrians)."""
    rides = [d for d in dets if d.cls in RIDEABLE]
    if not rides:
        return dets
    out = []
    for d in dets:
        if d.cls == "person":
            ax, ay = (d.bbox[0] + d.bbox[2]) / 2, d.bbox[3]
            riding = False
            for r in rides:
                x1, y1, x2, y2 = r.bbox
                pad = 0.1 * (x2 - x1)
                if x1 - pad <= ax <= x2 + pad and y1 <= ay <= y2 + 0.1 * (y2 - y1) \
                        and intersection_over_first(d.bbox, r.bbox) >= min_ioa:
                    riding = True
                    break
            if riding:
                continue
        out.append(d)
    return out


class CameraAnalytics:
    def __init__(self, camera_id: int, config: AnalyticsConfig, zones: list[ZoneSpec],
                 local_time=None, device: str = "cpu"):
        self.camera_id = camera_id
        self.cfg = config
        self.local_time = local_time or (lambda ts: datetime.fromtimestamp(ts))
        self.tracker = ByteTracker(TrackerConfig.from_dict(config.tracker))
        self.engine = EventEngine(camera_id, EngineConfig.from_dict(config.events))
        self.reid = (ReIdentifier(make_embedder(config.reid_backend, device), config.reid_window_seconds,
                                  config.reid_threshold) if config.reid_enabled else None)
        self.zones = zones
        self.size: tuple[int, int] | None = None
        self.lines: list[LineRule] = []
        self.polys: list[PolygonRule] = []
        self._frame_i = 0

    # ------------------------------------------------------------ configuration
    def set_zones(self, zones: list[ZoneSpec]) -> None:
        self.zones = zones
        if self.size:
            self.lines, self.polys = build_rules(zones, *self.size)

    def _ensure_size(self, w: int, h: int) -> None:
        if self.size != (w, h):
            self.size = (w, h)
            self.lines, self.polys = build_rules(self.zones, w, h)

    # ------------------------------------------------------------ main step
    def process(self, ts: float, frame: np.ndarray | None, detections: list[Detection],
                frame_size: tuple[int, int] | None = None) -> FrameResult:
        t0 = time.perf_counter()
        if frame is not None:
            h, w = frame.shape[:2]
        else:
            w, h = frame_size or (1920, 1080)
        self._ensure_size(w, h)
        self._frame_i += 1
        dets = detections
        if self.cfg.classes:
            dets = [d for d in dets if matches_object_types(self.cfg.classes, d.cls)]
        if self.cfg.rider_suppression:
            dets = suppress_riders(dets, self.cfg.rider_ioa)

        out = self.tracker.update(dets, ts)
        diag = math.hypot(w, h)

        # appearance: refresh embeddings every few observations
        if self.reid is not None and frame is not None:
            for tv in out.tracks:
                tr = self.tracker.get(tv.uid)
                if tr is not None and (tr.appearance is None or tr.hits % 5 == 0):
                    emb = self.reid.embedder.embed(frame, tv.bbox)
                    if emb is not None:
                        tr.appearance = emb if tr.appearance is None else _blend(tr.appearance, emb)

        # short-term re-identification for freshly confirmed tracks
        if self.reid is not None and out.confirmed_now:
            active_roots = {t.root_uid for t in out.tracks if t.uid not in out.confirmed_now}
            for uid in out.confirmed_now:
                tr = self.tracker.get(uid)
                if tr is None or tr.appearance is None:
                    continue
                # candidates: recently terminated (gallery) + currently LOST tracks
                best_root = self.reid.match(tr.group, tr.first_ts, tr.last_bbox, tr.appearance, diag,
                                            exclude_roots=active_roots)
                lost_match = self._match_lost(tr, diag, active_roots)
                if lost_match is not None:
                    best_root = lost_match
                if best_root is not None:
                    tr.root_uid = best_root
                    for i, tv in enumerate(out.tracks):
                        if tv.uid == uid:
                            out.tracks[i] = tr.view(True)

        crossings: list[CrossingRecord] = []
        candidates = []
        local_dt = self.local_time(ts)
        for tv in out.tracks:
            for lr in self.lines:
                c = lr.update(tv, ts)
                if c:
                    crossings.append(c)
            for pr in self.polys:
                candidates.extend(pr.update(tv, ts, local_dt))
        for pr in self.polys:
            candidates.extend(pr.crowd_check(ts, out.tracks[0] if out.tracks else None))

        summaries: list[TrackSummary] = []
        for tr in out.terminated:
            for rule in (*self.lines, *self.polys):
                rule.forget(tr.uid)
            if self.reid is not None and tr.appearance is not None:
                self.reid.remember(tr.root_uid or tr.uid, tr.group, tr.last_ts, tr.last_bbox, tr.appearance)
            summaries.append(self._summary(tr, w, h))

        decisions = [self.engine.decide(c) for c in candidates]
        if self._frame_i % 100 == 0:
            self.engine.prune(ts)
        return FrameResult(
            ts=ts, tracks=out.tracks, lost=out.lost, crossings=crossings, decisions=decisions,
            terminated=summaries,
            zone_occupancy={p.zone.id: p.occupancy for p in self.polys},
            line_totals={lr.zone.id: dict(lr.totals) for lr in self.lines},
            detections=dets, tracker_ms=(time.perf_counter() - t0) * 1000)

    def _match_lost(self, tr, diag: float, exclude_roots: set[int]) -> int | None:
        best, best_sim = None, self.reid.threshold
        x1, y1, x2, y2 = tr.last_bbox
        start = ((x1 + x2) / 2, y2)
        for other in self.tracker.tracks:
            if other.uid == tr.uid or other.state != LOST or other.group != tr.group or other.appearance is None:
                continue
            if (other.root_uid or other.uid) in exclude_roots:
                continue
            if tr.first_ts - other.last_ts > self.reid.window:
                continue
            ox1, oy1, ox2, oy2 = other.last_bbox
            if math.dist(start, ((ox1 + ox2) / 2, oy2)) > self.reid.max_distance_frac * diag:
                continue
            sim = float(np.dot(other.appearance, tr.appearance))
            if sim > best_sim:
                best, best_sim = other, sim
        if best is None:
            return None
        root = best.root_uid or best.uid
        best.last_ts = -1e18  # retire the lost track; its identity continues in the new one
        return root

    def flush(self) -> list[TrackSummary]:
        w, h = self.size or (1920, 1080)
        return [self._summary(t, w, h) for t in self.tracker.flush()]

    @staticmethod
    def _summary(tr, w: int, h: int) -> TrackSummary:
        traj = list(tr.trajectory)
        step = max(1, len(traj) // 50)
        pts = [[round(t, 2), round(x / w, 4), round(y / h, 4)] for t, x, y in traj[::step]]
        last_ts = tr.last_ts if tr.last_ts > 0 else (traj[-1][0] if traj else tr.first_ts)
        return TrackSummary(tr.uid, tr.root_uid or tr.uid, tr.cls, tr.group, tr.subtype, tr.first_ts,
                            last_ts, tr.hits, round(tr.mean_conf, 3), pts)


def _blend(a: np.ndarray, b: np.ndarray, w: float = 0.3) -> np.ndarray:
    v = (1 - w) * a + w * b
    n = np.linalg.norm(v)
    return v / n if n > 0 else a
