# ML toolkit

Everything here runs the **same analytics code as the live system** (`backend/app/analytics`),
so numbers measured offline match what the app does.

| Folder | What it is for |
|---|---|
| `datasets/` | download pretrained weights; convert public datasets to one canonical format |
| `college/` | bring in your own gate footage: extract frames, pre-label, validate, split by video |
| `training/` | reproducible fine-tuning (local, Kaggle, Colab or EC2) |
| `inference/` | run the full pipeline on a video file without the web app |
| `evaluation/` | detection metrics, incident metrics, scalability benchmark |

## Canonical classes

Every dataset is converted to these ids so any model can be swapped in:

`0 person, 1 bicycle, 2 two_wheeler, 3 three_wheeler, 4 car, 5 van, 6 lcv, 7 bus, 8 truck`

## Which data for which requirement

| Requirement | Pretrained model | Data to fine-tune / evaluate | Script |
|---|---|---|---|
| Person detection | YOLO26 (COCO) | CrowdHuman (crowded scenes) | `datasets/prepare_crowdhuman.py` |
| Vehicle detection | UVH-26 YOLOv11 (Bengaluru CCTV) | UVH-26, Kaggle Indian traffic | `datasets/prepare_coco_json.py`, `datasets/prepare_yolo_dataset.py` |
| Intrusion | rules on tracks (no extra model) | UCF-Crime Burglary/Stealing/Vandalism, Kaggle Burglary & Vandalism | `datasets/prepare_ucf_crime.py`, `datasets/prepare_clip_dataset.py` |
| Restricted-area access | rules on tracks (no extra model) | VIRAT ground videos (entering facilities) | `datasets/prepare_virat.py` |
| Everything, on campus | your fine-tuned model | college gate videos | `college/import_college.py` |

Intrusion and restricted access are *spatial and temporal rules on tracked objects*; the datasets
listed for them are used to **evaluate and tune** the rules (persistence, cooldown, schedules),
not to train another heavy video model. A learned incident classifier should only be added if
these evaluations show rules can't express a requirement.

## Typical workflow (Windows commands; activate `.venv` first)

```bat
:: 1. baseline on your own footage (no training) — counts, incidents and speed
python ml\inference\offline_run.py --video D:\college\gate_morning.mp4 --zones my_zones.json --fps 5 --annotate runs\gate.mp4 --out runs\gate.json

:: 2. build ground truth for a few college clips and measure incident accuracy
python ml\college\import_college.py events-template --videos D:\college --out data\eval\college_events.json
::    (edit the JSON: real incident start/end seconds and manual counts)
python ml\evaluation\eval_events.py --gt data\eval\college_events.json --fps 5 --register

:: 3. label frames and fine-tune when the baseline misses things
python ml\college\import_college.py frames --videos D:\college --out data\datasets\college_raw --fps 1
python ml\college\import_college.py autolabel --images data\datasets\college_raw
::    correct the boxes in CVAT / Label Studio / Roboflow, export YOLO format, then:
python ml\college\import_college.py labelled --src D:\export --out data\datasets\college_v1
python ml\training\train.py --data data\datasets\college_v1\data.yaml --model data\weights\yolo26n.pt --epochs 50

:: 4. compare old vs new model on the held-out college videos
python ml\evaluation\eval_detection.py --data data\datasets\college_v1\data.yaml --models data\weights\yolo26n.pt runs\train\weights\best.pt --register

:: 5. how many cameras can this machine handle?
python ml\evaluation\benchmark_scaling.py --video data\uploads\demo_gate.mp4 --cameras 1 2 5 10 --seconds 60 --register
```

Results passed `--register` show up on the app's **System health** page.

## Training remotely (Kaggle / Colab)

Upload `ml/training/train.py` and your prepared dataset (or attach the Kaggle dataset), then:

```bash
pip install -U ultralytics
python train.py --data /kaggle/working/college_v1/data.yaml --model yolo26n.pt --epochs 80 --batch 32 --device 0 --project /kaggle/working/runs --name college_v1
```

Download only `runs/college_v1/weights/best.pt` (a few MB) and upload it on the
**Models & data** page. The full dataset never has to live on your laptop.

## Reporting rules

* Report **detection** metrics (precision, recall, mAP) and **incident** metrics (event precision /
  recall, false alarms per hour, time to alert) separately.
* Always state dataset, split, image size, hardware and model version (the scripts record these).
* Split by source video, never by frame.
* A demo on sample videos is not proof of production scalability — use the benchmark numbers.
