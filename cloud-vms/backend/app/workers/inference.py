"""Shared inference service with cross-camera batching and bounded queues.

Every camera submits sampled frames here. Each camera has a small bounded
queue (default 2 frames): when inference can't keep up, the *oldest* frame is
dropped and counted, so latency stays bounded instead of growing forever
(backpressure). One inference thread pulls frames from several cameras
round-robin and runs them through the detector as a single batch.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

log = logging.getLogger("vms.inference")


@dataclass
class InferenceJob:
    camera_id: int
    ts: float
    frame: np.ndarray
    callback: Callable
    submitted: float = field(default_factory=time.perf_counter)
    meta: dict = field(default_factory=dict)


class InferenceService:
    def __init__(self, detector, batch_size: int = 4, max_pending_per_camera: int = 2, threads: int = 1):
        self.detector = detector
        self.batch_size = max(1, batch_size)
        self.max_pending = max(1, max_pending_per_camera)
        self._queues: dict[int, deque[InferenceJob]] = {}
        self._rr: deque[int] = deque()
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self.dropped: dict[int, int] = {}
        self.processed = 0
        self.batches = 0
        self.last_batch_ms = 0.0
        self._threads = [threading.Thread(target=self._loop, name=f"inference-{i}", daemon=True)
                         for i in range(max(1, threads))]
        for t in self._threads:
            t.start()

    # ------------------------------------------------------------- API
    def submit(self, job: InferenceJob) -> bool:
        with self._cond:
            q = self._queues.get(job.camera_id)
            if q is None:
                q = self._queues[job.camera_id] = deque()
                self._rr.append(job.camera_id)
            accepted = True
            if len(q) >= self.max_pending:
                q.popleft()  # drop the stalest frame
                self.dropped[job.camera_id] = self.dropped.get(job.camera_id, 0) + 1
                accepted = False
            q.append(job)
            self._cond.notify()
            return accepted

    def queue_depth(self, camera_id: int | None = None) -> int:
        with self._cond:
            if camera_id is not None:
                return len(self._queues.get(camera_id, ()))
            return sum(len(q) for q in self._queues.values())

    def remove_camera(self, camera_id: int) -> None:
        with self._cond:
            self._queues.pop(camera_id, None)
            if camera_id in self._rr:
                self._rr.remove(camera_id)

    def swap_detector(self, detector) -> None:
        with self._cond:
            self.detector = detector
        log.info("detector swapped to %s", getattr(detector, "version", "?"))

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()

    # ------------------------------------------------------------- worker loop
    def _take_batch(self) -> list[InferenceJob]:
        batch: list[InferenceJob] = []
        with self._cond:
            while not self._stop.is_set() and not any(self._queues.values()):
                self._cond.wait(0.5)
            if self._stop.is_set():
                return []
            # round-robin across cameras so a busy camera can't starve the others
            for _ in range(len(self._rr)):
                if len(batch) >= self.batch_size:
                    break
                cam = self._rr[0]
                self._rr.rotate(-1)
                q = self._queues.get(cam)
                if q:
                    batch.append(q.popleft())
        return batch

    def _loop(self) -> None:
        while not self._stop.is_set():
            batch = self._take_batch()
            if not batch:
                continue
            det = self.detector
            t0 = time.perf_counter()
            try:
                if hasattr(det, "script"):  # FakeDetector wants timestamps
                    results = det.predict([j.frame for j in batch], [{"ts": j.ts, **j.meta} for j in batch])
                else:
                    results = det.predict([j.frame for j in batch])
            except Exception:
                log.exception("inference failed for a batch of %d frames", len(batch))
                results = [[] for _ in batch]
            ms = (time.perf_counter() - t0) * 1000
            self.last_batch_ms = ms
            self.batches += 1
            self.processed += len(batch)
            per_frame = ms / len(batch)
            for job, dets in zip(batch, results):
                try:
                    job.callback(job, dets, per_frame)
                except Exception:
                    log.exception("result callback failed for camera %s", job.camera_id)
