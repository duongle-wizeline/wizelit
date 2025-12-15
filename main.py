import logging
import uuid
import asyncio
import re
from typing import Dict, Optional

import chainlit as cl
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.data.storage_clients.base import BaseStorageClient
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from agent import agent_runtime
from database import DatabaseManager
from utils import create_chat_settings

# Initialize database manager
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

        # 2. Check for Job ID (The Async Handshake)
        # UPDATED REGEX: Handles spaces (\s*) and captures the ID accurately
        job_match = re.search(r"JOB_ID:\s*(JOB-[\w-]+)", response_text)
        
        if job_match:
            job_id = job_match.group(1)
            
            # Announce the tracking
            await cl.Message(content=f"👨‍✈️ **Captain:** Dispatching Refactoring Crew... (ID: `{job_id}`)").send()

            # 3. Start the "Glass Box" UI (Streaming Logs)
            async with cl.Step(name="Refactoring Crew", type="run") as step:
                step.input = "Initializing Agent Swarm..."
                await step.update()

                # Polling Loop (Max 60 seconds)
                for _ in range(30):
                    await asyncio.sleep(2)
                    
                    # Ask the graph to check status
                    # We send a hidden prompt to the LLM to call 'get_job_status'
                    status_msg = HumanMessage(content=f"Use the 'get_job_status' tool to check status for {job_id}. Just output the result.")
                    status_res = await graph.ainvoke({"messages": [status_msg]}, config=config)
                    status_raw = _extract_response(status_res.get("messages", []))

                    # Parse the Logs
                    if "LOGS:" in status_raw:
                        # Extract everything between LOGS: and the end (or RESULT:)
                        clean_logs = status_raw.split("LOGS:")[1].split("RESULT:")[0].strip()
                        step.output = clean_logs # Update the UI bubble
                        await step.update()

                    # Check for Completion
                    if "STATUS: COMPLETED" in status_raw:
                        # Extract the code block
                        try:
                            final_code = status_raw.split("RESULT:")[1].strip()
                        except IndexError:
                            final_code = status_raw # Fallback
                            
                        await cl.Message(content=f"✅ **Refactoring Complete!**\n\n{final_code}").send()
                        return
                    
                    if "STATUS: FAILED" in status_raw:
                        await cl.Message(content="❌ **Job Failed.** Please check logs.").send()
                        return
            
        else:
            # Normal conversation (No job started)
            await cl.Message(content=response_text).send()

    except Exception as e:
        logger.exception("Error in main loop")
        await cl.Message(content=f"An error occurred: {str(e)}").send()

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
        if isinstance(message, AIMessage):
            return str(message.content)
    return ""