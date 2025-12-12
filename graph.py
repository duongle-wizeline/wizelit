from __future__ import annotations

from typing import Iterable, Sequence

from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import SystemMessage, AIMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition


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
        history = state.get("messages", [])
        response = await llm_with_tools.ainvoke(history)
        return {"messages": [response]}

    async def generate(state: MessagesState):
        """Generate the final answer using any newly retrieved context."""
        
        # 1. Capture Tool Outputs
        tool_messages = _gather_recent_tool_messages(state.get("messages", []))
        docs_content = "\n\n".join(_stringify_tool_message(msg) for msg in tool_messages)
        
        # 2. STRICT System Prompt
        system_message_content = (
            "You are Wizelit, an Engineering Manager. "
            "You CANNOT write or refactor code yourself. "
            "Your ONLY job is to delegate tasks to the 'Refactoring Crew'.\n\n"
            "CRITICAL RULES:\n"
            "1. If you receive a 'JOB_ID' from a tool, you must ONLY output that ID.\n"
            "2. Do NOT write python code. Do NOT explain the refactoring.\n"
            "3. Format your response exactly like this: 'I have started the job. JOB_ID: <the_id>.'\n"
            f"\n\nCONTEXT FROM TOOLS:\n{docs_content}"
        )

        conversation_messages = [
            message
            for message in state["messages"]
            if message.type in ("human", "system")
            or (message.type == "ai" and not message.tool_calls)
        ]
        
        # Force the System Prompt to be the last thing the model considers
        prompt = [SystemMessage(content=system_message_content)] + conversation_messages
        response = await llm.ainvoke(prompt)
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
    return builder.compile(checkpointer=memory)


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