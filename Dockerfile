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
# JSON/exec form — handles the spaces in the CSV filename (shell form breaks on them).
COPY ["mercyhealth_shipments_90d - mercyhealth_shipments_90d.csv.csv", "./"]

# Hosts that inject $PORT (Render/Koyeb/etc.) use it; otherwise default to 7860,
# which Hugging Face Spaces expects — so it runs there with zero extra config.
EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.coldchain.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
