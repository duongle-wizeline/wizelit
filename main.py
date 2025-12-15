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

from agent import agent_runtime
from database import DatabaseManager
from utils import create_chat_settings

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
                        tool_result = await agent_runtime.call_tool(
                            "get_job_status",
                            {"job_id": job_id},
                        )
                        # Extract text
                        status_raw = tool_result.content[0].text
                    except Exception as e:
                        status_raw = f"Error polling: {e}"

                    # Update UI
                    if "LOGS:" in status_raw:
                        clean_logs = status_raw.split("LOGS:")[1].split("RESULT:")[0].strip()
                        if clean_logs and clean_logs != last_logs:
                            step.output = clean_logs
                            await step.update()
                            last_logs = clean_logs

                    if "STATUS: COMPLETED" in status_raw:
                        final_code = status_raw.split("RESULT:")[1].strip() if "RESULT:" in status_raw else status_raw
                        await cl.Message(content=f"✅ **Done!**\n\n{final_code}").send()
                        return

                    if "STATUS: FAILED" in status_raw:
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
