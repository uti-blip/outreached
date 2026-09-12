FROM ghcr.io/astral-sh/uv:0.11.3 AS uv
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    APP_ENV=production WORKSPACE_DB_PATH=/data/workspace.db
WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend ./backend
COPY scripts/serve.py scripts/workspace_backup.py ./scripts/
RUN uv sync --frozen --no-dev && \
    groupadd --gid 10001 outreached && \
    useradd --uid 10001 --gid 10001 --no-create-home outreached && \
    mkdir -p /data && chown 10001:10001 /data

# The entrypoint fixes only the data volume ownership, then drops root before
# importing the application. Railway mounts volumes owned by root.
EXPOSE 8001
CMD ["python", "scripts/serve.py"]
