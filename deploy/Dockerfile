# Multi-stage build: compile the dashboard, then assemble the server image
# that serves both the API and the built SPA.

# --- Stage 1: build the dashboard SPA ---
FROM node:22-slim AS dashboard
WORKDIR /build
COPY apps/dashboard/package.json apps/dashboard/package-lock.json ./
RUN npm ci
COPY apps/dashboard/ ./
RUN npm run build

# --- Stage 2: server runtime ---
FROM python:3.12-slim AS server
# uv provides fast, reproducible installs from the workspace.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    TOKEMETRY_STATIC_DIR=/app/static

# Install dependencies first (cached until the lockfile changes).
COPY pyproject.toml uv.lock ./
COPY packages/core/pyproject.toml packages/core/
COPY apps/server/pyproject.toml apps/server/
COPY apps/collector/pyproject.toml apps/collector/
RUN uv sync --frozen --no-install-workspace --package tokemetry-server

# Copy the source and install the workspace packages.
COPY packages/core/ packages/core/
COPY apps/server/ apps/server/
RUN uv sync --frozen --package tokemetry-server

# Copy the built dashboard.
COPY --from=dashboard /build/dist /app/static

# Run as a non-root user; give it ownership of the app tree (venv + static).
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8787
# Bind to all interfaces inside the container; the host maps only the
# WireGuard address to the published port (see docker-compose.yml).
CMD ["uv", "run", "uvicorn", "tokemetry_server.app:create_app", \
     "--factory", "--host", "0.0.0.0", "--port", "8787"]
