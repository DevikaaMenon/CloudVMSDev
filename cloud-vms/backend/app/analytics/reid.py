"""Short-term re-identification to avoid double counting.

When somebody walks behind a bus the tracker usually loses them and starts a
new track a second later. For *unique counting* that would count one person
twice. When a new track is confirmed we compare its appearance with tracks
of the same group that ended recently and nearby; if they look alike the new
track inherits the old track's ``root_uid``.

Two embedders are available:
* ``histogram`` (default, no extra dependencies): HSV colour histograms of
  the upper and lower half of the box — cheap and good enough for a short
  time window at one camera.
* ``osnet``: OSNet (Zhou et al., ICCV 2019) via the optional ``torchreid``
  package, for stronger appearance matching.

Re-ID here only links appearances within one camera and a short time window.
It does not identify who a person is.
"""
from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np

log = logging.getLogger("vms.reid")


class HistogramEmbedder:
    name = "histogram"

    def embed(self, frame: np.ndarray, bbox) -> np.ndarray | None:
        x1, y1, x2, y2 = (int(max(0, v)) for v in bbox)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0 or crop.shape[0] < 8 or crop.shape[1] < 4:
            return None
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        h = hsv.shape[0]
        feats = []
        for part in (hsv[: h // 2], hsv[h // 2:]):
            hist = cv2.calcHist([part], [0, 1, 2], None, [8, 6, 4], [0, 180, 0, 256, 0, 256])
            feats.append(hist.flatten())
        v = np.concatenate(feats).astype(np.float32)
        v = np.sqrt(v)  # Hellinger mapping
        n = np.linalg.norm(v)
        return v / n if n > 0 else None


class OSNetEmbedder:
    name = "osnet"

    def __init__(self, device: str = "cpu"):
        from torchreid.utils import FeatureExtractor  # optional dependency
        self.extractor = FeatureExtractor(model_name="osnet_x0_25", device=device)

    def embed(self, frame, bbox):
        x1, y1, x2, y2 = (int(max(0, v)) for v in bbox)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        f = self.extractor([cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)])[0].cpu().numpy()
        n = np.linalg.norm(f)
        return f / n if n > 0 else None


def make_embedder(backend: str, device: str = "cpu"):
    if backend == "osnet":
        try:
            return OSNetEmbedder(device)
        except Exception as exc:  # torchreid missing
            log.warning("OSNet re-ID unavailable (%s); falling back to histogram", exc)
    return HistogramEmbedder()


@dataclass
class _GalleryItem:
    root_uid: int
    group: str
    end_ts: float
    end_xy: tuple[float, float]
    size: float
    emb: np.ndarray


class ReIdentifier:
    def __init__(self, embedder, window_seconds: float = 8.0, threshold: float = 0.8,
                 max_distance_frac: float = 0.35, gallery_size: int = 200):
        self.embedder = embedder
        self.window = window_seconds
        self.threshold = threshold
        self.max_distance_frac = max_distance_frac
        self.gallery: deque[_GalleryItem] = deque(maxlen=gallery_size)

    def remember(self, root_uid: int, group: str, end_ts: float, bbox, emb) -> None:
        if emb is None:
            return
        x1, y1, x2, y2 = bbox
        self.gallery.append(_GalleryItem(root_uid, group, end_ts, ((x1 + x2) / 2, y2),
                                         math.hypot(x2 - x1, y2 - y1), emb))

    def match(self, group: str, start_ts: float, bbox, emb, frame_diag: float,
              exclude_roots: set[int] | None = None) -> int | None:
        if emb is None:
            return None
        x1, y1, x2, y2 = bbox
        start_xy = ((x1 + x2) / 2, y2)
        best, best_sim = None, self.threshold
        for g in self.gallery:
            if g.group != group or not (0 <= start_ts - g.end_ts <= self.window):
                continue
            if exclude_roots and g.root_uid in exclude_roots:
                continue
            if math.dist(start_xy, g.end_xy) > self.max_distance_frac * frame_diag:
                continue
            sim = float(np.dot(g.emb, emb))
            if sim > best_sim:
                best, best_sim = g.root_uid, sim
        return best
