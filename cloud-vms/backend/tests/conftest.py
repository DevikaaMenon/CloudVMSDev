"""Test configuration: every test session uses a throw-away data directory and SQLite DB."""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="vms-test-")
os.environ["VMS_DATA_DIR"] = _TMP
os.environ["VMS_ENVIRONMENT"] = "test"
os.environ["VMS_EMBEDDED_WORKERS"] = "false"
os.environ["VMS_ADMIN_PASSWORD"] = "Admin12345"
os.environ["VMS_TIMEZONE"] = "Asia/Kolkata"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
