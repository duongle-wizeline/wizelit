"""Thin MCP client wrapper tailored for LangGraph agents."""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)


class MCPClientError(RuntimeError):
    """Domain-specific error raised when the MCP client cannot fulfill a request."""


class MCPClient:
    """Async helper that hides stream/session boilerplate for MCP HTTP servers."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        raw_timeout = timeout_seconds
        if raw_timeout is None:
            raw_timeout = float(os.getenv("MCP_HTTP_TIMEOUT", "30"))

        url = base_url or os.getenv("MCP_SERVER_URL", "http://localhost:1337/mcp")
        if not isinstance(url, str) or not url:
            msg = "MCP server URL must be a non-empty string."
            raise ValueError(msg)

        self._base_url = url.rstrip("/")
        self._timeout = float(raw_timeout)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def timeout(self) -> float:
        return self._timeout

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ClientSession]:
        """Open a fully initialized MCP session."""

        try:
            async with streamablehttp_client(
                self._base_url,
                timeout=self._timeout,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to open MCP session", exc_info=exc)
            raise MCPClientError(
                f"Unable to reach MCP server at {self._base_url}: {exc}"
            ) from exc

    async def list_tools(self) -> list[types.Tool]:
        """Return the available tool definitions."""

        async with self.session() as session:
            response = await session.list_tools()
            return list(response.tools)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> types.CallToolResult:
        """Invoke a tool on the MCP server."""

        payload = arguments or None
        async with self.session() as session:
            return await session.call_tool(name, payload)


def format_tool_result(result: types.CallToolResult) -> str:
    """Flatten structured MCP result into a concise string for LLM context."""

    blocks = [_render_content_block(block) for block in result.content]

    if result.structuredContent:
        structured = json.dumps(
            result.structuredContent,
            indent=2,
            ensure_ascii=False,
        )
        blocks.append(structured)

    cleaned = "\n\n".join(part for part in blocks if part)
    if not cleaned:
        cleaned = "Tool executed successfully but did not return any content."

    if result.isError:
        return f"[mcp:error]\n{cleaned}"

    return cleaned


def _render_content_block(block: types.ToolResultContent) -> str:
    if isinstance(block, types.TextContent):
        return block.text.strip()

    if isinstance(block, types.ResourceLink):
        suffix = f" — {block.description.strip()}" if block.description else ""
        return f"{block.uri}{suffix}"

    if isinstance(block, types.EmbeddedResource):
        data = block.resource.model_dump(mode="json")
        return json.dumps(data, ensure_ascii=False)

    if isinstance(block, types.ImageContent):
        desc = block.annotations.title if block.annotations else ""
        return f"[image]{' ' + desc if desc else ''}"

    if isinstance(block, types.AudioContent):
        desc = block.annotations.title if block.annotations else ""
        return f"[audio]{' ' + desc if desc else ''}"

    # Fallback to raw JSON to preserve information.
    return json.dumps(block.model_dump(mode="json"), ensure_ascii=False)
