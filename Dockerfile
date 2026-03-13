# Multi-stage build — production image
# Stage 1: builder (installs deps with uv)
# Stage 2: runtime (minimal image, no build tools)

# ---------------------------------------------------------------------------
# Stage 1: builder
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first (better layer caching)
COPY pyproject.toml ./
COPY src/ ./src/

# Install production dependencies only (no dev)
RUN uv sync --no-dev --frozen

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

WORKDIR /app

# Create non-root user
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

# Copy installed packages from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

# Set PATH to use venv
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Switch to non-root user
USER appuser

EXPOSE 8000

# Default command — runs the API server
# Override with ["python", "-m", "vcs_gateway.worker"] for queue consumer
CMD ["uvicorn", "vcs_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
