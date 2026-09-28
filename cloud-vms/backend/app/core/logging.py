"""Structured logging with request correlation IDs and credential redaction."""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

# rtsp://user:pass@host -> rtsp://***:***@host
_CRED_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@")


def redact(text: str) -> str:
    """Remove credentials embedded in URLs. Safe to call on any string."""
    return _CRED_RE.sub(r"\1***:***@", text or "")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
            "request_id": request_id_var.get(),
        }
        for key in ("camera_id", "event_id", "track_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload)


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        return redact(base)


def setup_logging(json_logs: bool = False, level: str = "INFO") -> None:
    root = logging.getLogger()
    if getattr(root, "_vms_configured", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_logs else PlainFormatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("uvicorn.access", "ultralytics", "httpx", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._vms_configured = True  # type: ignore[attr-defined]
