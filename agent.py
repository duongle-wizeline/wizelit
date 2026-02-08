import json
import os
import sys
import warnings
from typing import Any, Dict, Optional
from dotenv import load_dotenv

load_dotenv()

import asyncio
from contextlib import AsyncExitStack
from langchain_aws import ChatBedrock
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from langchain_mcp_adapters.tools import load_mcp_tools
from graph import build_graph
from utils.bedrock_config import normalize_aws_env, resolve_bedrock_model_id
from utils.mcp_storage import get_mcp_servers
from exceptions import (
    MCPConnectionError,
    MCPToolLoadError,
    GraphBuildError,
    ConfigurationError,
)

# Lock to prevent concurrent graph rebuilds
_rebuild_lock = asyncio.Lock()

# Suppress non-critical async generator cleanup warnings
# These occur when async generators are being closed and don't affect functionality
warnings.filterwarnings("ignore", message=".*async generator ignored GeneratorExit.*")

# Suppress "Exception ignored" messages from async generator cleanup
# These are printed directly to stderr by Python, not standard warnings
import io

# Create a custom stderr filter to suppress async generator cleanup messages
_original_stderr = sys.stderr


class FilteredStderr:
    """Filter stderr to suppress non-critical async generator cleanup messages"""

    def __init__(self, original_stderr: Any) -> None:
        self.original_stderr = original_stderr
        self._buffer = ""  # Buffer for multi-line messages

    def write(self, text: str) -> None:
        # Buffer text to handle multi-line exception messages
        self._buffer += text

        # Check if we have a complete exception message
        if "\n" in self._buffer:
            lines = self._buffer.split("\n")
            # Keep the last incomplete line in buffer
            self._buffer = lines[-1]
            # Process complete lines
            for line in lines[:-1]:
                if self._should_suppress(line):
                    continue
                self.original_stderr.write(line + "\n")
        # If no newline, keep buffering (will be flushed later)

    def _should_suppress(self, line: str) -> bool:
        """Check if a line should be suppressed"""
        # Suppress "Exception ignored" messages related to async generators
        if "Exception ignored in:" in line and "async_generator" in line:
            return True
        if "RuntimeError: async generator ignored GeneratorExit" in line:
            return True
        # Suppress "no running event loop" errors from httpx cleanup
        if "RuntimeError: no running event loop" in line:
            return True
        # Suppress traceback lines that are part of async generator cleanup
        if "Traceback (most recent call last):" in line:
            # Suppress all tracebacks from rebuild_graph and httpx cleanup
            return True
        if "File" in line and "agent.py" in line and "rebuild_graph" in line:
            # Suppress any line from rebuild_graph in agent.py
            return True
        # Suppress httpx cleanup traceback lines
        if "File" in line and (
            "httpx" in line or "httpcore" in line or "anyio" in line
        ):
            if "aclose" in line or "AsyncShieldCancellation" in line:
                return True
        # Suppress incomplete tracebacks from async generator cleanup
        if line.strip().startswith("File") and "agent.py" in line:
            return True
        # Suppress lines that are just "^" (pointing to the error location)
        if line.strip() and all(c in ("^", " ") for c in line.strip()):
            return True
        return False

    def flush(self) -> None:
        # Write any remaining buffered content
        if self._buffer:
            if not self._should_suppress(self._buffer):
                self.original_stderr.write(self._buffer)
            self._buffer = ""
        self.original_stderr.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.original_stderr, name)


# Only apply filter if not already applied
if not isinstance(sys.stderr, FilteredStderr):
    sys.stderr = FilteredStderr(sys.stderr)


