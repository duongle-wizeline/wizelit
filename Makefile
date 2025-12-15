.PHONY: run

setup:
	uv sync

run:
	uv run chainlit run main.py --port 9191

run-refactoring-agent:
	uv run python -m mcp_servers.refactoring-agent.main