import json
import logging
import os
import sys
import uuid
import asyncio
import time
import re
from typing import Dict, Optional, cast
from pathlib import Path
from mcp import ClientSession

import chainlit as cl
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.types import ThreadDict
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from agent import agent_runtime
from database import DatabaseManager
from utils import create_chat_settings
from utils.prompt_guides import refresh_prompt_guides
from utils.mcp_storage import (
    add_mcp_server,
    remove_mcp_server,
    get_mcp_server,
    clear_all,
    get_mcp_servers,
    is_server_removed,
    get_removal_cooldown_remaining,
)
from exceptions import (
    GraphBuildError,
    GraphExecutionError,
)


def _get_user_id() -> str:
    """
    Get a unique user identifier from Chainlit context.

    CRITICAL: Must return a consistent, unique ID per authenticated user.
    The ID is used to isolate MCP server connections between users.

    Priority:
    1. Authenticated user's identifier (email from OAuth)
    2. Authenticated user's internal ID
    3. WebSocket session ID (unique per browser tab)
    4. Stored user_id from chat session
    5. Generated UUID (should never happen)
    """
    user_id = None
    source = None

    try:
        if hasattr(cl, 'context') and cl.context and hasattr(cl.context, 'session'):
            session = cl.context.session

            # First priority: authenticated user identifier (OAuth email/ID)
            if hasattr(session, 'user') and session.user:
                user = session.user
                # Try identifier (usually email from OAuth)
                if hasattr(user, 'identifier') and user.identifier:
                    user_id = user.identifier
                    source = "user.identifier"
                # Try internal user ID
                elif hasattr(user, 'id') and user.id:
                    user_id = user.id
                    source = "user.id"

            # Second priority: WebSocket client ID (unique per browser connection)
            # This is more reliable than session.id for distinguishing browsers
            if not user_id:
                if hasattr(session, 'client_id') and session.client_id:
                    user_id = session.client_id
                    source = "client_id"
                elif hasattr(session, 'id') and session.id:
                    # session.id as fallback (but may not be unique per user!)
                    user_id = session.id
                    source = "session.id"
    except Exception as e:
        logger.warning(f"⚠️ [Auth] Error getting user_id from context: {e}")

    # Fallback: try to get from user_session (set in on_chat_start)
    if not user_id:
        try:
            stored_id = cl.user_session.get("user_id")
            if stored_id:
                user_id = stored_id
                source = "user_session"
        except Exception:
            pass

    # Last resort: generate a UUID (indicates a problem - should never happen)
    if not user_id:
        user_id = f"anon_{uuid.uuid4().hex[:12]}"
        source = "generated_uuid"
        logger.warning(f"⚠️ [Auth] Generated anonymous user_id: {user_id} - this may cause isolation issues!")

    # Log for debugging multi-user issues
    logger.debug(f"🔑 [Auth] User ID: {user_id} (source: {source})")
    return user_id


# Health check endpoint for ALB/ECS
@cl.server.app.get("/health")
async def health_check():
    """Health check endpoint for load balancer and container orchestration."""
    return {"status": "healthy", "service": "wizelit"}


db_manager = DatabaseManager()
logger = logging.getLogger(__name__)

# Add project root to Python path so imports work
PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TASK_TIMEOUT = os.getenv("TASK_TIMEOUT", 1200)  # Default to 20 minutes


@cl.on_app_startup
async def on_startup():
    await db_manager.init_db()

    # CRITICAL: Clear all MCP servers from in-memory storage on startup
    # This ensures that only MCP servers that Chainlit successfully auto-reconnects to
    # (via on_mcp_connect) are loaded, preventing stale connections from previous sessions
    existing_servers = get_mcp_servers()
    if existing_servers:
        logger.info(
            f"🧹 [Main] Clearing {len(existing_servers)} existing MCP server(s) from storage on startup"
        )
        clear_all()

    # Clear any in-memory removal blacklist (in case of hot reload)
    # NOTE: The blacklist is now in-memory only (no file persistence)
    # Chainlit's browser-stored MCP configs are the source of truth
    from utils.mcp_storage import clear_removed_servers
    clear_removed_servers()
    logger.info(
        "🧹 [Main] Ready for MCP connections - Chainlit UI is source of truth"
    )

    # Refresh handler metadata on startup
    from utils.tool_response_handler import _tool_response_handler

    _tool_response_handler.refresh_metadata()
    logger.info("✅ [Main] Handler metadata refreshed on startup")

    # Don't call ensure_ready() here - let Chainlit auto-reconnect first via on_mcp_connect
    # This ensures we only load servers that Chainlit successfully reconnects to
    # The graph will be built when the first query comes in (via get_graph())
    logger.info(
        "✅ [Main] Startup complete. Waiting for Chainlit to auto-reconnect MCP servers..."
    )


