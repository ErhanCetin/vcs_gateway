#!/usr/bin/env bash
# Check service health endpoints
# Usage: bash scripts/check-health.sh [port]

PORT=${1:-8000}
BASE_URL="http://localhost:${PORT}"

echo "=== Health Check: ${BASE_URL} ==="

echo -n "Liveness  (/health/live):  "
curl -sf "${BASE_URL}/health/live" | python3 -m json.tool 2>/dev/null || echo "FAILED"

echo -n "Readiness (/health/ready): "
curl -sf "${BASE_URL}/health/ready" | python3 -m json.tool 2>/dev/null || echo "FAILED"
