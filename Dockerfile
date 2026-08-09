# Single-image build: React frontend + FastAPI backend + OCR, one URL.
#
#   docker build -t vendor-onboarding .
#   docker run -p 8000:8000 vendor-onboarding
#   → open http://localhost:8000
#
# Runs with no API key at all: all nine checks and the verdict are
# deterministic. Pass GEMINI_API_KEY to additionally get AI-written vendor
# emails, reviewer summaries and open-ended copilot answers — the provider is
# inferred from the key, so nothing else needs setting.
#
#   docker build -t vendor-onboarding .
#   docker run -p 8000:8000 -e GEMINI_API_KEY=... vendor-onboarding

# ---- stage 1: build the frontend -----------------------------------------
FROM node:20-slim AS frontend
WORKDIR /fe

# The browser bundle needs the API key at BUILD time (Vite inlines it), while
# the server reads it at RUN time. Deriving both from this one argument is the
# only way they cannot drift — set them separately and the UI 401s against its
# own backend, which is a miserable thing to debug on a fresh deploy.
#
# Note this key is visible in the shipped JavaScript. It deters casual scripted
# abuse of the public endpoints; it is NOT authentication. Put SSO in front of
# the ops routes for anything real.
ARG APP_API_KEY=dev_secret
ENV VITE_API_KEY=$APP_API_KEY

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

# Same value the bundle was built with, so the two always agree.
ARG APP_API_KEY=dev_secret
ENV API_KEY=$APP_API_KEY

# No LLM_PROVIDER here on purpose. The app infers the provider from whichever
# key is present, so adding GEMINI_API_KEY is enough to switch it on. Pinning
# `offline` here would silently override that and leave a deployer wondering
# why their key does nothing.
ENV CHECK_DELAY_MS=250 \
    PYTHONUNBUFFERED=1

EXPOSE 8000
# $PORT is honoured for platforms (Render, Railway, Fly) that inject it.
CMD ["sh", "-c", "uvicorn backend.app.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
