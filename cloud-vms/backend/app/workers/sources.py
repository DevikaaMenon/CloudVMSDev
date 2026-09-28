"""Video sources: files (replayed as a simulated live camera), RTSP/HTTP streams and webcams."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import cv2
import numpy as np

from ..core.config import PROJECT_ROOT, get_settings
from ..core.logging import redact

log = logging.getLogger("vms.source")

# Prefer TCP for RTSP (more reliable over Wi-Fi / VPN) and fail fast on dead cameras.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")


def resolve_file(reference: str) -> Path:
    p = Path(reference)
    if not p.is_absolute():
        for base in (get_settings().uploads_dir, PROJECT_ROOT):
            if (base / p).exists():
                return base / p
        p = PROJECT_ROOT / p
    return p


def build_url(stream_type: str, reference: str, username: str = "", password: str = "") -> str:
    """Compose the connection string. Credentials are only ever combined here, in memory."""
    if stream_type == "file":
        return str(resolve_file(reference))
    if stream_type == "webcam":
        return reference or "0"
    if username:
        parts = urlsplit(reference)
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        netloc = f"{quote(username, safe='')}:{quote(password, safe='')}@{host}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return reference


@dataclass
class SourceStats:
    frames_read: int = 0
    reconnects: int = 0
    read_errors: int = 0
    opened_at: float = 0.0
    fps_reported: float = 0.0


class VideoSource:
    def __init__(self, stream_type: str, url: str, realtime: bool = True, loop: bool = True):
        self.stream_type, self.url = stream_type, url
        self.realtime = realtime if stream_type == "file" else False
        self.loop = loop
        self.cap: cv2.VideoCapture | None = None
        self.stats = SourceStats()
        self.fps = 25.0
        self.width = self.height = 0
        self.ended = False
        self._t0 = 0.0
        self._idx = 0
        self._base_ts = 0.0

    @property
    def is_file(self) -> bool:
        return self.stream_type == "file"

    def open(self) -> bool:
        self.close()
        if self.is_file and not Path(self.url).exists():
            log.error("video file not found: %s", self.url)
            return False
        if self.stream_type == "webcam":
            cap = cv2.VideoCapture(int(self.url) if str(self.url).isdigit() else self.url)
        else:
            params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000]
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG, params) if not self.is_file \
                else cv2.VideoCapture(self.url)
        if not cap.isOpened():
            cap.release()
            log.warning("could not open source %s", redact(self.url))
            return False
        self.cap = cap
        fps = cap.get(cv2.CAP_PROP_FPS)
        self.fps = fps if fps and 1 <= fps <= 120 else 25.0
        self.stats.fps_reported = self.fps
        self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._t0 = time.monotonic()
        self._base_ts = time.time()
        self._idx = 0
        self.stats.opened_at = time.time()
        self.ended = False
        return True

    def read(self) -> tuple[bool, np.ndarray | None, float]:
        """Returns (ok, frame, source_timestamp_epoch_seconds)."""
        if self.cap is None:
            return False, None, 0.0
        if self.is_file and self.realtime:
            due = self._t0 + self._idx / self.fps
            now = time.monotonic()
            if due > now:
                time.sleep(due - now)
            else:
                # fell behind: skip (grab without decoding) to stay in real time
                behind = int((now - due) * self.fps)
                for _ in range(min(behind, int(self.fps))):
                    if not self.cap.grab():
                        break
                    self._idx += 1
        ok, frame = self.cap.read()
        if not ok or frame is None:
            if self.is_file:
                if self.loop:
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    self._t0 = time.monotonic()
                    self._base_ts = time.time()
                    self._idx = 0
                    ok, frame = self.cap.read()
                    if ok:
                        self._idx = 1
                        return True, frame, time.time()
                self.ended = True
            self.stats.read_errors += 1
            return False, None, 0.0
        self._idx += 1
        self.stats.frames_read += 1
        if self.is_file and not self.realtime:
            ts = self._base_ts + self._idx / self.fps  # video time (offline analysis)
        else:
            ts = time.time()
        return True, frame, ts

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None


def probe(stream_type: str, url: str) -> dict:
    """Open a source briefly and grab one frame (used when registering a camera)."""
    src = VideoSource(stream_type, url, realtime=False, loop=False)
    try:
        if not src.open():
            return {"ok": False, "message": "could not open the stream"}
        ok, frame, _ = src.read()
        if not ok:
            return {"ok": False, "message": "opened, but no frame could be read"}
        h, w = frame.shape[:2]
        frames = int(src.cap.get(cv2.CAP_PROP_FRAME_COUNT)) if src.is_file else 0
        return {"ok": True, "width": w, "height": h, "fps": round(src.fps, 2),
                "duration_s": round(frames / src.fps, 1) if frames else None, "frame": frame}
    finally:
        src.close()