class AgentRuntime:
    """
    Agent runtime that supports per-user graphs.
    Each user gets their own isolated graph with their own MCP server connections.
    """


    # Default user ID for backward compatibility
    DEFAULT_USER_ID = "__default__"


    def __init__(self) -> None:
        # Per-user storage: user_id -> data
        self._graphs: Dict[str, Any] = {}
        self._exit_stacks: Dict[str, AsyncExitStack] = {}
        self._sessions: Dict[str, Dict[str, Any]] = {}  # user_id -> {server_name: session}
        self._tool_sessions: Dict[str, Dict[str, Any]] = {}  # user_id -> {tool_name: session}

    async def ensure_ready(self, user_id: Optional[str] = None) -> None:
        uid = user_id or self.DEFAULT_USER_ID
        if uid in self._graphs and self._graphs[uid] is not None:
            return
        await self._rebuild_graph(user_id=uid)

    async def _rebuild_graph(self, user_id: Optional[str] = None) -> None:
        """Rebuild the graph with current tools from in-memory storage for a specific user"""
        uid = user_id or self.DEFAULT_USER_ID


        # Only rebuild if graph doesn't exist yet for this user
        # If graph exists, we should not rebuild it here - use rebuild_graph() explicitly
        if uid in self._graphs and self._graphs[uid] is not None:
            # Graph already exists for this user, don't rebuild
            return

        # Create new exit stack for this user if one doesn't exist
        if uid not in self._exit_stacks or self._exit_stacks[uid] is None:
            self._exit_stacks[uid] = AsyncExitStack()

        # Create local reference for type narrowing in nested functions
        exit_stack = self._exit_stacks[uid]

        # Reset sessions for this user
        self._sessions[uid] = {}
        self._tool_sessions[uid] = {}

        tools_all = []
        seen_tool_names = set()  # Track tool names to prevent duplicates

        async def connect_and_load(
            label: str, url: str, headers: Optional[Dict[str, str]] = None
        ):
            print(f"🔌 [Agent] Connecting to {label} at {url}")
            if headers:
                print(
                    f"🔑 [Agent] Using authentication headers: {list(headers.keys())}"
                )

            # Detect transport type based on URL path
            # Streamable-HTTP servers use /mcp endpoint (or /mcp-server/http for n8n), SSE servers use /sse endpoint
            is_streamable_http = (
                "/mcp" in url.lower()
                or url.lower().endswith("/mcp")
                or "/mcp-server" in url.lower()
            )
            is_sse = "/sse" in url.lower() or url.lower().endswith("/sse")

            # Use streamable-http transport if URL indicates it
            if is_streamable_http:
                pass  # Using streamable-http transport

                try:
                    # Use streamable-http client with headers if provided
                    streamable_http = await exit_stack.enter_async_context(
                        streamablehttp_client(url=url, headers=headers)
                    )
                    read_stream, write_stream, get_session_id = streamable_http
                    session = await exit_stack.enter_async_context(
                        ClientSession(read_stream, write_stream)
                    )
                    await session.initialize()
                except Exception as e:
                    raise MCPConnectionError(label, url, str(e))

            else:
                # Default to SSE connection for other servers
                if not is_sse:
                    pass  # Using SSE transport

                try:
                    sse = await exit_stack.enter_async_context(
                        sse_client(url=url, timeout=600.0)
                    )
                    read_stream, write_stream = sse
                    session = await exit_stack.enter_async_context(
                        ClientSession(read_stream, write_stream)
                    )
                    await session.initialize()
                except Exception as e:
                    raise MCPConnectionError(label, url, str(e))

            try:
                tools = await load_mcp_tools(session)
                if not tools:
                    raise MCPToolLoadError(
                        label, "Server connected but returned no tools"
                    )

                if "n8n" in label.lower():
                    print(f"ℹ️  [Agent] Detected N8N MCP server at {label}")
                    n8n_workflows = []
                    for tool in tools or []:
                        if tool.name == "search_workflows":
                            n8n_response = await tool.ainvoke({})
                            data = json.loads(n8n_response[0].get('text', '{}'))
                            n8n_workflows = data.get('structuredContent', {}).get('data', [])

                    if n8n_workflows:
                        tools.extend(n8n_workflows)
                        print(f"✅ [Agent] Loaded {len(n8n_workflows)} workflows from N8N MCP server at {label}")
            except Exception as e:
                if isinstance(e, MCPToolLoadError):
                    raise
                raise MCPToolLoadError(label, str(e))

            print(f"✅ [Agent] Tools Loaded from {label}: {[t.name for t in tools]}")

            # Add tools, skipping duplicates (keep first occurrence)
            for t in tools:
                if t.name in seen_tool_names:
                    print(
                        f"⚠️ [Agent] Duplicate tool name '{t.name}' from {label}. Skipping duplicate."
                    )
                    continue
                seen_tool_names.add(t.name)
                tools_all.append(t)
                # Track which session owns which tool name for direct calls
                self._tool_sessions[uid][t.name] = session

            # Keep session for potential future use
            self._sessions[uid][label] = session

        async def load_from_chainlit_session(label: str, chainlit_session):
            """Load tools from a Chainlit-managed session (for stdio-based servers)"""
            print(
                f"🔌 [Agent] Loading {label} from Chainlit session (stdio transport) ..."
            )
            try:
                tools = await load_mcp_tools(chainlit_session)
                if not tools:
                    raise MCPToolLoadError(
                        label, "Chainlit session connected but returned no tools"
                    )
                print(
                    f"✅ [Agent] Tools Loaded from {label}: {[t.name for t in tools]}"
                )

                if "n8n" in label.lower():
                    print(f"ℹ️  [Agent] Detected N8N MCP server at {label}")
                    n8n_workflows = []
                    for tool in tools or []:
                        if tool.name == "search_workflows":
                            n8n_response = await tool.ainvoke({})
                            data = json.loads(n8n_response[0].get('text', '{}'))
                            n8n_workflows = data.get('structuredContent', {}).get('data', [])

                    if n8n_workflows:
                        tools.extend(n8n_workflows)
                        print(f"✅ [Agent] Loaded {len(n8n_workflows)} workflows from N8N MCP server at {label}")

                # Add tools, skipping duplicates
                for t in tools:
                    if t.name in seen_tool_names:
                        print(
                            f"⚠️ [Agent] Duplicate tool name '{t.name}' from {label}. Skipping duplicate."
                        )
                        continue
                    seen_tool_names.add(t.name)
                    tools_all.append(t)
                    self._tool_sessions[uid][t.name] = chainlit_session

                self._sessions[uid][label] = chainlit_session
                return
            except MCPToolLoadError:
                raise
            except Exception as e:
                raise MCPToolLoadError(label, str(e))

        try:
            # Get MCP servers from in-memory storage for THIS USER
            mcp_servers = get_mcp_servers(user_id=uid)
            print(
                f"🔍 [Agent] Building graph for user '{uid}' with {len(mcp_servers)} MCP server(s)"
            )

            for server in mcp_servers.values():
                # IMPORTANT: Prefer URL-based connection over chainlit_session
                # The chainlit_session doesn't work reliably when used from background tasks
                # (e.g., delayed_rebuild in ECS/AWS environments)
                # Only use chainlit_session for stdio-based servers that don't have a URL
                if "url" in server and server["url"]:
                    # SSE or streamable-http connection via URL (ngrok, remote MCP servers)
                    # Extract headers if available (for authenticated connections like n8n)
                    headers = server.get("headers")
                    if headers and isinstance(headers, dict):
                        # Ensure headers are in the correct format (dict[str, str])
                        headers = {str(k): str(v) for k, v in headers.items()}
                    await connect_and_load(
                        server["name"], server["url"], headers=headers
                    )
                elif "chainlit_session" in server and server["chainlit_session"]:
                    # stdio-based servers (like Code Formatter) - no URL, use Chainlit session
                    await load_from_chainlit_session(
                        server["name"], server["chainlit_session"]
                    )
                else:
                    # Server has no URL and no Chainlit session
                    # This means it's not properly configured - skip it
                    print(
                        f"⚠️  [Agent] Skipping {server.get('name', 'unknown')} - no URL or Chainlit session. Please add this server via Chainlit UI."
                    )

            # Bedrock LLM
            try:
                region = normalize_aws_env(default_region="us-east-1")
                model_id = resolve_bedrock_model_id()
            except Exception as e:
                raise ConfigurationError("AWS Bedrock configuration", str(e))

            try:
                llm = ChatBedrock(
                    model=model_id,
                    model_kwargs={"temperature": 0},
                    region=region,
                )
            except Exception as e:
                raise ConfigurationError(
                    "AWS Bedrock LLM initialization",
                    f"Failed to initialize ChatBedrock with model_id={model_id}, region={region}. {str(e)}",
                )

            try:
                self._graphs[uid] = build_graph(llm=llm, tools=tools_all)
            except Exception as e:
                raise GraphBuildError(str(e))

            print(
                f"✅ [Agent] Graph rebuilt for user '{uid}' with {len(tools_all)} unique tools"
            )

        except (
            MCPConnectionError,
            MCPToolLoadError,
            GraphBuildError,
            ConfigurationError,
        ):
            # Re-raise custom exceptions as-is
            await exit_stack.aclose()
            raise
        except Exception as e:
            print(
                f"❌ [Agent] Unexpected error during graph rebuild for user '{uid}': {e}"
            )
            await exit_stack.aclose()
            raise GraphBuildError(str(e))

    async def rebuild_graph(self, user_id: Optional[str] = None) -> None:
        """Public method to force graph rebuild for a user (e.g., after MCP servers are added/removed)"""
        uid = user_id or self.DEFAULT_USER_ID
        # Use lock to prevent concurrent rebuilds
        async with _rebuild_lock:
            await self._do_rebuild_graph(user_id=uid)

    async def _do_rebuild_graph(self, user_id: Optional[str] = None) -> None:
        """Internal method that performs the actual rebuild for a user"""
        uid = user_id or self.DEFAULT_USER_ID


        # Close existing MCP connections for this user if any
        if uid in self._graphs and self._graphs[uid] is not None:
            # Store old exit stack and sessions for this user
            old_exit_stack = self._exit_stacks.get(uid)
            old_sessions = self._sessions.get(uid, {}).copy()
            old_tool_sessions = self._tool_sessions.get(uid, {}).copy()

            # Reset state for this user first
            self._sessions[uid] = {}
            self._tool_sessions[uid] = {}
            self._graphs[uid] = None
            # Clear exit stack reference for this user to prevent reuse
            self._exit_stacks[uid] = None

            # Close old connections
            # Note: "async generator ignored GeneratorExit" and "no running event loop" warnings
            # are non-critical cleanup messages that occur during connection teardown
            if old_exit_stack and (old_sessions or old_tool_sessions):
                try:
                    # Close the exit stack - this will clean up all async contexts
                    # Wrap in try-except to suppress cleanup errors
                    try:
                        await old_exit_stack.aclose()
                    except (RuntimeError, Exception) as e:
                        # Suppress "no running event loop" and other cleanup errors
                        error_msg = str(e).lower()
                        if (
                            "no running event loop" in error_msg
                            or "event loop" in error_msg
                        ):
                            # These are non-critical cleanup warnings
                            pass
                        else:
                            print(
                                f"⚠️ [Agent] Error closing old exit stack for user '{uid}' (non-critical): {e}"
                            )
                except Exception as e:
                    # Log but don't fail - connections might already be closed
                    print(
                        f"⚠️ [Agent] Error closing old exit stack for user '{uid}' (non-critical): {e}"
                    )

                # Wait for async generator cleanup to complete
                # This gives time for all async contexts to fully close
                await asyncio.sleep(0.5)

            # Create new exit stack AFTER old one is fully closed
            try:
                self._exit_stacks[uid] = AsyncExitStack()
            except RuntimeError as e:
                # If RuntimeError occurs (shouldn't happen, but handle it)
                if "async generator" in str(e).lower() or "GeneratorExit" in str(e):
                    # Wait a bit more for cleanup to complete
                    await asyncio.sleep(0.2)
                    self._exit_stacks[uid] = AsyncExitStack()
                else:
                    raise

        # Now rebuild for this user
        await self._rebuild_graph(user_id=uid)

    def invalidate_graph(self, user_id: Optional[str] = None) -> None:
        """Invalidate the graph for a user so it will be rebuilt on next access"""
        uid = user_id or self.DEFAULT_USER_ID
        if uid in self._graphs:
            self._graphs[uid] = None
        print(
            f"🔄 [Agent] Graph invalidated for user '{uid}' - will be rebuilt on next access"
        )

    async def get_graph(self, user_id: Optional[str] = None) -> Any:
        uid = user_id or self.DEFAULT_USER_ID
        if uid not in self._graphs or self._graphs[uid] is None:
            await self.ensure_ready(user_id=uid)
        return self._graphs.get(uid)

    async def graph_to_mermaid(self, user_id: Optional[str] = None) -> str:
        """Convert the graph to a Mermaid string."""
        computed_graph = await self.get_graph(user_id=user_id)
        return computed_graph.get_graph().draw_mermaid()

    # Allow calling tools directly (for polling)
    async def call_tool(
        self, name: str, arguments: Dict[str, Any], user_id: Optional[str] = None
    ) -> Any:
        uid = user_id or self.DEFAULT_USER_ID
        user_tool_sessions = self._tool_sessions.get(uid, {})


        if not user_tool_sessions:
            await self.ensure_ready(user_id=uid)
            user_tool_sessions = self._tool_sessions.get(uid, {})


        session = user_tool_sessions.get(name)
        if not session:
            raise ValueError(
                f"Tool '{name}' is not registered for user '{uid}'. Available tools: {list(user_tool_sessions.keys())}"
            )

        # Check if session is still valid before calling
        # If connection was closed, we need to rebuild the graph
        try:
            return await session.call_tool(name, arguments)
        except Exception as e:
            error_msg = str(e).lower()
            if "closedresourceerror" in error_msg or "closed" in error_msg:
                # Connection was closed, rebuild graph and retry
                print(
                    f"⚠️ [Agent] Connection closed for tool '{name}' (user '{uid}'). Rebuilding graph..."
                )
                await self.rebuild_graph(user_id=uid)
                # Get the new session
                user_tool_sessions = self._tool_sessions.get(uid, {})
                session = user_tool_sessions.get(name)
                if not session:
                    raise ValueError(
                        f"Tool '{name}' is not available after rebuild for user '{uid}'. Available tools: {list(user_tool_sessions.keys())}"
                    )
                return await session.call_tool(name, arguments)
            else:
                raise


agent_runtime = AgentRuntime()
