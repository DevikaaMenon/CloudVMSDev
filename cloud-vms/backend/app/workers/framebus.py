"""Latest-frame bus for live preview (in-process, or Redis when workers run separately)."""
from __future__ import annotations

import threading
import time


class MemoryFrameBus:
    def __init__(self):
        self._frames: dict[int, tuple[float, bytes]] = {}
        self._cond = threading.Condition()

    def publish(self, camera_id: int, jpeg: bytes) -> None:
        with self._cond:
            self._frames[camera_id] = (time.time(), jpeg)
            self._cond.notify_all()

    def latest(self, camera_id: int) -> tuple[float, bytes] | None:
        return self._frames.get(camera_id)

    def wait_newer(self, camera_id: int, after: float, timeout: float = 2.0) -> tuple[float, bytes] | None:
        end = time.time() + timeout
        with self._cond:
            while True:
                item = self._frames.get(camera_id)
                if item and item[0] > after:
                    return item
                remaining = end - time.time()
                if remaining <= 0:
                    return item
                self._cond.wait(remaining)

    def clear(self, camera_id: int) -> None:
        self._frames.pop(camera_id, None)


class RedisFrameBus:
    def __init__(self, url: str):
        import redis
        self.r = redis.Redis.from_url(url)

    def publish(self, camera_id, jpeg):
        ts = time.time()
        p = self.r.pipeline()
        p.set(f"vms:frame:{camera_id}", jpeg, ex=10)
        p.set(f"vms:frame_ts:{camera_id}", ts, ex=10)
        p.execute()

    def latest(self, camera_id):
        jpeg, ts = self.r.get(f"vms:frame:{camera_id}"), self.r.get(f"vms:frame_ts:{camera_id}")
        return (float(ts), jpeg) if jpeg and ts else None

    def wait_newer(self, camera_id, after, timeout=2.0):
        end = time.time() + timeout
        while time.time() < end:
            item = self.latest(camera_id)
            if item and item[0] > after:
                return item
            time.sleep(0.05)
        return self.latest(camera_id)

    def clear(self, camera_id):
        self.r.delete(f"vms:frame:{camera_id}", f"vms:frame_ts:{camera_id}")


_bus = None


def get_frame_bus():
    global _bus
    if _bus is None:
        from ..core.config import get_settings
        url = get_settings().redis_url
        _bus = RedisFrameBus(url) if url else MemoryFrameBus()
    return _bus
