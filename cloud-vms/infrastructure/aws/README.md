# Deploying Cloud VMS on AWS

The same code runs on a laptop and in AWS; only configuration changes. Start small and add managed
services when you need them.

```
Cameras (RTSP) ──► EC2 worker(s) ──► RDS PostgreSQL   (metadata, events, counts)
      │               │  AI + tracking + rules
      │               └──────────────► S3 bucket       (recordings, evidence clips, snapshots)
      └──► MediaMTX (optional, raw live view)
Browser ──► ALB / EC2 API (FastAPI + React UI) ──► RDS, S3 (presigned links), Redis (live preview)
Logs: CloudWatch Logs (awslogs driver)
```

## 1. Storage and database

1. **S3**: create a private bucket, e.g. `rvce-vms-<yourname>`, in `ap-south-1` (Mumbai).
   Keep *Block all public access* on. Evidence is shared only through presigned URLs that expire
   after `VMS_MEDIA_URL_TTL_SECONDS` (15 minutes by default).
   Optional lifecycle rule: expire `recordings/` after 7 days (the app also deletes on its own).
2. **RDS PostgreSQL 16**: `db.t4g.micro` is enough for a project. Put it in the same VPC as the
   EC2 instance and allow port 5432 only from the instance's security group.
3. **IAM**: create a role for the EC2 instance with `iam-policy.json` from this folder (replace the
   bucket name). No access keys are needed on the instance.

## 2. Compute

| Workload | Instance | Notes |
|---|---|---|
| API + a few cameras (CPU) | `t3.large` / `c6i.xlarge` | Run the scaling benchmark first |
| Many cameras / bigger models | `g4dn.xlarge` (T4 GPU) | Build the image with the CUDA torch index |

Do not assume a GPU is needed: run `ml/evaluation/benchmark_scaling.py` on the instance type
you plan to use and keep the numbers for the report.

## 3. Run

```bash
# on the EC2 instance (Amazon Linux 2023 / Ubuntu) with Docker installed
git clone <your repo> && cd cloud-vms
docker build -f infrastructure/docker/Dockerfile -t cloud-vms .

cat > vms.env <<'EOF'
VMS_ENVIRONMENT=production
VMS_DATABASE_URL=postgresql+psycopg://vms:<password>@<rds-endpoint>:5432/vms
VMS_STORAGE_BACKEND=s3
VMS_S3_BUCKET=rvce-vms-<yourname>
VMS_S3_REGION=ap-south-1
VMS_SECRET_KEY=<64 random characters>
VMS_ADMIN_PASSWORD=<strong password>
VMS_TIMEZONE=Asia/Kolkata
VMS_CORS_ORIGINS=["https://<your-domain>"]
EOF

# single instance: API with embedded workers
docker run -d --name vms --restart unless-stopped --env-file vms.env -p 80:8000 \
  --log-driver=awslogs --log-opt awslogs-region=ap-south-1 --log-opt awslogs-group=/cloud-vms/api \
  --log-opt awslogs-create-group=true cloud-vms
```

### Scaling out

* Run the API with `VMS_EMBEDDED_WORKERS=false` and one or more worker containers:
  `docker run ... cloud-vms python -m app.workers.run --group gate-cameras`
* Give each camera a `worker_group` so each worker machine serves a known set of cameras.
* Add ElastiCache Redis and set `VMS_REDIS_URL` so live preview frames reach the API.
* Put the API behind an Application Load Balancer with HTTPS (ACM certificate).

### What is deliberately *not* used yet

* **SQS**: the queue between capture and inference is in-process and bounded (drops the oldest
  frame instead of growing). Moving event/evidence jobs to SQS is the next step once several
  machines must share work; the event engine is already idempotent (unique `dedup_key`), which
  SQS's at-least-once delivery requires.
* **Kinesis Video Streams**: not needed while cameras reach the worker over RTSP directly.

## 4. Security checklist

- [ ] Change the admin password on first login; create operator / viewer accounts per person.
- [ ] Camera passwords are stored encrypted (`camera_credentials_reference`), never shown in the UI
      or logs (URLs are redacted).
- [ ] Bucket not public; instance role scoped to one bucket.
- [ ] RDS reachable only from the app security group; automated backups on.
- [ ] HTTPS in front of the API; `VMS_CORS_ORIGINS` limited to your domain.
- [ ] Audit log (Users & audit page) reviewed periodically.
