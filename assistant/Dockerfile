# Akwaaba: one small container. The SQLite database and voice cache live on a
# mounted volume at /data, so they survive restarts and redeploys.
FROM python:3.12-slim

# ffmpeg: voice notes. tesseract: hides names on register photos (fails closed without it).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

RUN useradd --create-home akwaaba && mkdir -p /data && chown akwaaba /data
USER akwaaba

ENV AKWAABA_DB=/data/akwaaba.db \
    USAGE_LOG=/data/usage_log.jsonl \
    PYTHONUNBUFFERED=1 \
    PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz',timeout=4)"
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
