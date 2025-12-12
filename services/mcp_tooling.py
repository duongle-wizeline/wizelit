"""Utilities to convert MCP tools into LangChain-compatible tools."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Optional

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, create_model
from typing_extensions import Literal

from services.mcp_client import MCPClient, MCPClientError, format_tool_result

logger = logging.getLogger(__name__)


class MCPToolingError(RuntimeError):
    """Raised when the remote MCP tools cannot be materialized."""


class MCPToolRegistry:
    """Caches MCP tools and exposes them as LangChain tools."""

    def __init__(
        self,
        client: MCPClient,
        *,
        refresh_ttl_seconds: int = 300,
    ) -> None:
        self._client = client
        self._ttl = refresh_ttl_seconds
        self._tools: list[BaseTool] | None = None
        self._loaded_at: float | None = None
        self._lock = asyncio.Lock()

    async def get_tools(self, *, force_refresh: bool = False) -> list[BaseTool]:
        """Return cached tools, refreshing from the server when needed."""

        if not force_refresh and self._tools and not self._is_stale():
            return self._tools

        async with self._lock:
            if not force_refresh and self._tools and not self._is_stale():
                return self._tools

            try:
                definitions = await self._client.list_tools()
            except MCPClientError as exc:
                raise MCPToolingError(str(exc)) from exc

            builder = _MCPToolBuilder(self._client)
            self._tools = [builder.build_tool(defn) for defn in definitions]
            self._loaded_at = time.monotonic()
            logger.info(
                "Loaded %s MCP tool(s) from %s",
                len(self._tools),
                self._client.base_url,
            )
            return self._tools

    async def refresh(self) -> list[BaseTool]:
        """Force refresh of the underlying cache."""

        return await self.get_tools(force_refresh=True)

    async def warm(self) -> None:
        """Attempt to populate the cache eagerly without failing the app."""

        try:
            await self.get_tools()
        except MCPToolingError as exc:
            logger.warning("Skipping MCP warm-up: %s", exc)

    def _is_stale(self) -> bool:
        if self._loaded_at is None:
            return True
        return (time.monotonic() - self._loaded_at) > self._ttl


class _MCPToolBuilder:
    def __init__(self, client: MCPClient) -> None:
        self._client = client

    def build_tool(self, tool: Any) -> BaseTool:
        """Convert an MCP tool definition into a LangChain StructuredTool."""

        args_schema = self._build_args_schema(tool)
        description = tool.description or tool.title or f"MCP tool '{tool.name}'"

        async def _invoke(**kwargs: Any) -> str:
            result = await self._client.call_tool(tool.name, kwargs or None)
            return format_tool_result(result)

        return StructuredTool.from_function(
            func=None,
            coroutine=_invoke,
            name=tool.name,
            description=description,
            args_schema=args_schema,
            infer_schema=False,
        )

    def _build_args_schema(self, tool: Any) -> type[BaseModel]:
        schema = tool.inputSchema or {}
        model_name = _to_pascal_case(tool.name) + "Args"

        if schema.get("type") != "object":
            field_type = _json_schema_to_type(schema)
            return create_model(
                model_name,
                payload=(field_type, Field(..., description="Raw MCP input.")),
            )

        properties = schema.get("properties", {}) or {}
        required = set(schema.get("required") or [])

        if not properties:
            return create_model(model_name)

        fields: dict[str, tuple[Any, Field]] = {}
        for prop_name, prop_schema in properties.items():
            py_type = _json_schema_to_type(prop_schema)
            default: Any = ...
            if prop_name not in required:
                default = prop_schema.get("default", None)

            description = prop_schema.get("description")
            fields[prop_name] = (
                py_type,
                Field(
                    default,
                    description=description,
                )
                if default is not ...
                else Field(..., description=description),
            )

        return create_model(model_name, **fields)


def _json_schema_to_type(schema: dict[str, Any]) -> Any:
    schema_type = schema.get("type")
    optional = False

    if isinstance(schema_type, list):
        if "null" in schema_type:
            optional = True
            schema_type = [t for t in schema_type if t != "null"]
        schema_type = schema_type[0] if schema_type else None

    if "enum" in schema:
        literal = Literal[tuple(schema["enum"])]  # type: ignore[misc]
        base_type: Any = literal
    elif schema_type == "string":
        base_type = str
    elif schema_type == "integer":
        base_type = int
    elif schema_type == "number":
        base_type = float
    elif schema_type == "boolean":
        base_type = bool
    elif schema_type == "array":
        item_type = _json_schema_to_type(schema.get("items", {}))
        base_type = list[item_type]
    elif schema_type == "object":
        base_type = dict[str, Any]
    else:
        base_type = Any

    if optional:
        return Optional[base_type]
    return base_type


def _to_pascal_case(name: str) -> str:
    tokens = re.split(r"[^0-9a-zA-Z]+", name)
    cleaned = "".join(token.capitalize() for token in tokens if token)
    return cleaned or "Tool"
