"""Incident-level evaluation: event precision / recall, false alarms per hour, time-to-alert.

    python ml/evaluation/eval_events.py --gt data/eval/ucf_intrusion.json --fps 5 --register
    python ml/evaluation/eval_events.py --gt data/eval/college_events.json --fps 5 --tolerance 10

Ground truth JSON (made by ml/datasets/prepare_*.py or import_college.py events-template):
    {"videos": [{"video": "...mp4", "zones": "zones.json" | null, "clock": "2026-01-05T23:00",
                 "label": "positive" | "negative",
                 "events": [{"type": "INTRUSION_DETECTED", "start": 12.5, "end": 30.0}],
                 "counts": {"people_in": 12, ...}   # optional manual counts
                }]}

Matching follows the "detect the *beginning* of the incident" idea from the perimeter-intrusion survey
(Lohani et al., Sensors 2022):
* an alarm of the right type within [start - pre_tolerance, start + tolerance] seconds of a GT event
  start is a true positive (the first one only; later alarms inside the same GT interval are ignored
  as duplicates, alarms outside every GT interval are false positives);
* a GT event with no matching alarm is a miss;
* time-to-alert = alarm time - GT start (can be slightly negative when the rule fires as the object
  reaches the boundary).
For clip-level ground truth (no intervals) a positive clip counts as detected if it produced >= 1 alarm.

Videos without "zones" get a full-frame intrusion zone active at all times (a presence baseline).
Detection metrics (mAP) are deliberately NOT mixed into these numbers.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "inference"))
from common import dump, register_perf_run  # noqa: E402
from offline_run import load_zones, model_specs, run  # noqa: E402

from app.analytics.policy import PolicySpec  # noqa: E402
from app.analytics.rules import ZoneSpec  # noqa: E402


def full_frame_zone(event_type: str) -> list[ZoneSpec]:
    zt = "restricted" if event_type == "RESTRICTED_AREA_ACCESS" else "intrusion"
    return [ZoneSpec(1, 0, "full frame", zt, "polygon", [[0, 0], [1, 0], [1, 1], [0, 1]], "high", "allow",
                     {"require_entry_transition": False, "min_persistence": 3},
                     [PolicySpec(1, ["person"], [], "alert", name="anyone in view")])]


def match(alarms: list[float], gt: list[dict], tol: float, pre_tol: float) -> dict:
    used, tp, ttas, dup = set(), 0, [], 0
    for g in gt:
        hits = [a for a in alarms if g["start"] - pre_tol <= a <= g["start"] + tol and a not in used]
        if hits:
            tp += 1
            used.add(hits[0])
            ttas.append(hits[0] - g["start"])
    fp = 0
    for a in alarms:
        if a in used:
            continue
        if any(g["start"] - pre_tol <= a <= g["end"] + tol for g in gt):
            dup += 1  # another alarm during an already-detected incident
        else:
            fp += 1
    return {"tp": tp, "fn": len(gt) - tp, "fp": fp, "duplicates": dup, "tta": ttas}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gt", required=True)
    ap.add_argument("--fps", type=float, default=5.0)
    ap.add_argument("--profile", default="shared", choices=["shared", "specialized"])
    ap.add_argument("--weights", nargs="*")
    ap.add_argument("--config", help="AnalyticsConfig overrides JSON")
    ap.add_argument("--tolerance", type=float, default=10.0, help="seconds after GT start an alarm may come")
    ap.add_argument("--pre-tolerance", type=float, default=2.0)
    ap.add_argument("--max-seconds", type=float)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--out", default="runs/eval_events.json")
    ap.add_argument("--register", action="store_true")
    a = ap.parse_args()
    gt = json.loads(Path(a.gt).read_text())
    specs = model_specs(a.profile, a.weights)
    cfg = json.loads(Path(a.config).read_text()) if a.config else {}
    from app.analytics.detector import Detector
    det = Detector(specs, a.device).load()
    per_video, tot = [], {"tp": 0, "fn": 0, "fp": 0, "duplicates": 0, "tta": [], "hours": 0.0,
                          "clip_tp": 0, "clip_fn": 0, "clip_fp": 0, "clip_tn": 0}
    count_err = []
    for v in gt["videos"]:
        etype = v.get("event_type") or (v["events"][0]["type"] if v.get("events") else "INTRUSION_DETECTED")
        zones = load_zones(v["zones"]) if v.get("zones") and Path(v["zones"]).exists() else full_frame_zone(etype)
        clock = datetime.fromisoformat(v["clock"]) if v.get("clock") else None
        res = run(v["video"], zones, a.fps, specs, cfg, clock, detector=det, max_seconds=a.max_seconds)
        alarms = sorted(e["ts"] for e in res["events"] if e["kind"] == "new" and e["type"] == etype)
        row = {"video": v["video"], "label": v.get("label"), "alarms": len(alarms), "duration_s": res["duration_s"],
               "counts": res["counts"]}
        tot["hours"] += res["duration_s"] / 3600
        if v.get("events"):
            m = match(alarms, v["events"], a.tolerance, a.pre_tolerance)
            row.update({k: m[k] for k in ("tp", "fn", "fp", "duplicates")})
            for k in ("tp", "fn", "fp", "duplicates"):
                tot[k] += m[k]
            tot["tta"] += m["tta"]
        else:  # clip-level
            positive = v.get("label") == "positive"
            if positive:
                tot["clip_tp" if alarms else "clip_fn"] += 1
            else:
                tot["clip_fp" if alarms else "clip_tn"] += 1
                tot["fp"] += len(alarms)
        if v.get("counts"):
            for k, want in v["counts"].items():
                if want is not None and k in res["counts"]:
                    count_err.append(abs(res["counts"][k] - want))
        per_video.append(row)
        print(f"{Path(v['video']).name}: alarms={len(alarms)} {({k: row.get(k) for k in ('tp', 'fn', 'fp')})}")

    def ratio(x, y):
        return round(x / y, 4) if y else None

    tp, fp, fn = tot["tp"] + tot["clip_tp"], tot["fp"], tot["fn"] + tot["clip_fn"]
    summary = {
        "event_precision": ratio(tp, tp + fp), "event_recall": ratio(tp, tp + fn),
        "f1": ratio(2 * tp, 2 * tp + fp + fn), "true_positives": tp, "false_positives": fp, "missed": fn,
        "duplicate_alarms_suppressed_or_extra": tot["duplicates"],
        "false_alarms_per_hour": ratio(fp, tot["hours"]),
        "mean_time_to_alert_s": round(sum(tot["tta"]) / len(tot["tta"]), 2) if tot["tta"] else None,
        "clip_level": {k: tot[k] for k in ("clip_tp", "clip_fn", "clip_fp", "clip_tn")},
        "count_mae": round(sum(count_err) / len(count_err), 2) if count_err else None,
        "hours_evaluated": round(tot["hours"], 3), "videos": len(per_video),
    }
    report = {"ground_truth": a.gt, "fps": a.fps, "profile": a.profile, "tolerance_s": a.tolerance,
              "model": det.version, "device": det.device, "summary": summary, "per_video": per_video}
    dump(a.out, report)
    print(json.dumps(summary, indent=2))
    if a.register:
        register_perf_run(f"Event eval: {gt.get('name', Path(a.gt).stem)}", "event_eval",
                          {k: report[k] for k in ("ground_truth", "fps", "profile", "tolerance_s", "model", "device")},
                          summary)


if __name__ == "__main__":
    main()
