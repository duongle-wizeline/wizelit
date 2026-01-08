import json
import logging
import uuid
import asyncio
import re
import os
from typing import Dict, Optional

import chainlit as cl
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.data.storage_clients.base import BaseStorageClient
from chainlit.types import ThreadDict
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from diff_utils import generate_inline_diff, generate_plotly_diff

from agent import agent_runtime
from database import DatabaseManager
from utils import create_chat_settings
from utils.diff_viewer import html_diff_viewer

db_manager = DatabaseManager()
logger = logging.getLogger(__name__)

@cl.on_app_startup
async def on_startup():
    await db_manager.init_db()
    await agent_runtime.ensure_ready()

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
            await cl.Message(content=f"👨‍✈️ **Captain:** Dispatching Crew... (ID: `{job_id}`)").send()

            async with cl.Step(name="Refactoring Crew", type="run") as step:
                step.input = "Initializing Agent Swarm..."
                await step.update()

                # Check if streaming is enabled
                enable_streaming = os.getenv("ENABLE_LOG_STREAMING", "true").lower() == "true"

                if enable_streaming:
                    # Real-time streaming via Redis
                    try:
                        from core.wizelit_agent_wrapper.streaming import LogStreamer

                        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
                        log_streamer = LogStreamer(redis_url)

                        accumulated_logs = []
                        timeout = float(os.getenv("LOG_STREAM_TIMEOUT_SECONDS", "300"))

                        try:
                            async for log_event in log_streamer.subscribe_logs(job_id, timeout=timeout):
                                # Handle log messages
                                if "message" in log_event:
                                    ts = log_event.get("timestamp", "")[:8]  # HH:MM:SS
                                    level = log_event.get("level", "INFO")
                                    msg = log_event.get("message", "")
                                    formatted = f"[{level}] [{ts}] {msg}"
                                    accumulated_logs.append(formatted)

                                    # Update UI with latest logs
                                    step.output = "\n".join(accumulated_logs[-25:])  # Show last 25 lines
                                    await step.update()

                                # Handle status changes
                                if "status" in log_event:
                                    status = log_event["status"]

                                    if status == "completed":
                                        tool_result = log_event.get("result")

                                        if isinstance(tool_result, str):
                                            await cl.Message(content=f"{tool_result}").send()
                                            return

                                        if tool_result and "html" in tool_result and tool_result["html"]:
                                            html_viewer_element = cl.CustomElement(
                                                name="RawHtmlRenderElement",
                                                props={"htmlString": tool_result["html"]}
                                            )
                                            cl.user_session.set("html_viewer_el", html_viewer_element)
                                            await cl.Message(content="", elements=[html_viewer_element]).send()

                                        if tool_result and "code" in tool_result and tool_result["code"]:
                                            await cl.Message(
                                                content=f"### 📦 Final Code\n```python\n{tool_result['code']}\n```"
                                            ).send()

                                        if tool_result and "text" in tool_result and tool_result["text"]:
                                            await cl.Message(content=f"{tool_result['text']}").send()

                                        return

                                    elif status == "failed":
                                        error = log_event.get("error", "Unknown error")
                                        await cl.Message(content=f"❌ **Job Failed:** {error}").send()
                                        return

                        except asyncio.TimeoutError:
                            await cl.Message(content="⏱️ Job is still running. Check back later.").send()
                            return

                        finally:
                            await log_streamer.close()

                    except ImportError:
                        logger.warning("Redis not available, falling back to polling")
                        enable_streaming = False
                    except Exception as e:
                        logger.error(f"Streaming error: {e}", exc_info=True)
                        await cl.Message(content=f"⚠️ Streaming unavailable, falling back to polling: {e}").send()
                        enable_streaming = False

                # Fallback to polling if streaming is disabled or failed
                if not enable_streaming:
                    last_logs = ""
                    # Poll for 60 seconds
                    for _ in range(60):
                        await asyncio.sleep(1)

                        # Call tool via agent_runtime (Reuse existing connection)
                        try:
                            job_status = await agent_runtime.call_tool(
                                "get_job_status",
                                {"job_id": job_id},
                            )
                            # Extract text
                            job_result = json.loads(job_status.content[0].text)
                        except Exception as e:
                            job_result = {"error": f"Error polling: {e}"}

                        # Update UI
                        if "logs" in job_result and job_result["logs"] and job_result["logs"] != last_logs:
                            step.output = job_result["logs"]
                            await step.update()
                            last_logs = job_result["logs"]

                        if "status" in job_result:
                            if job_result["status"] == "completed":
                                tool_result = job_result["result"]

                                if isinstance(tool_result, str):
                                    await cl.Message(content=f"{tool_result}").send()
                                    return

                                if "html" in tool_result and tool_result["html"]:
                                    html_viewer_element = cl.CustomElement(
                                        name="RawHtmlRenderElement",
                                        props={"htmlString": tool_result["html"]}
                                    )
                                    cl.user_session.set("html_viewer_el", html_viewer_element)
                                    await cl.Message(content="", elements=[html_viewer_element]).send()

                                if "code" in tool_result and tool_result["code"]:
                                    await cl.Message(
                                        content=f"### 📦 Final Code\n```python\n{tool_result['code']}\n```"
                                    ).send()

                                if "text" in tool_result and tool_result["text"]:
                                    await cl.Message(content=f"{tool_result['text']}").send()

                                return

                            if job_result["status"] == "failed":
                                await cl.Message(content="❌ **Job Failed.**").send()
                                return
        else:
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
def oauth_callback(provider_id: str, token: str, raw_user_data: Dict[str, str], default_user: cl.User) -> Optional[cl.User]:
    if provider_id == "google" and raw_user_data["hd"] == "wizeline.com":
        return default_user
    return None

@cl.data_layer
def get_data_layer():
    return SQLAlchemyDataLayer(conninfo=db_manager.DATABASE_URL, storage_provider=BaseStorageClient)

def _extract_response(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.content:
            return str(message.content)
    return ""

