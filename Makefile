.PHONY: run

setup:
	uv sync

init-streaming:
	uv run python scripts/init_streaming.py

monitor-redis:
	uv run python scripts/monitor_redis.py

docker-up:
	@if command -v docker-compose >/dev/null 2>&1; then \
		docker-compose up -d; \
	else \
		docker compose up -d; \
	fi

docker-down:
	@if command -v docker-compose >/dev/null 2>&1; then \
		docker-compose down; \
	else \
		docker compose down; \
	fi

run:
	uv run chainlit run main.py --port 9191
