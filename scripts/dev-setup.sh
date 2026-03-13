#!/usr/bin/env bash
# One-shot local development setup script
# Run once after cloning the repo: bash scripts/dev-setup.sh

set -euo pipefail

echo "=== [VCS Gateway] Dev Setup ==="

# 1. Check uv is installed
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source "$HOME/.cargo/env"
fi

# 2. Install Python dependencies
echo "Installing dependencies..."
uv sync

# 3. Copy env template if .env.local doesn't exist
if [ ! -f .env.local ]; then
    cp .env.template .env.local
    echo ".env.local created from template — fill in your values"
fi

# 4. Install pre-commit hooks
echo "Installing pre-commit hooks..."
uv run pre-commit install

# 5. Start infrastructure
echo "Starting infrastructure (PostgreSQL, RabbitMQ, Redis)..."
docker compose up -d postgres rabbitmq redis

# Wait for services to be healthy
echo "Waiting for services to be ready..."
sleep 5

# 6. Run DB migrations
echo "Running database migrations..."
uv run alembic upgrade head

echo ""
echo "=== Setup complete ==="
echo "Run API:    uv run uvicorn vcs_gateway.main:app --reload"
echo "Run worker: uv run python -m vcs_gateway.worker"
echo "Run tests:  uv run pytest"
