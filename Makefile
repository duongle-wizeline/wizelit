.PHONY: run

setup:
	uv sync

run:
	uv run chainlit run main.py -w --port 9191
