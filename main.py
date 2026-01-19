import json
import logging
import os
import sys
import uuid
import asyncio
import time
import re
from typing import Dict, Optional
from pathlib import Path
from mcp import ClientSession

import chainlit as cl
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.types import ThreadDict
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from agent import agent_runtime
from database import DatabaseManager
from utils import create_chat_settings
from utils.prompt_guides import refresh_prompt_guides
from utils.mcp_storage import add_mcp_server, remove_mcp_server, get_mcp_server


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
    # Refresh handler metadata on startup
    from utils.tool_response_handler import _tool_response_handler

    _tool_response_handler.refresh_metadata()
    logger.info("✅ [Main] Handler metadata refreshed on startup")
    await agent_runtime.ensure_ready()


@cl.on_mcp_connect
async def on_mcp(connection, session: ClientSession):
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

    # Store server metadata in memory (replaces agents.yaml)
    server_key = connection.name.replace(" ", "")
    new_connection = connection.__dict__.copy()
    new_connection["tools"] = tools

    # Check if server already exists (to avoid overwriting on Chainlit auto-reconnect)
    existing_server = get_mcp_server(server_key)
    if existing_server:
        logger.info(
            f"ℹ️ [Main] MCP server '{connection.name}' already in storage, updating (Chainlit auto-reconnect)"
        )

    add_mcp_server(server_key, new_connection)
    logger.info(f"✅ [Main] Stored MCP server '{connection.name}' in memory")
    refresh_prompt_guides()
    # Refresh tool response handler metadata
    from utils.tool_response_handler import _tool_response_handler

    _tool_response_handler.refresh_metadata()

    # CRITICAL: Rebuild the graph so it includes the newly added tools
    # The graph is cached and won't automatically pick up new tools
    logger.info(
        f"🔄 [Main] Rebuilding graph to include new tools from '{connection.name}'..."
    )
    await agent_runtime.rebuild_graph()
    logger.info(f"✅ [Main] Graph rebuilt. MCP server '{connection.name}' connected.")


@cl.on_mcp_disconnect
async def on_mcp_disconnect(name: str, session: ClientSession):
    """Called when an MCP connection is terminated"""
    # Remove the disconnected server from in-memory storage
    no_spaces_name = name.replace(" ", "")
    remove_mcp_server(no_spaces_name)

    refresh_prompt_guides()
    # Refresh tool response handler metadata
    from utils.tool_response_handler import _tool_response_handler

    _tool_response_handler.refresh_metadata()

    # CRITICAL: Rebuild the graph after removing tools
    logger.info(f"🔄 [Main] Rebuilding graph after removing '{name}'...")
    await agent_runtime.rebuild_graph()
    logger.info(f"✅ [Main] Graph rebuilt after disconnecting '{name}'.")


@cl.on_chat_start
async def on_chat_start():
    session_id = str(uuid.uuid4())
    cl.user_session.set("session_id", session_id)
    await create_chat_settings().send()


@cl.on_message
async def main(message: cl.Message):
    session_id = cl.user_session.get("session_id")
    graph = await agent_runtime.get_graph()
    config = {"configurable": {"thread_id": session_id}}

    try:
        # 1. Call the Agent
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=message.content)]},
            config=config,
        )
        response_text = _extract_response(result.get("messages", []))

        # 2. Check for Job ID
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
                        from core.wizelit_agent_wrapper.streaming import LogStreamer

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
                    await _polling_for_job(job_id, step)
        else:
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
        await cl.Message(content=f"An error occurred: {str(e)}").send()


@cl.on_chat_resume
async def on_chat_resume(thread: ThreadDict):
    # Listen on_chat_resume event is required to let user continue the thread
    # even we do nothing in this event.
    pass


@cl.oauth_callback
def oauth_callback(
    provider_id: str, token: str, raw_user_data: Dict[str, str], default_user: cl.User
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


async def _polling_for_job(job_id: str, step: cl.Step):
    last_logs = ""
    job_status = ""

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

        # Call tool via agent_runtime (Reuse existing connection)
        try:
            job = await agent_runtime.call_tool(
                "get_job_status",
                {"job_id": job_id},
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
