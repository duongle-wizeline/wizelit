#!/bin/bash
# Monitor Redis Pub/Sub messages for job streaming
# Usage: ./scripts/monitor_redis.sh [job_id]

JOB_PATTERN="${1:-job:*}"

echo "=========================================="
echo "🔍 Redis Pub/Sub Monitor"
echo "=========================================="
echo "Pattern: $JOB_PATTERN"
echo ""
echo "Listening for messages..."
echo "Press Ctrl+C to stop"
echo ""

docker exec -it wizelit-redis redis-cli PSUBSCRIBE "$JOB_PATTERN"
