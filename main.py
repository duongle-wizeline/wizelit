import uuid
import chainlit as cl
from typing import List, Dict, Optional
from database import DatabaseManager
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.data.storage_clients.base import BaseStorageClient
from chainlit.types import ThreadDict
from utils import create_chat_settings
# Initialize database manager (one-time)
db_manager = DatabaseManager()


@cl.on_app_startup
async def on_startup():
    """
    Initialize database before the Chainlit app starts accepting connections.
    """
    await db_manager.init_db()

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
    settings = await create_chat_settings().send()

@cl.on_message
async def main(message: cl.Message):
    # Your custom logic goes here...

    # Send a response back to the user
    await cl.Message(
        content=f"Received: {message.content}",
    ).send()


@cl.on_chat_resume
async def on_chat_resume(thread: ThreadDict):
    # Listen on_chat_resume event is required to let user continue the thread
    # even we do nothing in this event.

    # NOTE: The following logic just an example to extract messages from thread, you can customize it as you want
    try:
        settings = await create_chat_settings().send()
        steps = thread.get("steps", [])
        messages = []
        print(steps)
        for step in steps:
            step_type = step.get("type")
            content = (step.get("output") or "").strip()
            if not content:
                continue  # skip empty rows

            if step_type == "user_message":
                messages.append(
                    {
                        "role": "user",
                        "content": content,
                    }
                )
            elif step_type == "assistant_message":
                messages.append({
                    "role": "assistant",
                    "content": content,
                })
        print(messages)

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