@cl.on_mcp_connect
async def on_mcp(connection, session: ClientSession):
    server_key = connection.name.replace(" ", "")
    user_id = _get_user_id()

    # Enhanced logging to debug multi-user issues
    logger.info(f"🔌 [Main] MCP connect request: server='{connection.name}', user='{user_id}'")

    # Log additional context for debugging
    try:
        if hasattr(cl, 'context') and cl.context and hasattr(cl.context, 'session'):
            ctx_session = cl.context.session
            user_info = "no_user"
            if hasattr(ctx_session, 'user') and ctx_session.user:
                user_info = f"identifier={getattr(ctx_session.user, 'identifier', 'N/A')}, id={getattr(ctx_session.user, 'id', 'N/A')}"
            client_id = getattr(ctx_session, 'client_id', 'N/A')
            session_id = getattr(ctx_session, 'id', 'N/A')
            logger.info(f"🔍 [Main] Context details: client_id={client_id}, session_id={session_id}, user=({user_info})")
    except Exception as e:
        logger.debug(f"Could not log context details: {e}")

    # Check if this server was recently removed for THIS USER (in cooldown period)
    # The cooldown prevents Chainlit auto-reconnect from immediately re-adding removed servers
    if is_server_removed(server_key, user_id=user_id):
        remaining = get_removal_cooldown_remaining(server_key, user_id=user_id)
        logger.warning(
            f"🚫 [Main] Rejecting reconnect to '{connection.name}' for user '{user_id}' - server is in removal cooldown ({remaining:.0f}s remaining)"
        )
        # Don't add to storage, don't rebuild graph
        # The connection will be established by Chainlit, but we won't use it
        return

    # List available tools
    result = await session.list_tools()

    # Process tool metadata
    tools = []
    for t in result.tools:
        tool_dict = {
            "name": t.name,
            "description": t.description,
            "input_schema": t.inputSchema,
            "output_schema": t.outputSchema,
            "meta": t.meta,
            "title": t.title,
        }

        # Extract response_handling from MCP tool meta (from agent code via MCP protocol)
        if t.meta and isinstance(t.meta, dict):
            if "wizelit_response_handling" in t.meta:
                tool_dict["response_handling"] = t.meta["wizelit_response_handling"]
                logger.info(
                    f"✅ [Main] Found response_handling for {t.name}: {t.meta['wizelit_response_handling']}"
                )
            else:
                logger.debug(
                    f"⚠️ [Main] No response_handling in meta for {t.name}. Meta keys: {list(t.meta.keys())}"
                )

        tools.append(tool_dict)

    # Store server metadata in memory (per-user)
    new_connection = connection.__dict__.copy()
    new_connection["tools"] = tools
    # CRITICAL: For stdio-based servers (like Code Formatter), store the Chainlit session
    # so agent.py can reuse it instead of trying to reconnect
    new_connection["chainlit_session"] = session

    # Check if server already exists for this user (to avoid overwriting on Chainlit auto-reconnect)
    existing_server = get_mcp_server(server_key, user_id=user_id)
    if existing_server:
        logger.info(
            f"ℹ️ [Main] MCP server '{connection.name}' already in storage for user '{user_id}', updating"
        )

    add_mcp_server(server_key, new_connection, user_id=user_id)
    logger.info(f"✅ [Main] Stored MCP server '{connection.name}' for user '{user_id}'")
    refresh_prompt_guides()
    # Refresh tool response handler metadata for this user
    from utils.tool_response_handler import _tool_response_handler

    _tool_response_handler.refresh_metadata(user_id=user_id)

    # CRITICAL: Rebuild the graph so it includes the newly added tools
    # The graph is cached and won't automatically pick up new tools
    # Add a small delay to let Chainlit finish its session setup before rebuilding
    logger.info(
        f"🔄 [Main] Scheduling graph rebuild to include new tools from '{connection.name}'..."
    )

    # Use asyncio.create_task to run rebuild in background after a short delay
    # This allows Chainlit to complete its session setup without blocking
    # Capture user_id for the closure
    rebuild_user_id = user_id

    async def delayed_rebuild():
        try:
            # Wait a bit to let Chainlit finish its session operations
            await asyncio.sleep(0.5)
            await agent_runtime.rebuild_graph(user_id=rebuild_user_id)
            logger.info(f"✅ [Main] Graph rebuilt for '{connection.name}' (user '{rebuild_user_id}').")
        except GraphBuildError as rebuild_error:
            logger.error(f"❌ [Main] Failed to rebuild graph for '{connection.name}': {rebuild_error}")
        except Exception as rebuild_error:
            logger.error(f"❌ [Main] Unexpected error rebuilding graph: {rebuild_error}")

    # Store task reference to prevent garbage collection
    task = asyncio.create_task(delayed_rebuild())
    # Don't await - let it run in background


