"""Runtime orchestration for the Chainlit agent graph."""

from __future__ import annotations

import asyncio
import logging
from typing import List

from langgraph.graph.state import CompiledStateGraph

from graph import build_graph
from llm import llm
from services.mcp_client import MCPClient
from services.mcp_tooling import MCPToolRegistry, MCPToolingError

logger = logging.getLogger(__name__)

class AgentRuntime:
    """Lazily compiles the LangGraph agent and refreshes tools when needed."""

    def __init__(self, tool_registry: MCPToolRegistry) -> None:
        self._tool_registry = tool_registry
        self._graph: CompiledStateGraph | None = None
        self._lock = asyncio.Lock()

    async def ensure_ready(self) -> None:
        """Warm up the registry and compile the graph at least once."""

        await self._tool_registry.warm()
        try:
            await self.get_graph()
        except MCPToolingError:
            logger.warning("Agent graph initialized without MCP tools.")

    async def get_graph(self) -> CompiledStateGraph:
        """Return a compiled LangGraph, building it on first access."""

        if self._graph:
            return self._graph

        async with self._lock:
            if self._graph:
                return self._graph

            tools = await self._load_tools()
            self._graph = build_graph(llm, tools)

            return self._graph

    async def refresh(self) -> CompiledStateGraph:
        """Force a tool refresh and rebuild the graph."""

        async with self._lock:
            tools = await self._tool_registry.refresh()
            self._graph = build_graph(llm, tools)
            return self._graph

    async def graph_to_mermaid(self) -> str:
        """Convert the graph to a Mermaid string."""
        computed_graph = await self.get_graph()
        return computed_graph.get_graph().draw_mermaid()

    async def _load_tools(self) -> List:
        try:
            return await self._tool_registry.get_tools()
        except MCPToolingError as exc:
            logger.warning("Falling back to tool-less execution: %s", exc)
            return []


# Singleton-style runtime shared across the Chainlit app.
_mcp_client = MCPClient()
_tool_registry = MCPToolRegistry(_mcp_client)
agent_runtime = AgentRuntime(_tool_registry)
