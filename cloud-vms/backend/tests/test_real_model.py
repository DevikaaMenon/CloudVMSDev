"""Real-model smoke test: YOLO26 + the full analytics pipeline on the synthetic demo video.

Skipped automatically when the weights or the demo video are missing
(create them with setup_windows.bat, or: python scripts/make_demo_video.py).
The demo video has known ground truth: 3 people walk in, 1 walks out, 1 enters the
staff-only area, and a bus drives along the road (never through the gate).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEIGHTS = ROOT / "data" / "weights" / "yolo26n.pt"
VIDEO = ROOT / "data" / "uploads" / "demo_gate.mp4"

pytestmark = pytest.mark.skipif(not (WEIGHTS.exists() and VIDEO.exists()), reason="weights or demo video missing")


def test_demo_video_counts_and_incident():
    sys.path.insert(0, str(ROOT / "ml" / "inference"))
    from offline_run import load_zones, model_specs, run
    res = run(str(VIDEO), load_zones(str(ROOT / "ml" / "inference" / "example_zones.json")), 5.0,
              model_specs("shared", None), device="cpu")
    assert res["counts"]["people_in"] == 3
    assert res["counts"]["people_out"] == 1
    new_events = [e for e in res["events"] if e["kind"] == "new"]
    assert [e["type"] for e in new_events] == ["RESTRICTED_AREA_ACCESS"]
    assert new_events[0]["class"] == "person"
    assert res["counts"]["unique_vehicles_seen"] >= 1  # the bus
