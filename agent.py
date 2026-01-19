import os
import sys
import warnings
from dotenv import load_dotenv

load_dotenv()

import asyncio
from contextlib import AsyncExitStack, suppress
from langchain_aws import ChatBedrock
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from langchain_mcp_adapters.tools import load_mcp_tools
from graph import build_graph
from utils.bedrock_config import normalize_aws_env, resolve_bedrock_model_id
from utils.mcp_storage import get_mcp_servers

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

    def __init__(self, original_stderr):
        self.original_stderr = original_stderr
        self._buffer = ""  # Buffer for multi-line messages

    def write(self, text):
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

    def _should_suppress(self, line):
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

    def flush(self):
        # Write any remaining buffered content
        if self._buffer:
            if not self._should_suppress(self._buffer):
                self.original_stderr.write(self._buffer)
            self._buffer = ""
        self.original_stderr.flush()

    def __getattr__(self, name):
        return getattr(self.original_stderr, name)


# Only apply filter if not already applied
if not isinstance(sys.stderr, FilteredStderr):
    sys.stderr = FilteredStderr(sys.stderr)


class AgentRuntime:
    def __init__(self):
        self._graph = None
        self._exit_stack = AsyncExitStack()
        self._sessions = {}
        self._tool_sessions = {}

    async def ensure_ready(self):
        if self._graph is not None:
            return
        await self._rebuild_graph()

    async def _rebuild_graph(self):
        """Rebuild the graph with current tools from in-memory storage"""
        # Only rebuild if graph doesn't exist yet
        # If graph exists, we should not rebuild it here - use rebuild_graph() explicitly
        if self._graph is not None:
            # Graph already exists, don't rebuild
            return

        # Create new exit stack if one doesn't exist (for initial build from ensure_ready)
        # rebuild_graph() creates one before calling this, so this only runs for initial build
        if self._exit_stack is None:
            self._exit_stack = AsyncExitStack()

        # Reset sessions
        self._sessions = {}
        self._tool_sessions = {}

        tools_all = []
        seen_tool_names = set()  # Track tool names to prevent duplicates

        async def connect_and_load(label: str, url: str):
            print(f"🔌 [Agent] Connecting to {label} at {url} ...")
            sse = await self._exit_stack.enter_async_context(
                sse_client(url=url, timeout=600.0)
            )
            read_stream, write_stream = sse
            session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
            tools = await load_mcp_tools(session)
            if not tools:
                raise RuntimeError(f"❌ Connected to {url}, but found 0 tools!")
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
                self._tool_sessions[t.name] = session

            # Keep session for potential future use
            self._sessions[label] = session

        try:
            # Get MCP servers from in-memory storage (replaces agents.yaml)
            mcp_servers = get_mcp_servers()

            for server in mcp_servers.values():
                await connect_and_load(server["name"], server["url"])

            # Bedrock LLM
            region = normalize_aws_env(default_region="us-east-1")
            model_id = resolve_bedrock_model_id()
            llm = ChatBedrock(
                model_id=model_id,
                model_kwargs={"temperature": 0},
                region_name=region,
            )

            self._graph = build_graph(llm=llm, tools=tools_all)
            print(f"✅ [Agent] Graph rebuilt with {len(tools_all)} unique tools")

        except Exception as e:
            print(f"❌ [Agent] Connection Failed: {e}")
            await self._exit_stack.aclose()
            raise e

    async def rebuild_graph(self):
        """Public method to force graph rebuild (e.g., after MCP servers are added/removed)"""
        # Use lock to prevent concurrent rebuilds
        async with _rebuild_lock:
            await self._do_rebuild_graph()

    async def _do_rebuild_graph(self):
        """Internal method that performs the actual rebuild"""
        # Close existing MCP connections if any (but not database connections)
        if self._graph is not None:
            # Store old exit stack and sessions
            old_exit_stack = self._exit_stack
            old_sessions = self._sessions.copy()
            old_tool_sessions = self._tool_sessions.copy()

            # Reset state first (but keep old exit stack reference until closed)
            self._sessions = {}
            self._tool_sessions = {}
            self._graph = None
            # Clear exit stack reference to prevent reuse
            self._exit_stack = None

            # Close old connections
            # Note: "async generator ignored GeneratorExit" and "no running event loop" warnings
            # are non-critical cleanup messages that occur during connection teardown
            if old_sessions or old_tool_sessions:
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
                                f"⚠️ [Agent] Error closing old exit stack (non-critical): {e}"
                            )
                except Exception as e:
                    # Log but don't fail - connections might already be closed
                    print(f"⚠️ [Agent] Error closing old exit stack (non-critical): {e}")

                # Wait for async generator cleanup to complete
                # This gives time for all async contexts to fully close
                await asyncio.sleep(0.5)

            # Create new exit stack AFTER old one is fully closed
            # The "Exception ignored" RuntimeError warnings are non-critical cleanup messages
            # They occur when Python cleans up async generators and don't prevent functionality
            # If we get a RuntimeError (unlikely), wait a bit more and retry
            try:
                self._exit_stack = AsyncExitStack()
            except RuntimeError as e:
                # If RuntimeError occurs (shouldn't happen, but handle it)
                if "async generator" in str(e).lower() or "GeneratorExit" in str(e):
                    # Wait a bit more for cleanup to complete
                    await asyncio.sleep(0.2)
                    self._exit_stack = AsyncExitStack()
                else:
                    raise

        # Now rebuild
        await self._rebuild_graph()

    async def get_graph(self):
        if self._graph is None:
            await self.ensure_ready()
        return self._graph

    async def graph_to_mermaid(self) -> str:
        """Convert the graph to a Mermaid string."""
        computed_graph = await self.get_graph()
        return computed_graph.get_graph().draw_mermaid()

    # Allow calling tools directly (for polling)
    async def call_tool(self, name: str, arguments: dict):
        if not self._tool_sessions:
            await self.ensure_ready()
        session = self._tool_sessions.get(name)
        if not session:
            raise ValueError(f"Tool '{name}' is not registered in any session")

        # Check if session is still valid before calling
        # If connection was closed, we need to rebuild the graph
        try:
            return await session.call_tool(name, arguments)
        except Exception as e:
            error_msg = str(e).lower()
            if "closedresourceerror" in error_msg or "closed" in error_msg:
                # Connection was closed, rebuild graph and retry
                print(
                    f"⚠️ [Agent] Connection closed for tool '{name}'. Rebuilding graph..."
                )
                await self.rebuild_graph()
                # Get the new session
                session = self._tool_sessions.get(name)
                if not session:
                    raise ValueError(f"Tool '{name}' is not available after rebuild")
                return await session.call_tool(name, arguments)
            else:
                raise


agent_runtime = AgentRuntime()
