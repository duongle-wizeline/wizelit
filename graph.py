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
from utils.tool_response_handler import ToolResponseHandler

print(f"\n{prompt_guides}\n")

# Initialize tool response handler (module-level singleton)
_tool_response_handler = ToolResponseHandler()


def build_graph(
    llm: BaseLanguageModel,
    tools: Sequence[BaseTool] | None = None,
    tool_response_handler: ToolResponseHandler | None = None,
):
    """
    Compile the LangGraph agent with the provided language model and tools.

    Args:
        llm: Language model to use
        tools: List of tools available to the agent
        tool_response_handler: Optional custom tool response handler (uses default if None)
    """
    # Use provided handler or default module-level handler
    handler = tool_response_handler or _tool_response_handler

    tool_list = list(tools or [])
    llm_with_tools = llm.bind_tools(tool_list) if tool_list else llm
    memory = MemorySaver()

    async def query_or_respond(state: MessagesState):
        """Let the model decide whether it needs to call a tool."""
        system_message_content = f"{prompt_guides}"
        history = state.get("messages", [])

        # Filter messages to ensure proper role alternation and tool_use/tool_result pairing
        # Bedrock requires: tool_use blocks must be immediately followed by tool_result blocks
        filtered_history = []
        i = 0
        while i < len(history):
            msg = history[i]
            msg_type = getattr(msg, "type", None)

            # Always include human and system messages
            if msg_type in ("human", "system"):
                filtered_history.append(msg)
                i += 1
            # Handle AI messages
            elif msg_type == "ai":
                ai_msg = msg
                has_tool_calls = bool(getattr(ai_msg, "tool_calls", None))

                # If AI message has tool_calls, we must include it AND its tool results
                if has_tool_calls:
                    filtered_history.append(ai_msg)
                    i += 1
                    # Include all following tool messages that match this AI message's tool calls
                    tool_call_ids = {tc.get("id") for tc in ai_msg.tool_calls}
                    while i < len(history):
                        next_msg = history[i]
                        if getattr(next_msg, "type", None) == "tool":
                            tool_msg = next_msg
                            if getattr(tool_msg, "tool_call_id", None) in tool_call_ids:
                                filtered_history.append(tool_msg)
                                i += 1
                            else:
                                break
                        else:
                            break
                else:
                    # AI message without tool_calls - only include if last message wasn't also AI
                    if not filtered_history or filtered_history[-1].type != "ai":
                        filtered_history.append(ai_msg)
                    i += 1
            # Always include tool messages (they're handled above when following tool_use)
            elif msg_type == "tool":
                filtered_history.append(msg)
                i += 1
            else:
                i += 1

        prompt = [SystemMessage(content=system_message_content)] + filtered_history
        # Normalize tool messages to fix Bedrock validation issues on subsequent calls
        normalized_prompt = _normalize_tool_messages(prompt)
        response = await llm_with_tools.ainvoke(normalized_prompt)

        # Log whether the LLM is using tool calling
        if hasattr(response, "tool_calls") and response.tool_calls:
            print(
                f"✅ [Graph] LLM is calling {len(response.tool_calls)} tool(s): {[tc.get('name') for tc in response.tool_calls]}"
            )
        else:
            content_preview = (
                str(response.content)[:200]
                if hasattr(response, "content")
                else "No content"
            )
            print(
                f"⚠️ [Graph] LLM generated text instead of tool calls. Content preview: {content_preview}"
            )
            # Generic check: If LLM generated text instead of tool calls, and tools are available,
            # check if the content looks like execution code/commands (not just conversational text)
            if hasattr(response, "content") and tool_list:
                import re

                content_str = str(response.content).strip()

                # Generic patterns that indicate execution attempts (works for any agent):
                # 1. Function call syntax: function_name(...)
                function_call_pattern = r"^\w+\s*\([^)]*\)\s*$"
                # 2. Code blocks: ```python, import statements, etc.
                code_block_pattern = (
                    r"```python|```\s*\w+\s*\(|from\s+\w+\s+import|import\s+\w+"
                )
                # 3. Command-like patterns: word followed by flags or URLs (generic, not tool-specific)
                # This catches patterns like "command -flag" or "command://url" without hardcoding specific commands
                execution_pattern = r"\b\w+\s+(-[a-zA-Z]|--[a-zA-Z-]+|\w+://)"

                # Only filter if it looks like execution code/commands, not conversational text
                if (
                    re.match(function_call_pattern, content_str)
                    or re.search(code_block_pattern, content_str, re.IGNORECASE)
                    or re.search(execution_pattern, content_str)
                ):
                    print(
                        f"❌ [Graph] LLM generated code/command syntax instead of using tool calling API. Content: {content_str[:200]}. Filtering out."
                    )
                    # Return a message that prompts the LLM to use tools instead
                    return {
                        "messages": [
                            AIMessage(
                                content="I need to use the available tools to complete this request. Let me call the appropriate tool instead of generating code or commands."
                            )
                        ]
                    }

        return {"messages": [response]}

    async def generate(state: MessagesState):
        """Generate the final answer using any newly retrieved context."""

        # 1. Capture Tool Outputs
        tool_messages = _gather_recent_tool_messages(state.get("messages", []))
        print(
            f"🔍 [Graph] generate() called. Found {len(tool_messages)} tool message(s)"
        )

        # 2. Extract tool output content FIRST (before handler logic)
        docs_content_parts = []
        for msg in tool_messages:
            extracted = _stringify_tool_message(msg)
            if extracted and extracted.strip():
                docs_content_parts.append(extracted)
            else:
                print(
                    f"⚠️ [Graph] Tool message {getattr(msg, 'name', 'unknown')} extracted empty content. Raw content: {str(getattr(msg, 'content', ''))[:200]}"
                )

        docs_content = "\n\n".join(docs_content_parts)
        print(
            f"🔍 [Graph] Found {len(tool_messages)} tool message(s). Extracted {len(docs_content_parts)} non-empty parts. docs_content length: {len(docs_content)}, preview: {docs_content[:200]}"
        )

        # Log all tool names for debugging
        if tool_messages:
            tool_names = [
                msg.name for msg in tool_messages if isinstance(msg, ToolMessage)
            ]
            print(f"🔍 [Graph] Tool names: {tool_names}")

        # 3. CRITICAL: If we have tool outputs with actual content, ALWAYS show them directly
        # This prevents LLM from generating descriptions or explanations
        # We do this FIRST, before checking handler metadata, to ensure stability
        has_tool_output = tool_messages and docs_content and docs_content.strip()
        print(
            f"🔍 [Graph] has_tool_output: {has_tool_output} (tool_messages: {bool(tool_messages)}, docs_content exists: {bool(docs_content)}, docs_content.strip: {bool(docs_content.strip() if docs_content else False)})"
        )

        if has_tool_output:
            print(
                f"✅ [Graph] Tool outputs detected with content. Attempting to show directly."
            )

            # Try handler first (for proper formatting if metadata is available)
            # Force refresh metadata before checking to ensure it's up to date
            # This ensures metadata is fresh even if servers were added via UI
            handler.refresh_metadata()
            handler_worked = False
            for message in tool_messages:
                if isinstance(message, ToolMessage):
                    tool_name = message.name
                    print(f"🔍 [Graph] Processing tool: {tool_name}")
                    print(
                        f"🔍 [Graph] Tool message content type: {type(message.content)}, preview: {str(message.content)[:200]}"
                    )

                    should_handle = handler.should_handle_directly(tool_name)
                    print(
                        f"🔍 [Graph] Tool: {tool_name}, should_handle_directly: {should_handle}"
                    )

                    if should_handle:
                        print(
                            f"🔧 [Graph] Tool {tool_name} should be handled directly. Using handler."
                        )
                        response = handler.handle_tool_response(message)
                        if (
                            response
                            and response.content
                            and str(response.content).strip()
                        ):
                            print(
                                f"✅ [Graph] Handler returned response for {tool_name}: {str(response.content)[:200]}"
                            )
                            handler_worked = True
                            return {"messages": [response]}

            # If handler didn't work (metadata missing, handler failed, or tool has no direct mode),
            # show raw output directly - this is ALWAYS better than letting LLM generate descriptions
            if not handler_worked:
                print(
                    f"⚠️ [Graph] Handler didn't intercept. Showing tool output directly to prevent LLM descriptions."
                )
                return {"messages": [AIMessage(content=docs_content)]}

        # If no tool outputs, let LLM respond normally
        # But if we somehow reach here with tool outputs (shouldn't happen, but safety check)
        if tool_messages and docs_content and docs_content.strip():
            print(
                f"⚠️ [Graph] WARNING: Reached LLM processing with tool outputs! This shouldn't happen. Showing tool output directly instead."
            )
            return {"messages": [AIMessage(content=docs_content)]}

        system_message_content = (
            f"{prompt_guides}"
            f"\n\nCRITICAL INSTRUCTIONS FOR TOOL RESULTS:\n"
            f"- When a tool returns results, display the tool output EXACTLY as returned\n"
            f"- Do NOT add any introductory text like 'The tool found...' or 'Here are the results...'\n"
            f"- Do NOT summarize, explain, or rephrase the tool output\n"
            f"- Do NOT wrap the output in explanatory sentences\n"
            f"- Simply show the raw tool output directly to the user\n"
            f"- The tool output is already formatted and ready to display\n"
            f"\nCONTEXT FROM TOOLS:\n{docs_content}"
        )

        print(f"\n🧠 [Graph] System Prompt:\n{system_message_content}\n")

        # Filter messages to ensure proper role alternation and avoid consecutive assistant messages
        conversation_messages = []
        last_type = None
        for message in state["messages"]:
            msg_type = message.type
            # Include human, system, and AI messages without tool_calls
            if msg_type in ("human", "system"):
                conversation_messages.append(message)
                last_type = msg_type
            elif msg_type == "ai" and not getattr(message, "tool_calls", None):
                # Only add AI message if last message wasn't also AI (avoid consecutive assistants)
                if last_type != "ai":
                    conversation_messages.append(message)
                    last_type = "ai"

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
    """Convert tool message content to string, handling MCP format."""
    content = getattr(message, "content", "")

    # Handle MCP format: [{'type': 'text', 'text': 'value'}]
    if isinstance(content, list) and len(content) > 0:
        first_item = content[0]
        if isinstance(first_item, dict):
            # Try 'text' key first (MCP format)
            if "text" in first_item:
                return first_item["text"]
            # Fallback to 'result' key
            elif "result" in first_item:
                return str(first_item["result"])
            # Fallback to first string value
            for v in first_item.values():
                if isinstance(v, str):
                    return v

    if isinstance(content, str):
        return content

    # For dict or other types, convert to JSON string
    if isinstance(content, (dict, list)):
        import json

        return json.dumps(content, indent=2)

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