@cl.on_mcp_disconnect
async def on_mcp_disconnect(name: str, session: ClientSession):
    """Called when an MCP connection is terminated"""
    no_spaces_name = name.replace(" ", "")
    user_id = _get_user_id()

    logger.info(f"🔌 [Main] MCP disconnect: server='{name}', user='{user_id}'")

    # Remove the disconnected server from in-memory storage (for this user only)
    remove_mcp_server(no_spaces_name, user_id=user_id)
    logger.info(f"🗑️ [Main] Removed MCP server '{name}' for user '{user_id}'")

    # CRITICAL: Immediately invalidate the graph for THIS USER so it will be rebuilt on next access
    agent_runtime.invalidate_graph(user_id=user_id)
    logger.info(f"🔄 [Main] Graph invalidated for user '{user_id}' after disconnecting '{name}'")

    refresh_prompt_guides()
    # Refresh tool response handler metadata for this user
    from utils.tool_response_handler import _tool_response_handler

    _tool_response_handler.refresh_metadata(user_id=user_id)

    # CRITICAL: Rebuild the graph after removing tools
    # Run rebuild in background to avoid blocking
    logger.info(f"🔄 [Main] Scheduling graph rebuild for user '{user_id}' after removing '{name}'...")

    # Capture user_id for the closure
    rebuild_user_id = user_id

    async def delayed_rebuild():
        try:
            # Wait a bit to let any cleanup operations complete
            await asyncio.sleep(0.5)
            await agent_runtime.rebuild_graph(user_id=rebuild_user_id)
            logger.info(f"✅ [Main] Graph rebuilt for user '{rebuild_user_id}' after disconnecting '{name}'.")
        except GraphBuildError as rebuild_error:
            logger.error(f"❌ [Main] Failed to rebuild graph after disconnect: {rebuild_error}")
        except Exception as rebuild_error:
            logger.error(f"❌ [Main] Unexpected error rebuilding graph: {rebuild_error}")

    # Store task reference to prevent garbage collection
    task = asyncio.create_task(delayed_rebuild())
    # Don't await - let it run in background


@cl.on_chat_start
async def on_chat_start():
    session_id = str(uuid.uuid4())
    cl.user_session.set("session_id", session_id)

    # Store user_id in session for consistent access
    user_id = _get_user_id()
    cl.user_session.set("user_id", user_id)

    # Log detailed info for debugging multi-user isolation
    logger.info(f"🆕 [Main] New chat started for user '{user_id}' (session: {session_id[:8]}...)")

    await create_chat_settings().send()

    # Show MCP status (with small delay to let auto-reconnects complete)
    await asyncio.sleep(1.0)

    # Get MCP servers for THIS USER only
    mcp_servers = get_mcp_servers(user_id=user_id)

    # Log storage state for debugging multi-user issues
    from utils.mcp_storage import get_all_user_ids, get_user_count
    total_users = get_user_count()
    all_users = get_all_user_ids()
    logger.info(f"📊 [Main] Storage state: {total_users} user(s) with MCP servers: {all_users}")
    logger.info(f"📊 [Main] User '{user_id}' has {len(mcp_servers)} MCP server(s): {list(mcp_servers.keys())}")

    if mcp_servers:
        tool_count = sum(len(s.get("tools", [])) for s in mcp_servers.values())
        server_list = ", ".join(f"`{name}`" for name in mcp_servers.keys())
        # Show user ID (truncated) for debugging multi-user isolation
        user_display = user_id[:20] + "..." if len(user_id) > 20 else user_id
        await cl.Message(
            content=f"🔧 **{len(mcp_servers)} MCP Server(s) Connected:** {server_list}\n"
            f"📦 **{tool_count} tools** available.\n"
            f"🔑 *User: {user_display}*"
        ).send()
    else:
        await cl.Message(
            content="⚠️ **No MCP tools connected yet.**\n"
            "Add servers via the MCP panel, or they may auto-connect shortly."
        ).send()


