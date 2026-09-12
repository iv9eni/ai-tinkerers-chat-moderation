FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    QUEUE_DB=/data/blackline.db \
    AUDIT_FILE=/data/audit.jsonl \
    POLICY_FILE=/app/policies/default.yaml \
    LOG_FORMAT=json \
    PORT=8080

WORKDIR /app
RUN pip install --no-cache-dir uv \
 && useradd --create-home --uid 10001 blackline \
 && mkdir -p /data && chown blackline:blackline /data

# dependencies first so code changes do not reinstall them
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system --no-cache .

COPY adapters ./adapters
COPY policies ./policies
COPY deploy/entrypoint.sh /entrypoint.sh

VOLUME ["/data"]
EXPOSE 8080
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
  CMD ["python", "-m", "adapters.healthcheck"]
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "adapters.slack_app"]
