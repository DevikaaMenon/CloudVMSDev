# One image for both the API and the camera workers.
#   docker build -f infrastructure/docker/Dockerfile -t cloud-vms .
# GPU hosts: build with --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu124 and run with --gpus all

# ---------- frontend
FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- backend
FROM python:3.11-slim
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 VMS_ENVIRONMENT=production
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir torch torchvision --index-url ${TORCH_INDEX} \
    && pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY config/ config/
COPY ml/ ml/
COPY scripts/ scripts/
COPY --from=web /web/dist frontend/dist
RUN useradd -m vms && mkdir -p /app/data && chown -R vms /app/data
USER vms
RUN python ml/datasets/download_models.py --yolo26 n
WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fs http://localhost:8000/api/health || exit 1
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
