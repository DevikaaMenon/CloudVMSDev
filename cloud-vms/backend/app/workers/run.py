"""Run camera workers as a separate process (scale-out mode).

    python -m app.workers.run --group gate-cameras

Point every worker at the same database (VMS_DATABASE_URL), object storage
(VMS_STORAGE_BACKEND=s3) and Redis (VMS_REDIS_URL, for live preview), and set
VMS_EMBEDDED_WORKERS=false on the API. Cameras are assigned to a worker by
their ``worker_group`` field.
"""
from __future__ import annotations

import argparse
import logging
import signal
import threading

from ..core.config import get_settings
from ..core.logging import setup_logging
from ..db import init_db
from .supervisor import Supervisor


def main() -> None:
    ap = argparse.ArgumentParser(description="Cloud VMS camera worker")
    ap.add_argument("--group", default=None, help="worker group to serve (default: VMS_WORKER_GROUP)")
    args = ap.parse_args()
    setup_logging(json_logs=get_settings().environment == "production")
    init_db()
    sup = Supervisor(group=args.group).start()
    logging.getLogger("vms").info("worker started for group '%s'", sup.group)
    done = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: done.set())
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    done.wait()
    sup.stop()


if __name__ == "__main__":
    main()
