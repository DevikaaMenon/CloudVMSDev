"""Shared object detection.

One ``Detector`` wraps one or more YOLO models (Ultralytics). Each model has a
``class_map`` translating its labels into canonical classes, so the rest of
the system never needs to know which dataset a model was trained on.

Profiles
    shared       one model detects people and vehicles (default, cheapest)
    specialized  a person model + an Indian-vehicle model (e.g. UVH-26)

Models can be swapped at runtime (the registry in the database decides which
versions are active) without touching the analytics code.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .classes import CANONICAL_CLASSES, normalize_label
from .datatypes import Detection

log = logging.getLogger("vms.detector")


def resolve_device(pref: str = "auto") -> str:
    if pref and pref != "auto":
        return pref
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda:0"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


@dataclass
class ModelSpec:
    name: str
    weights: str
    class_map: dict[str, str]  # model label -> canonical class
    role: str = "shared"


@dataclass
class ModelRunner:
    spec: ModelSpec
    device: str
    imgsz: int = 640
    conf: float = 0.15
    model: object = None
    keep_idx: list[int] = field(default_factory=list)
    idx_to_cls: dict[int, tuple[str, str]] = field(default_factory=dict)

    def load(self) -> None:
        from ultralytics import YOLO
        self.model = YOLO(self.spec.weights)
        names = self.model.names  # {idx: label}
        cmap = {normalize_label(k): v for k, v in self.spec.class_map.items()}
        for idx, label in names.items():
            canon = cmap.get(normalize_label(label))
            if canon and canon in CANONICAL_CLASSES:
                self.keep_idx.append(int(idx))
                self.idx_to_cls[int(idx)] = (canon, str(label))
        if not self.keep_idx:
            raise ValueError(f"model {self.spec.name}: none of its labels map to canonical classes "
                             f"(labels: {list(names.values())[:20]}...)")
        log.info("loaded %s (%s) on %s, classes -> %s", self.spec.name, self.spec.weights, self.device,
                 sorted({c for c, _ in self.idx_to_cls.values()}))

    def predict(self, frames: list[np.ndarray]) -> list[list[Detection]]:
        results = self.model.predict(frames, imgsz=self.imgsz, conf=self.conf, classes=self.keep_idx,
                                     device=self.device, verbose=False)
        out: list[list[Detection]] = []
        for r in results:
            dets = []
            if r.boxes is not None and len(r.boxes):
                xyxy = r.boxes.xyxy.cpu().numpy()
                confs = r.boxes.conf.cpu().numpy()
                clss = r.boxes.cls.cpu().numpy().astype(int)
                for box, c, k in zip(xyxy, confs, clss):
                    canon, label = self.idx_to_cls.get(int(k), (None, ""))
                    if canon:
                        dets.append(Detection(tuple(float(v) for v in box), float(c), canon, label,
                                              self.spec.name))
            out.append(dets)
        return out


class Detector:
    def __init__(self, specs: list[ModelSpec], device: str = "auto", imgsz: int = 640, conf: float = 0.15):
        self.device = resolve_device(device)
        self.runners = [ModelRunner(s, self.device, imgsz, conf) for s in specs]
        self._lock = threading.Lock()
        self.last_latency_ms = 0.0

    @property
    def version(self) -> str:
        return "+".join(r.spec.name for r in self.runners)

    def load(self) -> "Detector":
        for r in self.runners:
            r.load()
        return self

    def warmup(self) -> None:
        self.predict([np.zeros((480, 640, 3), dtype=np.uint8)])

    def predict(self, frames: list[np.ndarray]) -> list[list[Detection]]:
        t0 = time.perf_counter()
        with self._lock:  # one inference at a time per model set
            merged: list[list[Detection]] = [[] for _ in frames]
            for r in self.runners:
                for i, dets in enumerate(r.predict(frames)):
                    merged[i].extend(dets)
        self.last_latency_ms = (time.perf_counter() - t0) * 1000
        return merged


class FakeDetector:
    """Deterministic detector for tests and demos without model weights.

    ``script`` is a callable (frame_index, ts, frame) -> list[Detection].
    """

    version = "fake-detector"
    device = "cpu"

    def __init__(self, script):
        self.script = script
        self.calls = 0
        self.last_latency_ms = 0.1

    def load(self):
        return self

    def warmup(self):
        pass

    def predict(self, frames, meta=None):
        out = []
        for i, f in enumerate(frames):
            m = (meta or [{}] * len(frames))[i]
            out.append(self.script(self.calls, m.get("ts", 0.0), f))
            self.calls += 1
        return out


HUB_PREFIXES = ("yolo", "yolov", "rtdetr")


def weights_available(weights: str) -> bool:
    """True if the file exists, or it is an official Ultralytics name that auto-downloads."""
    p = Path(weights)
    return p.exists() or (p.suffix == ".pt" and p.name.startswith(HUB_PREFIXES))
