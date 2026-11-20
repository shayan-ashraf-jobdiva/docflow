FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DOCFLOW_DATA=/data

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app app
COPY static static
COPY samples samples

# Run as a normal user (id 1000), not root. /data holds the database and uploads.
RUN useradd --uid 1000 --create-home app \
    && mkdir -p /data \
    && chown app:app /data
USER app

VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"

# One process only: the queue, cache and worker live inside it.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
