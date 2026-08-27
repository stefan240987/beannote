FROM python:3.12-slim

LABEL org.opencontainers.image.title="BeanNote"
LABEL org.opencontainers.image.description="Personal coffee journal and rating companion"
LABEL org.opencontainers.image.source="https://github.com/stefan240987/beannote"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ENVIRONMENT=production \
    TZ=Europe/Copenhagen \
    PUID=99 \
    PGID=100

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-dan \
        curl \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY db.py ocr.py image_search.py translations.py main.py deps.py schemas.py jobs.py worker.py gear_catalog.json entrypoint.sh ./
COPY routes ./routes
COPY static ./static

RUN chmod +x /app/entrypoint.sh && mkdir -p /app/data

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=25s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8501/api/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