@cl.on_message
async def main(message: cl.Message):
    session_id = cl.user_session.get("session_id")
    user_id = cl.user_session.get("user_id") or _get_user_id()

    # Get graph for THIS USER
    graph = await agent_runtime.get_graph(user_id=user_id)
    if graph is None:
        await cl.Message(
            content="⚠️ **Connection Error:** The agent graph is unavailable. Please try again."
        ).send()
        return

    config = cast(RunnableConfig, {"configurable": {"thread_id": session_id}})

    try:
        # 1. Call the Agent
        # Get the current message count before invoking to track what's new
        try:
            # Get current state to see message count before this invocation
            current_state = await graph.aget_state(config)
            messages_before = len(current_state.values.get("messages", [])) if current_state.values else 0
            logger.debug(f"📊 [Main] Messages before invocation: {messages_before}")

            result = await graph.ainvoke(
                {"messages": [HumanMessage(content=message.content)]},
                config=config,
            )

            messages_after = len(result.get("messages", []))
            logger.debug(f"📊 [Main] Messages after invocation: {messages_after} (added {messages_after - messages_before} messages)")
        except GraphBuildError as graph_error:
            logger.error(f"Graph build failed: {graph_error}")
            await cl.Message(
                content=f"❌ **Graph Build Error:** {graph_error.message}\n\n{graph_error.suggestion}"
            ).send()
            return
        except GraphExecutionError as graph_error:
            logger.error(f"Graph execution failed: {graph_error}")
            await cl.Message(
                content=f"❌ **Execution Error:** {graph_error.message}\n\n{graph_error.suggestion}"
            ).send()
            return
        except Exception as graph_error:
            # Log the full error for debugging
            logger.exception(f"Error during graph execution: {graph_error}")
            # Check if it's a connection error
            error_msg = str(graph_error).lower()
            if (
                "closedresourceerror" in error_msg
                or "no running event loop" in error_msg
            ):
                try:
                    await agent_runtime.rebuild_graph(user_id=user_id)
                    await cl.Message(
                        content="⚠️ **Connection Recovered:** MCP server connection was restored. Please try your query again."
                    ).send()
                except GraphBuildError as rebuild_error:
                    await cl.Message(
                        content=f"⚠️ **Connection Error:** The MCP server connection was interrupted.\n\n{rebuild_error.suggestion}"
                    ).send()
                return
            else:
                # Re-raise to be caught by outer exception handler
                raise

        # Extract all handler responses (AI messages without tool_calls) for multi-step workflows
        # Only extract responses from the current execution (after the most recent human message)
        all_responses = _extract_all_responses(result.get("messages", []), only_recent=True)
        logger.info(f"📋 [Main] Extracted {len(all_responses)} handler response(s) from {len(result.get('messages', []))} total messages")

        # Separate job responses from regular responses
        job_responses = []
        regular_responses = []

        for response_text in all_responses:
            if not response_text or not response_text.strip():
                continue

            # Check for Job ID in each response
            job_match = re.search(r"JOB_ID:\s*(JOB-[\w-]+)", response_text)
            if job_match:
                job_responses.append(response_text)
            else:
                regular_responses.append(response_text)

        # Send all regular (non-job) responses first
        for idx, response_text in enumerate(regular_responses, 1):
            logger.info(f"📤 [Main] Sending regular handler response {idx}/{len(regular_responses)}: {response_text[:100]}...")
            await cl.Message(content=response_text).send()

        # Then handle job responses (these are long-running and will return early)
        for idx, response_text in enumerate(job_responses, 1):
            logger.info(f"📤 [Main] Handling job response {idx}/{len(job_responses)}: {response_text[:100]}...")
            job_match = re.search(r"JOB_ID:\s*(JOB-[\w-]+)", response_text)

            if job_match:
                job_id = job_match.group(1)
                await cl.Message(
                    content=f"👨‍✈️ **Captain:** Dispatching Crew... (ID: `{job_id}`)"
                ).send()

                async with cl.Step(name="Refactoring Crew", type="run") as step:
                    step.input = "Initializing Agent Swarm..."
                    await step.update()

                    # Check if streaming is enabled
                    enable_streaming = (
                        os.getenv("ENABLE_LOG_STREAMING", "true").lower() == "true"
                    )

                    if enable_streaming:
                        # Real-time streaming via Redis
                        try:
                            from wizelit_sdk.agent_wrapper.streaming import LogStreamer

                            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
                            log_streamer = LogStreamer(redis_url)

                            accumulated_logs = []
                            timeout = float(os.getenv("LOG_STREAM_TIMEOUT_SECONDS", "300"))

                            try:
                                async for log_event in log_streamer.subscribe_logs(
                                    job_id, timeout=timeout
                                ):
                                    # Handle log messages
                                    if "message" in log_event:
                                        ts = log_event.get("timestamp", "")[:8]  # HH:MM:SS
                                        level = log_event.get("level", "INFO")
                                        msg = log_event.get("message", "")
                                        formatted = f"[{level}] [{ts}] {msg}"
                                        accumulated_logs.append(formatted)

                                        # Update UI with latest logs
                                        step.output = "\n".join(
                                            accumulated_logs[-25:]
                                        )  # Show last 25 lines
                                        await step.update()

                                    # Handle status changes
                                    if "status" in log_event:
                                        status = log_event["status"]

                                        if status == "completed":
                                            tool_result = log_event.get("result")
                                            # Delegate handling to helper function; if it returns True, we should return from main.
                                            if await _handle_tool_result(tool_result):
                                                return

                                        elif status == "failed":
                                            error = log_event.get("error", "Unknown error")
                                            await cl.Message(
                                                content=f"❌ **Job Failed:** {error}"
                                            ).send()
                                            return

                            except asyncio.TimeoutError:
                                await cl.Message(
                                    content="⏱️ Job is still running. Check back later."
                                ).send()
                                return

                            finally:
                                await log_streamer.close()

                        except ImportError:
                            logger.warning("Redis not available, falling back to polling")
                            enable_streaming = False

                        except Exception as e:
                            logger.error(f"Streaming error: {e}", exc_info=True)
                            await cl.Message(
                                content=f"⚠️ Streaming unavailable, falling back to polling: {e}"
                            ).send()
                            enable_streaming = False

                    # Fallback to polling if streaming is disabled or failed
                    if not enable_streaming:
                        await _polling_for_job(job_id, step, user_id=user_id)
                    # Job handling is complete, return
                    return
            else:
                # Not a job response, send the message and continue to next response
                await cl.Message(content=response_text).send()
                continue

        # If no responses were found, fall back to original extraction
        if not all_responses:
            response_text = _extract_response(result.get("messages", []))
            if response_text:
                try:
                    # Try to parse response as JSON
                    response_json = json.loads(response_text)

                    if "status" in response_json:
                        if response_json["status"] == "completed":
                            tool_result = response_json["result"]

                            # Delegate handling to helper function; if it returns True, we should return from main.
                            if await _handle_tool_result(tool_result):
                                return

                        if response_json["status"] == "failed":
                            await cl.Message(content="❌ **Job Failed.**").send()
                            return

                        if "logs" in response_json:
                            await cl.Message(content=response_json["logs"]).send()
                            return
                    else:
                        await cl.Message(content=response_text).send()
                except json.JSONDecodeError:
                    # Fallback to plain text response
                    await cl.Message(content=response_text).send()

    except Exception as e:
        logger.exception("Error in main loop")
        # Provide more detailed error message
        error_msg = str(e)
        error_type = type(e).__name__
        # If it's a connection error, provide helpful context
        if "ClosedResourceError" in error_type or "no running event loop" in error_msg:
            await cl.Message(
                content=f"⚠️ **Connection Error:** The MCP server connection was interrupted. Please try again or restart the MCP servers."
            ).send()
        else:
            await cl.Message(content=f"❌ **Error ({error_type}):** {error_msg}").send()


