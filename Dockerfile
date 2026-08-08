# GVC Invoice Service — Cloud Run container
#
# Build:  docker build -t gvc-invoice .
# Run:    docker run -p 8080:8080 --env-file .env gvc-invoice
# Deploy: see docs/cloud-run-deploy.md
FROM python:3.12-slim-bookworm

# WeasyPrint system dependencies (Pango/Cairo/GDK-Pixbuf + fonts).
# Pinned to the slim variants — the resulting image is ~350MB.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz-subset0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    shared-mime-info \
    fonts-liberation \
    fonts-dejavu \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (Docker layer cache: deps change rarely, code often)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Application code (package layout, 2026-06) + templates + static assets.
# Organized into app/ (FastAPI routes), orchestrators/ (cross-system flows),
# subsystems/ (domain logic), adapters/ (Stripe/Drive/Gmail/Monday/GCS/Vision/
# Slack), shared/ (paths, money, boards, errors, access, auth, store, activity).
# COPY whole packages — no more per-file list to keep in sync.
COPY app ./app
COPY orchestrators ./orchestrators
COPY subsystems ./subsystems
COPY adapters ./adapters
COPY shared ./shared
COPY templates ./templates
COPY web ./web
COPY assets ./assets
COPY content ./content

# Import smoke test at build time — if app.service or its transitive imports
# fail to load, fail the BUILD here with a real traceback rather than at
# runtime where uvicorn swallows the ImportError into a generic message.
RUN python -c "import app.service; print('service import OK')"

# Output and logs live under /tmp (Cloud Run gives us a writable /tmp).
# Secrets are mounted at runtime by Cloud Run from Secret Manager — see deploy doc.
# PYTHONPATH=/app is load-bearing: uvicorn does not add cwd to sys.path the
# way `python -c "import service"` does at build time, so without this the
# container starts and uvicorn dies with "Could not import module 'service'".
ENV GVC_OUTPUT_DIR=/tmp/output \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8080

EXPOSE 8080

# Cloud Run sets $PORT (default 8080). uvicorn listens on it.
# Single worker is fine — Cloud Run handles concurrency by spinning up containers.
# DEBUG: print the real Python import error before uvicorn swallows it. The
# `python -c "import service"` step surfaces the actual ImportError /
# traceback to Cloud Run logs; if it succeeds, uvicorn takes over. Remove
# the diagnostic prefix once the startup issue is resolved.
CMD python -c "import app.service; print('[startup-debug] service imported OK')" && \
    exec uvicorn app.service:app --host 0.0.0.0 --port ${PORT} --workers 1
