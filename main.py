import logging
import uuid
from typing import Dict, Optional

import chainlit as cl
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.data.storage_clients.base import BaseStorageClient
from chainlit.types import ThreadDict
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from agent import agent_runtime
from database import DatabaseManager
from utils import create_chat_settings

# Initialize database manager (one-time)
db_manager = DatabaseManager()
logger = logging.getLogger(__name__)


@cl.on_app_startup
async def on_startup():
    """
    Initialize database before the Chainlit app starts accepting connections.
    """
    await db_manager.init_db()
    await agent_runtime.ensure_ready()

@cl.on_chat_start
async def on_chat_start():
    """
    Called when a new chat session starts.
    Initialize user's AI agent based on their preferences.
    """
    session_id = str(uuid.uuid4())
    cl.user_session.set("session_id", session_id)
    app_user = cl.user_session.get("user")
    print(app_user)
    # Get user settings or use defaults
    await create_chat_settings().send()

@cl.on_message
async def main(message: cl.Message):
    session_id = cl.user_session.get("session_id")
    if not session_id:
        session_id = str(uuid.uuid4())
        cl.user_session.set("session_id", session_id)

    graph = await agent_runtime.get_graph()
    config = {"configurable": {"thread_id": session_id}}

    try:
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=message.content)]},
            config=config,
        )
        response = _extract_response(result.get("messages", []))
    except Exception:  # noqa: BLE001
        logger.exception("Unable to generate response")
        response = "I ran into an internal error while processing that request."

    await cl.Message(content=response).send()


@cl.on_chat_resume
async def on_chat_resume(thread: ThreadDict):
    # Listen on_chat_resume event is required to let user continue the thread
    # even we do nothing in this event.

    # NOTE: The following logic just an example to extract messages from thread, you can customize it as you want
    try:
        await create_chat_settings().send()
        steps = thread.get("steps", [])
        restored: list[BaseMessage] = []
        for step in steps:
            step_type = step.get("type")
            content = (step.get("output") or "").strip()
            if not content:
                continue

            if step_type == "user_message":
                restored.append(HumanMessage(content=content))
            elif step_type == "assistant_message":
                restored.append(AIMessage(content=content))
        cl.user_session.set("history", restored)

    except Exception as e:

        print(f"\nError resuming chat: {e}")
        cl.user_session.set("state", {"messages": []})


# Authentication
@cl.oauth_callback
def oauth_callback(
    provider_id: str,
    token: str,
    raw_user_data: Dict[str, str],
    default_user: cl.User,
) -> Optional[cl.User]:
    print(raw_user_data)
    if provider_id == "google" and raw_user_data["hd"] == "wizeline.com":
        default_user.display_name = raw_user_data["name"]
        default_user.metadata.update({
            "name": raw_user_data["name"],
        })
        return default_user

    return None

@cl.data_layer
def get_data_layer():
    """
    Return chainlit's SQLAlchemyDataLayer.
    Note: This must be synchronous. Chainlit's SQLAlchemyDataLayer
    creates its own synchronous engine, so we use the sync connection string.
    """
    return SQLAlchemyDataLayer(conninfo=db_manager.DATABASE_URL, storage_provider=BaseStorageClient)


def _extract_response(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            content = message.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    else:
                        parts.append(str(block))
                return "\n".join(filter(None, parts))
            return str(content)

    return "I couldn't find a response to share yet."