@cl.on_chat_resume
async def on_chat_resume(thread: ThreadDict):
    # Listen on_chat_resume event is required to let user continue the thread
    # even we do nothing in this event.
    pass


@cl.oauth_callback
async def oauth_callback(
    provider_id: str,
    token: str,
    raw_user_data: Dict[str, str],
    default_user: cl.User,
    context: Optional[str] = None,
) -> Optional[cl.User]:
    if provider_id == "google" and raw_user_data["hd"] == "wizeline.com":
        return default_user
    return None


@cl.data_layer
def get_data_layer():
    return SQLAlchemyDataLayer(conninfo=db_manager.DATABASE_URL)


async def _handle_tool_result(tool_result) -> bool:
    """
    Handle the tool result by sending the appropriate messages.
    Returns True if handling is terminal and the caller should return.
    """
    if isinstance(tool_result, str):
        await cl.Message(content=f"{tool_result}").send()
        return True

    if isinstance(tool_result, dict):
        if "html" in tool_result and tool_result["html"]:
            html_viewer_element = cl.CustomElement(
                name="RawHtmlRenderElement", props={"htmlString": tool_result["html"]}
            )
            # Store the element if we want to update it server side at a later stage.
            cl.user_session.set("html_viewer_el", html_viewer_element)
            await cl.Message(content="", elements=[html_viewer_element]).send()

        if "code" in tool_result and tool_result["code"]:
            await cl.Message(
                content=f"### 📦 Final Code\n```python\n{tool_result['code']}\n```"
            ).send()

        if "text" in tool_result and tool_result["text"]:
            await cl.Message(content=f"{tool_result['text']}").send()

        return True

    return False


