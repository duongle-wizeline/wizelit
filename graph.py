from __future__ import annotations

import json
from typing import Iterable, Sequence

from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import SystemMessage, AIMessage, ToolMessage
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
        # Normalize tool messages to fix Bedrock validation issues on subsequent calls
        normalized_history = _normalize_tool_messages(history)
        response = await llm_with_tools.ainvoke(normalized_history)
        return {"messages": [response]}

    async def generate(state: MessagesState):
        """Generate the final answer using any newly retrieved context."""

        # 1. Capture Tool Outputs
        tool_messages = _gather_recent_tool_messages(state.get("messages", []))

        # Special handling for get_job_status: extract and format job status information
        for message in tool_messages:
            if isinstance(message, ToolMessage) and message.name == "get_job_status":
                job_data = _extract_job_status_content(message.content)
                if job_data:
                    # Format the response based on job status
                    response_text = _format_job_status_response(job_data)
                    return {"messages": [AIMessage(content=response_text)]}
                # If extraction fails, continue with normal flow

        docs_content = "\n\n".join(
            _stringify_tool_message(msg) for msg in tool_messages
        )

        # 2. STRICT System Prompt
        system_message_content = (
            "You are Wizelit, an Engineering Manager with two distinct toolsets:\n\n"
            "CODE SCOUT (Analysis Only):\n"
            "- code_scout_symbol_usage: Find where symbols (functions/classes/variables) are defined or used\n"
            "- code_scout_grep: Fast text search across codebases\n"
            "Use these for: 'find usages', 'where is X used', 'search for', 'analyze dependencies'\n\n"
            "REFACTORING CREW (Code Changes Only):\n"
            "- start_refactoring_job: Modify/refactor code snippets\n"
            "- get_job_status: Check refactoring job progress\n"
            "Use these ONLY for: 'refactor this code', 'improve this code', 'rewrite'\n\n"
            "Rules:\n"
            "1) For analysis/search requests → Use Code Scout tools and summarize findings.\n"
            "2) For code modification requests → Use start_refactoring_job and respond ONLY with: 'I have started the job. JOB_ID: <the_id>.'\n"
            "3) Never write Python code yourself.\n"
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

    """Convert the graph to a Mermaid string."""
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


def _extract_job_status_content(content) -> dict | None:
    """
    Safely extract job status data from ToolMessage content.

    Handles multiple content formats:
    - JSON string (after normalization)
    - List with dict elements (LangChain format: [{"text": "..."}])
    - Dict (direct format)
    - String (plain text)

    Returns the parsed job status dict, or None if extraction fails.
    """
    try:
        # Case 1: Content is a JSON string (after _normalize_tool_messages)
        if isinstance(content, str):
            # Try to parse as JSON
            try:
                parsed = json.loads(content)
                # If parsed is a dict with job status keys, return it
                if isinstance(parsed, dict) and "status" in parsed:
                    return parsed
                # If parsed is a string that looks like JSON, try parsing again
                if isinstance(parsed, str):
                    parsed = json.loads(parsed)
                    if isinstance(parsed, dict) and "status" in parsed:
                        return parsed
            except (json.JSONDecodeError, TypeError):
                # Not JSON, continue to other cases
                pass

        # Case 2: Content is a list (LangChain format: [{"text": "..."}])
        if isinstance(content, list) and len(content) > 0:
            first_item = content[0]
            # Check if it's a dict with "text" key
            if isinstance(first_item, dict) and "text" in first_item:
                text_content = first_item["text"]
                # Try to parse the text as JSON
                try:
                    parsed = (
                        json.loads(text_content)
                        if isinstance(text_content, str)
                        else text_content
                    )
                    if isinstance(parsed, dict) and "status" in parsed:
                        return parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            # Or if the list item itself is a dict with status
            elif isinstance(first_item, dict) and "status" in first_item:
                return first_item

        # Case 3: Content is a dict directly
        if isinstance(content, dict) and "status" in content:
            return content

        # Case 4: Content might be a list of dicts, check first element
        if isinstance(content, list) and len(content) > 0:
            if isinstance(content[0], dict) and "status" in content[0]:
                return content[0]

    except (IndexError, KeyError, AttributeError, TypeError) as e:
        # Log the error for debugging but don't crash
        print(f"Warning: Failed to extract job status content: {e}")
        return None

    return None


def _format_job_status_response(job_data: dict) -> str:
    """
    Format job status data into a human-readable response for the LLM.

    Args:
        job_data: Dictionary with job status information (status, logs, result, error)

    Returns:
        Formatted string response
    """
    status = job_data.get("status", "unknown")

    if status == "completed":
        result = job_data.get("result", {})
        logs = job_data.get("logs", "")

        response_parts = ["✅ **Job Completed**"]

        if logs:
            # Include last few lines of logs
            log_lines = logs.strip().split("\n")
            if len(log_lines) > 5:
                response_parts.append(
                    f"**Recent logs:**\n```\n" + "\n".join(log_lines[-5:]) + "\n```"
                )
            else:
                response_parts.append(f"**Logs:**\n```\n{logs}\n```")

        if result:
            if isinstance(result, dict):
                if "html" in result:
                    response_parts.append("Result includes HTML diff viewer.")
                elif "code" in result:
                    response_parts.append(
                        f"**Refactored Code:**\n```python\n{result.get('code', '')}\n```"
                    )
                elif "text" in result:
                    response_parts.append(f"**Result:**\n{result.get('text', '')}")
                else:
                    response_parts.append(
                        f"**Result:**\n{json.dumps(result, indent=2)}"
                    )
            elif isinstance(result, str):
                response_parts.append(f"**Result:**\n{result}")

        return "\n\n".join(response_parts)

    elif status == "failed":
        error = job_data.get("error", "Unknown error")
        logs = job_data.get("logs", "")

        response_parts = [f"❌ **Job Failed:** {error}"]

        if logs:
            log_lines = logs.strip().split("\n")
            if len(log_lines) > 5:
                response_parts.append(
                    f"**Recent logs:**\n```\n" + "\n".join(log_lines[-5:]) + "\n```"
                )
            else:
                response_parts.append(f"**Logs:**\n```\n{logs}\n```")

        return "\n\n".join(response_parts)

    elif status == "running":
        logs = job_data.get("logs", "")

        response_parts = ["⏳ **Job is still running...**"]

        if logs:
            log_lines = logs.strip().split("\n")
            if len(log_lines) > 10:
                response_parts.append(
                    f"**Recent logs:**\n```\n" + "\n".join(log_lines[-10:]) + "\n```"
                )
            else:
                response_parts.append(f"**Logs:**\n```\n{logs}\n```")

        return "\n\n".join(response_parts)

    else:
        # Unknown status
        return f"**Job Status:** {status}\n{json.dumps(job_data, indent=2)}"


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
