FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/securerag \
    HF_HOME=/var/cache/huggingface \
    HF_HUB_CACHE=/var/cache/huggingface/hub \
    SENTENCE_TRANSFORMERS_HOME=/var/cache/huggingface \
    XDG_CACHE_HOME=/var/cache/huggingface/.cache \
    TORCH_HOME=/var/cache/huggingface/torch \
    TMPDIR=/tmp
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt \
    && groupadd --gid 10001 securerag \
    && useradd --uid 10001 --gid 10001 --home-dir /home/securerag --create-home \
        --no-user-group --shell /usr/sbin/nologin securerag \
    && install -d --owner=10001 --group=10001 --mode=0750 \
        /var/lib/securerag/documents /var/cache/huggingface /home/securerag
COPY backend/app ./app
COPY backend/alembic ./backend/alembic
COPY alembic.ini ./alembic.ini
EXPOSE 8000
USER 10001:10001
ENTRYPOINT ["uvicorn"]
CMD ["app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-graceful-shutdown", "30"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; prefix='/' + os.environ.get('API_V1_PREFIX', '/api/v1').strip('/'); prefix=prefix if prefix != '/' else '/api/v1'; urllib.request.urlopen('http://127.0.0.1:8000' + prefix + '/health', timeout=4)"]
