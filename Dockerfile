FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OTOMASYON_DB=/app/data/otomasyon.db

WORKDIR /app

RUN addgroup --system otomasyon && adduser --system --ingroup otomasyon otomasyon

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY otomasyon ./otomasyon
COPY README.md .

RUN mkdir -p /app/data && chown -R otomasyon:otomasyon /app
USER otomasyon

EXPOSE 8080
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "2", "--access-logfile", "-", "otomasyon.web:app"]
