# Cold-chain visibility dashboard — FastAPI + static frontend.
# Universal image: runs on Render, Railway, Fly.io, Google Cloud Run, HF Spaces, etc.
FROM python:3.12-slim

WORKDIR /app

# Install deps first so they cache across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + static assets, the 90-day shipment CSV, and the cached Tive snapshot
# (so the live screen serves real, labelled data even with no live token).
COPY app ./app
COPY "mercyhealth_shipments_90d - mercyhealth_shipments_90d.csv.csv" ./

# Hosts inject $PORT; default to 8000 when run locally.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.zoomlogi.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
