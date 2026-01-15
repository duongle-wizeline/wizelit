from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import SystemMessage, AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from utils.prompt_guides import prompt_guides
print(f"\n{prompt_guides}\n")

def build_graph(
    llm: BaseLanguageModel,
    tools: Sequence[BaseTool] | None = None,
):
    """Compile the LangGraph agent with the provided language model and tools."""

    tool_list = list(tools or [])
    llm_with_tools = llm.bind_tools(tool_list) if tool_list else llm
    memory = MemorySaver()

    async def query_or_respond(state: MessagesState):
        """Let the model decide whether it needs to call a tool."""
        system_message_content = f"{prompt_guides}"
        history = state.get("messages", [])
        prompt = [SystemMessage(content=system_message_content)] + history
        # Normalize tool messages to fix Bedrock validation issues on subsequent calls
        normalized_prompt = _normalize_tool_messages(prompt)
        response = await llm_with_tools.ainvoke(normalized_prompt)
        return {"messages": [response]}

    async def generate(state: MessagesState):
        """Generate the final answer using any newly retrieved context."""

        # 1. Capture Tool Outputs
        tool_messages = _gather_recent_tool_messages(state.get("messages", []))

        for message in tool_messages:
            if isinstance(message, ToolMessage) and message.name == "get_job_status":
                return {"messages": [AIMessage(content=message.content[0]["text"])]}
            elif isinstance(message, ToolMessage) and message.name == "start_refactoring_job":
                return {"messages": [AIMessage(content=f"Refactoring job has started. JOB_ID: {message.content[0]["text"]}.")]}

        docs_content = "\n\n".join(
            _stringify_tool_message(msg) for msg in tool_messages
        )

        # 2. STRICT System Prompt
        system_message_content = (
            f"{prompt_guides}"
            f"\n\nCONTEXT FROM TOOLS:\n{docs_content}"
        )

        print(f"\n🧠 [Graph] System Prompt:\n{system_message_content}\n")

        conversation_messages = [
            message
            for message in state["messages"]
            if message.type in ("human", "system")
            or (message.type == "ai" and not message.tool_calls)
        ]

        # Force the System Prompt to be the last thing the model considers
        prompt = [SystemMessage(content=system_message_content)] + conversation_messages
        # Normalize tool messages before sending to Bedrock
        normalized_prompt = _normalize_tool_messages(prompt)
        response = await llm.ainvoke(normalized_prompt)
        return {"messages": [response]}

    builder = StateGraph(MessagesState)
    builder.add_node("query_or_respond", query_or_respond)
    builder.add_node("generate", generate)
    builder.set_entry_point("query_or_respond")

    if tool_list:
        tool_node = ToolNode(tool_list)
        builder.add_node("tools", tool_node)
        builder.add_conditional_edges(
            "query_or_respond",
            tools_condition,
            {END: END, "tools": "tools"},
        )
        builder.add_edge("tools", "generate")
    else:
        builder.add_edge("query_or_respond", "generate")

    builder.add_edge("generate", END)

    compiled_graph = builder.compile(checkpointer=memory)
    return compiled_graph


def _gather_recent_tool_messages(messages: Iterable) -> list:
    recent_tool_messages = []
    for message in reversed(list(messages)):
        if getattr(message, "type", None) == "tool":
            recent_tool_messages.append(message)
        else:
            break
    return list(reversed(recent_tool_messages))


def _stringify_tool_message(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return str(content)


def _normalize_tool_messages(messages: list) -> list:
    """
    Normalize tool messages to fix Bedrock validation issues.

    When MCP tools return complex content structures with extra fields like 'id',
    Bedrock rejects them with ValidationException. This function converts tool
    result content to plain strings.
    """
    normalized = []
    for message in messages:
        if isinstance(message, ToolMessage):
            # Convert tool message content to a plain string if it's a complex structure
            content = message.content
            if isinstance(content, (list, dict)):
                # Convert complex structures to string representation
                string_content = json.dumps(content)
            else:
                string_content = content

            # Create a new ToolMessage with normalized string content
            normalized_msg = ToolMessage(
                content=string_content,
                tool_call_id=getattr(message, "tool_call_id", None),
                name=getattr(message, "name", None),
            )
            normalized.append(normalized_msg)
        else:
            normalized.append(message)
    return normalized

