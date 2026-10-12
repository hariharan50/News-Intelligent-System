# One image runs every Python service: api, migrate, scheduler and worker.
# docker-compose.yml picks the command for each.
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

RUN useradd --system --uid 1001 --no-create-home app
USER app

EXPOSE 8000
# The API is only reachable through nginx (and localhost), so trust its X-Forwarded-For:
# login rate limits and the device list then see the real client IP.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
