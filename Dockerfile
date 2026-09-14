# Backend image — FastAPI/Uvicorn API only. The frontend is built and served
# by its own image (frontend/Dockerfile); see docker-compose.yml for how the
# two are wired together, and README.md for how to run without Docker at all.

FROM python:3.11-slim

# LANA_DATA_DIR default ("data") is relative to the working directory, which
# without this would be wherever `python` happens to be invoked from — inside
# a container that is deliberately fixed, not incidental.
WORKDIR /app

# Build layer caching: dependencies change far less often than application
# code, so they get their own layer and are not invalidated by a code edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY backend/ ./backend/

# Runs as an unprivileged user — this process parses uploaded files from
# strangers (or at least from the network, once exposed beyond one laptop),
# and root is not a privilege it needs for that.
RUN useradd --create-home --uid 1000 lana \
    && mkdir -p /data \
    && chown -R lana:lana /app /data
USER lana

ENV LANA_DATA_DIR=/data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
