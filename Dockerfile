FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY sable_platform/ sable_platform/

RUN pip install --no-cache-dir ".[postgres]" \
    && groupadd -r sable && useradd -r -g sable -d /app sable \
    && mkdir -p /data && chown sable:sable /data

ENV SABLE_DB_PATH=/data/sable.db

USER sable

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
    CMD ["sable-platform", "db-health", "--json"]

ENTRYPOINT ["sable-platform"]
CMD ["--help"]
