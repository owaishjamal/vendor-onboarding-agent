# Single-image build: React frontend + FastAPI backend + OCR, one URL.
#
#   docker build -t vendor-onboarding .
#   docker run -p 8000:8000 vendor-onboarding
#   → open http://localhost:8000
#
# Runs fully offline by default (LLM_PROVIDER=offline), so no API key is needed
# for it to work. Set ANTHROPIC_API_KEY + LLM_PROVIDER=anthropic to use a model.

# ---- stage 1: build the frontend -----------------------------------------
FROM node:20-slim AS frontend
WORKDIR /fe
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build           # emits /fe/dist

# ---- stage 2: python runtime + OCR ---------------------------------------
FROM python:3.11-slim

# tesseract is the only system dependency — needed for the scanned-document
# OCR path. Without it, scanned docs degrade to "please resend"; everything
# else still works.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# App code + the built frontend
COPY . .
COPY --from=frontend /fe/dist frontend/dist

# Generate the reference data and render the sample documents into the image,
# so the seven demo cases work the instant the container starts.
RUN python scripts/build_fixtures.py

ENV LLM_PROVIDER=offline \
    CHECK_DELAY_MS=250 \
    PYTHONUNBUFFERED=1

EXPOSE 8000
# $PORT is honoured for platforms (Render, Railway, Fly) that inject it.
CMD ["sh", "-c", "uvicorn backend.app.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