def _extract_all_responses(messages: list[BaseMessage], only_recent: bool = False) -> list[str]:
    """
    Extract all handler responses (AI messages without tool_calls) from messages.
    This is used for multi-step workflows where multiple tools are called and each
    produces a handler response that should be shown to the user.

    Args:
        messages: List of messages to extract from
        only_recent: If True, only extract responses after the most recent human message
                     (i.e., only responses from the current query execution)

    Returns:
        A list of response strings in order.
    """
    responses = []

    # If only_recent is True, find the index of the most recent human message
    start_idx = 0
    if only_recent:
        for idx in range(len(messages) - 1, -1, -1):
            if hasattr(messages[idx], "type") and messages[idx].type == "human":
                start_idx = idx + 1  # Start from the message after the human message
                logger.debug(f"🔍 [Extract] Only extracting responses after human message at index {idx}, starting from index {start_idx}")
                break

    logger.debug(f"🔍 [Extract] Processing {len(messages)} messages (starting from index {start_idx}) to extract handler responses")
    for idx in range(start_idx, len(messages)):
        message = messages[idx]
        if isinstance(message, AIMessage) and message.content:
            content = str(message.content)

            # Skip messages with tool_calls (these are tool invocation messages, not handler responses)
            if getattr(message, "tool_calls", None):
                logger.debug(f"🔍 [Extract] Message {idx}: Skipping AI message with tool_calls")
                continue

            logger.debug(f"🔍 [Extract] Message {idx}: Found AI message without tool_calls, content preview: {content[:100]}")

            # Filter out text that looks like function calls (LLM generating code instead of using tools)
            import re
            content_stripped = content.strip()

            # Skip empty content
            if not content_stripped:
                continue

            # Pattern 1: Exact function call match
            function_call_pattern = r"^\s*\w+\s*\([^)]*\)\s*$"
            if re.match(function_call_pattern, content_stripped, re.MULTILINE):
                continue

            # Pattern 2: Function call at the start
            if re.match(r"^\s*\w+\s*\([^)]*\)", content_stripped):
                continue

            # Pattern 3: Function call followed by newline
            if re.match(r"^\s*\w+\s*\([^)]*\)\s*\n", content_stripped):
                continue

            # Pattern 4: Short standalone function call
            if len(content_stripped) < 200 and re.match(
                r"^\s*\w+\s*\([^)]*\)", content_stripped
            ):
                continue

            # This is a valid handler response, add it
            logger.debug(f"✅ [Extract] Message {idx}: Added as handler response")
            responses.append(content)

    logger.debug(f"✅ [Extract] Extracted {len(responses)} handler response(s) total")
    return responses


