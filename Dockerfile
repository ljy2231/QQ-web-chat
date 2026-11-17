FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CHAT_DATA_DIR=/data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 webchat \
    && useradd --uid 10001 --gid webchat --no-create-home webchat \
    && mkdir /data && chown webchat:webchat /data
COPY --chmod=644 server.py ./
COPY --chmod=755 webchat/ ./webchat/
COPY --chmod=755 templates/ ./templates/
COPY --chmod=755 static/ ./static/
COPY --chmod=755 tools/ ./tools/
USER 10001:10001
EXPOSE 4001
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4001/healthz', timeout=3)" || exit 1
CMD ["python", "server.py"]
