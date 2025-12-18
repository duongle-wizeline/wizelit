import json
import logging
import uuid
import asyncio
import re
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
                    if job_result["logs"] and job_result["logs"] != last_logs:
                        step.output = job_result["logs"]
                        await step.update()
                        last_logs = job_result["logs"]

                    if job_result["status"]:
                        if job_result["status"] == "completed":
                            tool_result = job_result["result"]

                            if "html" in tool_result and tool_result["html"]:
                                html_viewer_element = cl.CustomElement(name="RawHtmlRenderElement", props={"htmlString": tool_result["html"]})
                                # Store the element if we want to update it server side at a later stage.
                                cl.user_session.set("diff_viewer_el", html_viewer_element)
                                await cl.Message(content="", elements=[html_viewer_element]).send()

                            if "code" in tool_result and tool_result["code"]:
                                await cl.Message(
                                    content=f"### 📦 Final Code\n```python\n{tool_result["code"]}\n```"
                                ).send()

                            if "text" in tool_result and tool_result["text"]:
                                await cl.Message(content=f"{tool_result["text"]}").send()

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

def _extract_lines(text: str) -> list[str]:
    # Break incoming message into an array of text lines (non-empty)
    lines = [l for l in text.splitlines() if l.strip() != ""]
    # Fallback to original content if splitting yields nothing (e.g., only whitespace)
    if not lines:
        lines = [text]
    return lines