def _extract_response(messages: list[BaseMessage]) -> str:
    """
    Extract the final AI response from messages.
    Filters out tool call syntax that the LLM might generate as text.
    """
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.content:
            content = str(message.content)

            # Check if this message has actual tool_calls (proper tool calling)
            if getattr(message, "tool_calls", None):
                # If it has tool_calls, the tools should have been executed
                # Don't show the tool call syntax, wait for the result
                logger.debug(
                    f"Message has tool_calls, skipping content: {content[:100]}"
                )
                continue

            # Filter out text that looks like function calls (LLM generating code instead of using tools)
            # This is a generic pattern that works for any tool
            import re

            content_stripped = content.strip()

            # Pattern 1: Exact function call match: function_name(param="value", param2="value2")
            # Match: word characters, optional whitespace, opening paren, any content, closing paren, optional whitespace
            function_call_pattern = r"^\s*\w+\s*\([^)]*\)\s*$"
            if re.match(function_call_pattern, content_stripped, re.MULTILINE):
                logger.warning(
                    f"❌ [Main] LLM generated function call syntax instead of using tools: {content_stripped}"
                )
                # Return a helpful message instead of showing the function call
                return "I need to use the available tools to complete this request. Let me try again."

            # Pattern 2: Function call at the start (even if there's more text after)
            # This catches cases like "search_code(...) and then some explanation" or "scan_directory(...)\n[...]"
            if re.match(r"^\s*\w+\s*\([^)]*\)", content_stripped):
                logger.warning(
                    f"❌ [Main] LLM generated function call syntax at start of response: {content_stripped[:200]}"
                )
                # If there's content after the function call, it might be tool output - extract just the function call part
                # But for now, just filter the whole thing
                return "I need to use the available tools to complete this request. Let me try again."

            # Pattern 3: Function call followed by JSON/list output (LLM generated code + tool result mixed)
            # Catches: "function_name(...)\n[{...}]" or "function_name(...)\n[...]"
            if re.match(r"^\s*\w+\s*\([^)]*\)\s*\n", content_stripped):
                logger.warning(
                    f"❌ [Main] LLM generated function call syntax followed by output: {content_stripped[:300]}"
                )
                return "I need to use the available tools to complete this request. Let me try again."

            # Pattern 4: Check for common function call patterns (more lenient)
            # Catches: function_name(...) with any spacing
            if re.search(r"\w+\s*\([^)]+\)", content_stripped):
                # Only flag if it looks like a standalone function call (not part of explanation)
                # If the content is mostly just a function call, filter it
                if len(content_stripped) < 200 and re.match(
                    r"^\s*\w+\s*\([^)]*\)", content_stripped
                ):
                    logger.warning(
                        f"❌ [Main] LLM generated function call syntax (lenient match): {content_stripped}"
                    )
                    return "I need to use the available tools to complete this request. Let me try again."

            return content
    return ""


async def _polling_for_job(job_id: str, step: cl.Step, user_id: Optional[str] = None):
    last_logs = ""
    job_status = ""
    uid = user_id or cl.user_session.get("user_id") or _get_user_id()

    # Apply optional timeout from TASK_TIMEOUT (seconds)
    timeout = float(TASK_TIMEOUT)
    start_time = time.monotonic()

    while job_status not in ["completed", "failed"]:
        await asyncio.sleep(1)

        # Check for timeout
        if (time.monotonic() - start_time) > timeout:
            await cl.Message(
                content=f"⏳ **Timeout:** Job {job_id} takes too long to complete. You may check it status later."
            ).send()
            return

        # Call tool via agent_runtime (Reuse existing connection for this user)
        try:
            job = await agent_runtime.call_tool(
                "get_job_status",
                {"job_id": job_id},
                user_id=uid,
            )
            # Extract text
            job_result = json.loads(job.content[0].text)
        except Exception as e:
            job_result = {"error": f"Error polling: {e}"}

        # Update UI
        if (
            "logs" in job_result
            and job_result["logs"]
            and job_result["logs"] != last_logs
        ):
            step.output = job_result["logs"]
            await step.update()
            last_logs = job_result["logs"]

        if "status" in job_result:
            job_status = job_result["status"]

            if job_result["status"] == "completed":
                tool_result = job_result["result"]

                # Delegate handling to helper function; if it returns True, we should return from main.
                if await _handle_tool_result(tool_result):
                    return

            if job_result["status"] == "failed":
                await cl.Message(content="❌ **Job Failed.**").send()
                return
