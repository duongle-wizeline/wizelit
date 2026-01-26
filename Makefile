.PHONY: help build run setup init-streaming monitor-redis docker-up docker-down

.DEFAULT_GOAL := help

help:
	@echo "Wizelit Project - Available Commands"
	@echo "===================================="
	@echo ""
	@echo "Setup & Build:"
	@echo "  make setup              - Install dependencies using uv"
	@echo "  make build              - Run build checks and validate project"
	@echo ""
	@echo "Running:"
	@echo "  make run                - Start the Chainlit application on port 9191"
	@echo ""
	@echo "Development:"
	@echo "  make init-streaming     - Initialize streaming configuration"
	@echo "  make monitor-redis      - Monitor Redis activity"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-up          - Start Docker Compose services"
	@echo "  make docker-down        - Stop Docker Compose services"
	@echo ""
	@echo "Help:"
	@echo "  make help               - Display this help message"
	@echo ""

setup:
	uv sync

build:
	@echo "Running build checks..."
	@bash check_build.sh

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
