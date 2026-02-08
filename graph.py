from __future__ import annotations

import asyncio
import json
import os
import logging
from typing import Iterable, Sequence, Optional

from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import SystemMessage, AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from utils.tool_response_handler import ToolResponseHandler

logger = logging.getLogger(__name__)
behavior_rules = """
CRITICAL BEHAVIORAL RULES:
1) TOOL USAGE IS PURPOSE-DRIVEN: Only call tools when the user's request matches a tool's purpose (as described in the tool's description above). If the request does NOT match any tool's purpose, respond directly using your knowledge (NO tools).
2) TOOL SELECTION: Read tool descriptions carefully. Choose the tool that best matches the user's request based on its description.
3) WORK WITH TOOLS THAT REQUIRE EXISTING RESOURCES: If a tool's description says it works with EXISTING resources (e.g., 'refactors EXISTING code', 'analyzes EXISTING codebase'), but the user hasn't provided any existing resources, DO NOT use that tool. Respond directly using your knowledge instead.
4) PREFER FORMATTED TOOLS: If multiple tools exist for the same purpose, prefer the one that returns formatted human-readable text. Avoid tools marked as '[RAW JSON - DO NOT USE]' or 'Returns raw JSON'.
5) CLARIFYING QUESTIONS - If the user's request is ambiguous and could match multiple tools, ask a clarifying question to determine the best tool to use before proceeding.
"""

def get_prompt_template(guides: str) -> str:
    return (
        "You are Wizelit, an Engineering Manager assistant.\n"
        f"{guides}\n"
        f"{behavior_rules}\n"
    )

async def generate_tools_guides(tools: Sequence[BaseTool] | None = None) -> str:
    """Generate prompt guides from in-memory MCP server storage"""
    n8n_search_workflows = None
    workflows = []

    guides = "You have access to the following tools:\n" if tools else ""
    count = 0
    for tool in tools or []:
        count += 1
        guides += f"{count}. Use tool `{tool.name}` for purpose: {tool.description}\n"
        if tool.name == "search_workflows":
            n8n_search_workflows = tool

    if n8n_search_workflows:
        n8n_response = await n8n_search_workflows.ainvoke({})
        response_content = json.loads(n8n_response[0].get('text', '{}'))
        workflows = response_content.get('data', [])
        print(f"\nN8N WORKFLOWS EXTRACTED: {workflows}\n")

        if workflows:
            guides += "\nThis is the list of N8N workflows:\n"
            ncount = 0
            for workflow in workflows:
                ncount += 1
                guides += f"{ncount}. Use tool `{workflow['name']}` - id `{workflow['id']}` - for purpose: {workflow.get('description')}\n"
            guides += "IMPORTANT: To invoke an N8N workflow, use the tool \"execute_workflow\" with the workflow's ID in the tool call.\n"

    print(f"\nGENERATED PROMPT GUIDES:\n{guides}\n")

    return get_prompt_template(guides)


# Initialize tool response handler (module-level singleton)
_tool_response_handler = ToolResponseHandler()


def _get_current_user_id() -> Optional[str]:
    """
    Try to get the current user ID from Chainlit context.

    Returns None if we're not in a Chainlit context (e.g., testing).
    """
    try:
        import chainlit as cl
        session = cl.context.session
        if session:
            # Try stored user_id first
            stored_id = cl.user_session.get("user_id")
            if stored_id:
                return stored_id
            # Try user object
            if hasattr(session, 'user') and session.user:
                user = session.user
                if hasattr(user, 'identifier') and user.identifier:
                    return user.identifier
                if hasattr(user, 'id') and user.id:
                    return user.id
            # Try client_id or session.id
            if hasattr(session, 'client_id') and session.client_id:
                return session.client_id
            if hasattr(session, 'id') and session.id:
                return session.id
    except Exception as e:
        logger.debug(f"Could not get user_id from Chainlit context: {e}")
    return None

# Maximum number of conversation turns to keep in history
# A turn = human message + AI response + tool calls/results
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "10"))

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
    prompt_guides = asyncio.run(generate_tools_guides(tools=tools))

    def truncate_history(messages: list) -> list:
        """
        Truncate message history to keep only the most recent conversation turns.
        Preserves system messages and the last N conversation turns.
        IMPORTANT: Ensures tool_use/tool_result pairs are kept together for Bedrock compatibility.
        """
        if (
            len(messages) <= MAX_HISTORY_TURNS * 2
        ):  # Rough estimate: 2 messages per turn
            return messages

        # Separate system messages from conversation messages
        system_messages = [
            msg for msg in messages if getattr(msg, "type", None) == "system"
        ]
        conversation_messages = [
            msg for msg in messages if getattr(msg, "type", None) != "system"
        ]

        # If we have too many messages, keep only the most recent ones
        if (
            len(conversation_messages) > MAX_HISTORY_TURNS * 3
        ):  # Allow for tool messages
            # Keep the most recent messages (last N turns)
            # A turn typically includes: human, ai, and possibly tool messages
            # We'll keep the last MAX_HISTORY_TURNS * 3 messages to account for tool calls
            truncated = conversation_messages[-(MAX_HISTORY_TURNS * 3) :]
            print(
                f"⚠️ [Graph] Truncated message history from {len(conversation_messages)} to {len(truncated)} messages (keeping last {MAX_HISTORY_TURNS} turns)"
            )
            return system_messages + truncated

        return messages

    async def query_or_respond(state: MessagesState):
        """Let the model decide whether it needs to call a tool."""
        history = state.get("messages", [])

        # Check if we have tool messages in recent history (meaning we just processed tool results)
        recent_tool_messages = _gather_recent_tool_messages(history)
        has_recent_tool_results = bool(recent_tool_messages)

        if has_recent_tool_results:
            # Tool results have been shown to the user
            # LLM should use them to decide next steps, NOT summarize them
            # Extract tool results for context
            tool_results_text = "\n\n".join(
                _stringify_tool_message(msg) for msg in recent_tool_messages
            )
            # Check if user's original request suggests multiple steps
            original_request = ""
            if history:
                for msg in reversed(history):
                    if hasattr(msg, "type") and msg.type == "human":
                        original_request = str(getattr(msg, "content", ""))
                        break

            # Determine if more tools are likely needed
            multi_step_indicators = [
                "then",
                "next",
                "after",
                "also",
                "and then",
                "followed by",
            ]
            likely_needs_more_tools = any(
                indicator in original_request.lower()
                for indicator in multi_step_indicators
            )

            if likely_needs_more_tools:
                system_message_content = (
                    f"{prompt_guides}"
                    f"\n\nCRITICAL: You have executed a tool and the results are shown below."
                    f" The user's request requires MULTIPLE steps/tools: {original_request}"
                    f" You MUST call the NEXT tool immediately using the tool calling API."
                    f" Do NOT generate text, do NOT summarize, do NOT explain - JUST CALL THE NEXT TOOL."
                    f" Look at the user's request again - what is the next step? Call that tool now."
                    f"\n\nTOOL RESULTS (use to proceed with next step):\n{tool_results_text}"
                )
            else:
                system_message_content = (
                    f"{prompt_guides}"
                    f"\n\nCRITICAL: Tool results have already been displayed to the user."
                    f" Use them to decide your next action:"
                    f" - If more tools are needed, call them immediately using tool calling API"
                    f" - If the task is complete, provide a final answer"
                    f" - Do NOT summarize, explain, or rephrase the tool output"
                    f"\n\nTOOL RESULTS:\n{tool_results_text}"
                )
        else:
            system_message_content = f"{prompt_guides}"

        # Truncate history if it's too long to prevent "Input is too long" errors
        history = truncate_history(history)

        # Pre-check: Detect generation requests and prevent tool usage
        # This is a generic check that works for any agent
        if history:
            last_message = history[-1]
            if hasattr(last_message, "content") and last_message.content:
                user_request = str(last_message.content).lower()
                # Generic patterns that indicate generation requests (not tool-specific)
                generation_keywords = [
                    "give me sample",
                    "give me example",
                    "show me sample",
                    "show me example",
                    "create a sample",
                    "create an example",
                    "generate sample",
                    "generate example",
                    "provide sample",
                    "provide example",
                    "write sample",
                    "write example",
                    "sample code",
                    "example code",
                    "hello world",
                ]
                # Check if user is asking to generate something without providing existing resources
                is_generation_request = any(
                    keyword in user_request for keyword in generation_keywords
                )
                # Check if user provided existing resources (URLs, file paths, code snippets)
                has_existing_resources = any(
                    indicator in user_request
                    for indicator in [
                        "http://",
                        "https://",
                        "github.com",
                        "file://",
                        "path:",
                        "directory:",
                        "here is",
                        "here's",
                        "this code",
                        "my code",
                        "existing",
                    ]
                )

                # If it's a generation request without existing resources, use plain LLM without tools
                # This avoids Bedrock validation issues and ensures direct response generation
                if is_generation_request and not has_existing_resources:
                    print(
                        f"⚠️ [Graph] Detected generation request without existing resources. Using plain LLM (no tools) for direct response."
                    )
                    # Use plain LLM without tools for generation requests
                    # Filter messages to ensure proper role alternation
                    filtered_history = []
                    i = 0
                    while i < len(history):
                        msg = history[i]
                        msg_type = getattr(msg, "type", None)

                        # Include human and system messages
                        if msg_type in ("human", "system"):
                            filtered_history.append(msg)
                            i += 1
                        # Include AI messages without tool_calls
                        elif msg_type == "ai":
                            ai_msg = msg
                            if not getattr(ai_msg, "tool_calls", None):
                                if (
                                    not filtered_history
                                    or filtered_history[-1].type != "ai"
                                ):
                                    filtered_history.append(ai_msg)
                                i += 1
                            else:
                                # Skip AI messages with tool_calls for generation requests
                                i += 1
                        # Skip tool messages for generation requests
                        elif msg_type == "tool":
                            i += 1
                        else:
                            i += 1

                    prompt = [
                        SystemMessage(content=system_message_content)
                    ] + filtered_history
                    # Use plain LLM without tools
                    response = await llm.ainvoke(prompt)
                    return {"messages": [response]}

        # Filter messages to ensure proper role alternation and tool_use/tool_result pairing
        # Bedrock requires: tool_use blocks must be immediately followed by tool_result blocks
        # Bedrock also requires: roles must alternate between "user" and "assistant"
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
                    # AI message without tool_calls
                    # CRITICAL: After tool messages, we cannot have another AI message
                    # Tool messages don't break the user/assistant alternation
                    # Check the last non-system message to ensure proper alternation
                    last_non_system = None
                    for j in range(len(filtered_history) - 1, -1, -1):
                        if filtered_history[j].type != "system":
                            last_non_system = filtered_history[j]
                            break

                    # Don't add AI message if:
                    # 1. Last non-system message is AI (consecutive AI)
                    # 2. Last non-system message is tool (tool messages are part of assistant turn)
                    # EXCEPTION: If this AI message is a handler response after tool messages,
                    # we skip it but keep the tool messages so LLM can see results for next step
                    if last_non_system is None:
                        # No previous messages, safe to add
                        filtered_history.append(ai_msg)
                    elif last_non_system.type == "ai":
                        # Consecutive AI message - skip
                        print(
                            f"⚠️ [Graph] Skipping consecutive AI message to maintain role alternation"
                        )
                    elif last_non_system.type == "tool":
                        # Tool messages are part of assistant turn - cannot have another AI after
                        # This is likely a handler response - skip it but keep tool messages
                        # so LLM can see tool results and decide next step
                        print(
                            f"⚠️ [Graph] Skipping AI message (handler response) after tool messages. Tool messages will be included so LLM can see results for next step."
                        )
                    elif last_non_system.type == "human":
                        # Human message before AI - safe to add
                        filtered_history.append(ai_msg)
                    else:
                        # Unknown type - skip to be safe
                        print(
                            f"⚠️ [Graph] Skipping AI message after unknown message type: {last_non_system.type}"
                        )
                    i += 1
            # Tool messages are handled above when following tool_use
            # Skip orphaned tool messages (they should be paired with AI messages with tool_calls)
            elif msg_type == "tool":
                # Only include if it's not already handled above
                i += 1
            else:
                i += 1

        # CRITICAL: Bedrock doesn't allow AI messages at the end when tools are available
        # Also ensure no consecutive AI messages remain
        if filtered_history and tool_list:
            # Remove trailing AI messages without tool_calls
            while filtered_history:
                last_msg = filtered_history[-1]
                if hasattr(last_msg, "type") and last_msg.type == "ai":
                    if not getattr(last_msg, "tool_calls", None):
                        print(
                            f"⚠️ [Graph] Removing trailing AI message without tool_calls to avoid Bedrock validation error"
                        )
                        filtered_history = filtered_history[:-1]
                    else:
                        break
                else:
                    break

            # Final check: ensure no consecutive AI messages
            # CRITICAL: Never remove AI messages with tool_calls - they need their tool messages
            cleaned_history = []
            i = 0
            while i < len(filtered_history):
                msg = filtered_history[i]
                if msg.type == "system":
                    cleaned_history.append(msg)
                    i += 1
                elif msg.type == "ai":
                    ai_msg = msg
                    has_tool_calls = bool(getattr(ai_msg, "tool_calls", None))

                    if has_tool_calls:
                        # Always include AI messages with tool_calls and their tool messages
                        cleaned_history.append(ai_msg)
                        i += 1
                        # Include all following tool messages
                        tool_call_ids = {tc.get("id") for tc in ai_msg.tool_calls}
                        while i < len(filtered_history):
                            next_msg = filtered_history[i]
                            if getattr(next_msg, "type", None) == "tool":
                                tool_msg = next_msg
                                if (
                                    getattr(tool_msg, "tool_call_id", None)
                                    in tool_call_ids
                                ):
                                    cleaned_history.append(tool_msg)
                                    i += 1
                                else:
                                    break
                            else:
                                break
                    else:
                        # AI message without tool_calls
                        # CRITICAL: After tool messages, we cannot have another AI message
                        # Tool messages don't break the user/assistant alternation
                        # Check the last non-system message
                        last_non_system = None
                        for j in range(len(cleaned_history) - 1, -1, -1):
                            if cleaned_history[j].type != "system":
                                last_non_system = cleaned_history[j]
                                break

                        # Don't add AI message if:
                        # 1. Last non-system message is AI (consecutive AI)
                        # 2. Last non-system message is tool (tool messages are part of assistant turn)
                        if last_non_system is None:
                            # No previous messages, safe to add
                            cleaned_history.append(ai_msg)
                        elif last_non_system.type == "ai":
                            # Consecutive AI message - skip
                            print(
                                f"⚠️ [Graph] Skipping consecutive AI message (without tool_calls) to maintain role alternation"
                            )
                        elif last_non_system.type == "tool":
                            # Tool messages are part of assistant turn - cannot have another AI after
                            print(
                                f"⚠️ [Graph] Skipping AI message after tool messages (tool messages don't break user/assistant alternation)"
                            )
                        elif last_non_system.type == "human":
                            # Human message before AI - safe to add
                            cleaned_history.append(ai_msg)
                        else:
                            # Unknown type - skip to be safe
                            print(
                                f"⚠️ [Graph] Skipping AI message after unknown message type: {last_non_system.type}"
                            )
                        i += 1
                elif msg.type == "tool":
                    # Tool messages should be handled above when following AI with tool_calls
                    # If we encounter an orphaned tool message, skip it
                    print(
                        f"⚠️ [Graph] Skipping orphaned tool message (no preceding AI message with tool_calls)"
                    )
                    i += 1
                else:
                    cleaned_history.append(msg)
                    i += 1
            filtered_history = cleaned_history

        # CRITICAL: Bedrock requires the first message (after system) to be a user message
        # Ensure filtered_history starts with a human message
        if filtered_history:
            first_non_system_idx = -1
            for idx, msg in enumerate(filtered_history):
                if getattr(msg, "type", None) != "system":
                    first_non_system_idx = idx
                    break

            if first_non_system_idx >= 0:
                first_non_system = filtered_history[first_non_system_idx]
                if getattr(first_non_system, "type", None) != "human":
                    # First non-system message is not human - this will cause Bedrock validation error
                    # Find the most recent human message and ensure it's at the start
                    print(
                        f"⚠️ [Graph] First non-system message is not human (type: {getattr(first_non_system, 'type', 'unknown')}). "
                        f"Ensuring human message is first."
                    )
                    # Find the most recent human message
                    human_msg = None
                    for msg in reversed(filtered_history):
                        if getattr(msg, "type", None) == "human":
                            human_msg = msg
                            break

                    if human_msg:
                        # Reorder: put human message first (after system messages)
                        system_msgs = [
                            msg
                            for msg in filtered_history
                            if getattr(msg, "type", None) == "system"
                        ]
                        non_system_msgs = [
                            msg
                            for msg in filtered_history
                            if getattr(msg, "type", None) != "system"
                        ]
                        # Remove the human message from non_system_msgs if it's there
                        non_system_msgs = [
                            msg for msg in non_system_msgs if msg != human_msg
                        ]
                        # Reconstruct: system messages, then human message, then rest
                        filtered_history = system_msgs + [human_msg] + non_system_msgs
                    else:
                        # No human message found - this is a critical error
                        print(
                            f"❌ [Graph] ERROR: No human message found in filtered_history! This will cause Bedrock validation error."
                        )
                        # Try to get the original user message from state
                        if history:
                            for msg in reversed(history):
                                if getattr(msg, "type", None) == "human":
                                    # Add it at the start (after system messages)
                                    system_msgs = [
                                        msg
                                        for msg in filtered_history
                                        if getattr(msg, "type", None) == "system"
                                    ]
                                    non_system_msgs = [
                                        msg
                                        for msg in filtered_history
                                        if getattr(msg, "type", None) != "system"
                                    ]
                                    filtered_history = (
                                        system_msgs + [msg] + non_system_msgs
                                    )
                                    print(
                                        f"✅ [Graph] Recovered human message from original history"
                                    )
                                    break
            else:
                # Only system messages - need to add a human message
                print(
                    f"⚠️ [Graph] filtered_history contains only system messages. Looking for human message in original history."
                )
                if history:
                    for msg in reversed(history):
                        if getattr(msg, "type", None) == "human":
                            filtered_history.append(msg)
                            print(
                                f"✅ [Graph] Added human message from original history"
                            )
                            break
        else:
            # Empty filtered_history - this should not happen, but handle it
            print(
                f"⚠️ [Graph] filtered_history is empty. Looking for human message in original history."
            )
            if history:
                for msg in reversed(history):
                    if getattr(msg, "type", None) == "human":
                        filtered_history = [msg]
                        print(
                            f"✅ [Graph] Recovered human message from original history"
                        )
                        break

        # Debug: Check if tool messages are in the prompt
        tool_msgs_in_prompt = [
            msg for msg in filtered_history if getattr(msg, "type", None) == "tool"
        ]
        human_msgs_in_prompt = [
            msg for msg in filtered_history if getattr(msg, "type", None) == "human"
        ]
        print(
            f"🔍 [Graph] query_or_respond: filtered_history has {len(filtered_history)} messages, "
            f"including {len(tool_msgs_in_prompt)} tool message(s), {len(human_msgs_in_prompt)} human message(s)"
        )
        if tool_msgs_in_prompt:
            print(
                f"🔍 [Graph] Tool messages in prompt: {[getattr(msg, 'name', 'unknown') for msg in tool_msgs_in_prompt]}"
            )
        # When we have tool results, make it VERY clear what the next step is
        if has_recent_tool_results:
            # Extract the actual tool result content to show what was accomplished
            tool_result_content = (
                _stringify_tool_message(recent_tool_messages[0])
                if recent_tool_messages
                else ""
            )

            # Parse user request to identify next tool needed
            user_request = ""
            if human_msgs_in_prompt:
                user_request = (
                    str(human_msgs_in_prompt[-1].content)
                    if human_msgs_in_prompt
                    else ""
                )

            # Check what the next step should be based on user request
            next_tool_hint = ""
            if (
                "schema validator" in user_request.lower()
                or "validate" in user_request.lower()
            ):
                next_tool_hint = (
                    "validate_function_signature or validate_class_structure"
                )
            elif "format" in user_request.lower():
                next_tool_hint = "format_all or format_code"

            if next_tool_hint:
                # Try to extract formatted code from tool result
                formatted_code = ""
                try:
                    import json
                    import re

                    # Look for JSON in tool result
                    json_match = re.search(
                        r'\{.*?"formatted_code".*?\}', tool_result_content, re.DOTALL
                    )
                    if json_match:
                        tool_data = json.loads(json_match.group())
                        formatted_code = tool_data.get("formatted_code", "")
                except:
                    pass

                # Build explicit instruction with formatted code if available
                code_section = ""
                if formatted_code and "validate" in user_request.lower():
                    code_section = f"\n\nFORMATTED CODE TO VALIDATE:\n```python\n{formatted_code}\n```"
                    code_section += f"\n\nUse this code in the validate_function_signature tool call."

                system_message_content = (
                    f"{prompt_guides}"
                    f"\n\n🚨🚨🚨 CRITICAL - MULTI-STEP REQUEST 🚨🚨🚨"
                    f"\nUser's full request: {user_request}"
                    f"\n\nYou have completed step 1. Tool result preview:\n{tool_result_content[:300]}"
                    f"{code_section}"
                    f"\n\nYOU MUST NOW CALL THE NEXT TOOL: {next_tool_hint}"
                    f"\n\nDO NOT generate text. DO NOT summarize. DO NOT explain."
                    f"\nJUST CALL THE TOOL using the tool calling API immediately."
                    f"\nIf you see formatted_code above, use it as the 'code' parameter in your tool call."
                )
            elif "then" in user_request.lower() or "next" in user_request.lower():
                system_message_content += (
                    f"\n\n🚨 CRITICAL: User's request has multiple steps: {user_request}"
                    f"\nYou have completed one step. You MUST call the NEXT tool now."
                    f"\nDO NOT generate text - call the tool using tool calling API."
                )

        # CRITICAL: Bedrock requires the first message (after system) to be a user message
        # Ensure the prompt starts with system, then user message
        prompt = [SystemMessage(content=system_message_content)]

        # Separate system messages from other messages in filtered_history
        system_msgs_from_history = [
            msg for msg in filtered_history if getattr(msg, "type", None) == "system"
        ]
        non_system_msgs = [
            msg for msg in filtered_history if getattr(msg, "type", None) != "system"
        ]

        # Add system messages from history (if any) after the main system message
        prompt.extend(system_msgs_from_history)

        # Ensure the first non-system message is a human message
        if non_system_msgs:
            first_non_system = non_system_msgs[0]
            if getattr(first_non_system, "type", None) != "human":
                # Find the most recent human message
                human_msg = None
                for msg in reversed(non_system_msgs):
                    if getattr(msg, "type", None) == "human":
                        human_msg = msg
                        break

                if human_msg:
                    # Reorder: put human message first
                    non_system_msgs = [
                        msg for msg in non_system_msgs if msg != human_msg
                    ]
                    non_system_msgs = [human_msg] + non_system_msgs
                    print(
                        f"✅ [Graph] Reordered messages to ensure human message is first"
                    )
                else:
                    # No human message found - try to get from original history
                    print(
                        f"⚠️ [Graph] No human message in filtered_history. Looking in original history..."
                    )
                    if history:
                        for msg in reversed(history):
                            if getattr(msg, "type", None) == "human":
                                non_system_msgs = [msg] + non_system_msgs
                                print(
                                    f"✅ [Graph] Added human message from original history"
                                )
                                break

        prompt.extend(non_system_msgs)

        # Final validation: ensure first non-system message is human
        first_non_system_idx = -1
        for idx, msg in enumerate(prompt):
            if not isinstance(msg, SystemMessage):
                first_non_system_idx = idx
                break

        if first_non_system_idx >= 0:
            first_non_system = prompt[first_non_system_idx]
            if (
                not hasattr(first_non_system, "type")
                or first_non_system.type != "human"
            ):
                print(
                    f"❌ [Graph] ERROR: First non-system message is not human! Type: {getattr(first_non_system, 'type', 'unknown')}"
                )
                # This will cause Bedrock validation error, but we've done our best

        # Normalize tool messages to fix Bedrock validation issues on subsequent calls
        normalized_prompt = _normalize_tool_messages(prompt)
        response = await llm_with_tools.ainvoke(normalized_prompt)

        # Log whether the LLM is using tool calling
        if hasattr(response, "tool_calls") and response.tool_calls:
            # Validate tool calls - check if all tool names exist
            valid_tool_names = {tool.name for tool in tool_list} if tool_list else set()
            invalid_tool_calls = []
            valid_tool_calls = []

            for tc in response.tool_calls:
                tool_name = tc.get("name")
                if tool_name not in valid_tool_names:
                    invalid_tool_calls.append(tool_name)
                    print(
                        f"❌ [Graph] LLM tried to call invalid tool '{tool_name}'. Valid tools: {list(valid_tool_names)}"
                    )
                else:
                    valid_tool_calls.append(tc)

            # If there are invalid tool calls, return a message prompting direct response
            if invalid_tool_calls:
                print(
                    f"⚠️ [Graph] Invalid tool calls detected: {invalid_tool_calls}. Prompting LLM to respond directly."
                )
                return {
                    "messages": [
                        AIMessage(
                            content="I cannot use tools for this request. Let me respond directly using my knowledge instead."
                        )
                    ]
                }

            # Only log if all tool calls are valid
            if valid_tool_calls:
                print(
                    f"✅ [Graph] LLM is calling {len(valid_tool_calls)} tool(s): {[tc.get('name') for tc in valid_tool_calls]}"
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

            # Generic check: If no tools are available and LLM generated content that looks like a tool call attempt,
            # provide a helpful error message
            if hasattr(response, "content") and not tool_list:
                import re

                content_str = str(response.content).strip()

                # Generic patterns to detect tool call attempts (works for any tool format):
                # 1. JSON with "tool" and "args" fields
                json_tool_pattern = (
                    r'\{[^}]*"tool"\s*:\s*["\'][^"\']+["\'][^}]*"args"\s*:\s*\{'
                )
                # 2. JSON-like structures with tool/function/name fields
                json_like_pattern = (
                    r'\{[^}]*"(?:tool|function|name)"\s*:\s*["\'][^"\']+["\']'
                )
                # 3. Function call syntax: function_name(...) - generic, works for any function name
                function_call_pattern = r"\b\w+\s*\([^)]*\)"
                # 4. Code blocks with function calls: ```python function_name(...) ```
                code_block_with_call = r"```\s*\w*\s*\n?\s*\w+\s*\("
                # 5. Code-like patterns that suggest tool invocation attempts
                code_like_pattern = r"(?:```|function|call|invoke|execute)\s*\w+\s*\("

                # Check if content looks like a tool call attempt (any format)
                looks_like_tool_call = (
                    re.search(json_tool_pattern, content_str, re.IGNORECASE | re.DOTALL)
                    or re.search(
                        json_like_pattern, content_str, re.IGNORECASE | re.DOTALL
                    )
                    or re.search(function_call_pattern, content_str, re.IGNORECASE)
                    or re.search(code_block_with_call, content_str, re.IGNORECASE)
                    or re.search(code_like_pattern, content_str, re.IGNORECASE)
                )

                if looks_like_tool_call:
                    print(
                        f"⚠️ [Graph] LLM attempted to generate tool call but no tools are available. Content: {content_str[:200]}"
                    )
                    return {
                        "messages": [
                            AIMessage(
                                content="⚠️ **No Tools Available**: Your request requires tools, but no MCP servers are currently connected.\n\n"
                                "**To fix this:**\n"
                                "1. Go to the Chainlit UI settings\n"
                                "2. Add MCP servers that provide the tools you need\n"
                                "3. Ensure MCP servers are running and accessible\n"
                                "4. Retry your query\n\n"
                                "**Note:** MCP servers expose tools via HTTP endpoints. Configure them in the Chainlit UI settings."
                            )
                        ]
                    }

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

        # Truncate history to prevent "Input is too long" errors
        messages = state.get("messages", [])
        messages = truncate_history(messages)

        # 1. Capture Tool Outputs
        tool_messages = _gather_recent_tool_messages(messages)
        print(
            f"🔍 [Graph] generate() called. Found {len(tool_messages)} tool message(s)"
        )

        docs_content = "\n\n".join(
            _stringify_tool_message(msg) for msg in tool_messages
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

            # Get current user ID for per-user metadata lookup
            current_user_id = _get_current_user_id()
            print(f"🔍 [Graph] Current user_id: {current_user_id}")

            # Try handler first (for proper formatting if metadata is available)
            # Force refresh metadata before checking to ensure it's up to date
            # This ensures metadata is fresh even if servers were added via UI
            handler.refresh_metadata(user_id=current_user_id)
            handler_worked = False
            for message in tool_messages:
                if isinstance(message, ToolMessage):
                    tool_name = message.name
                    print(f"🔍 [Graph] Processing tool: {tool_name}")
                    print(
                        f"🔍 [Graph] Tool message content type: {type(message.content)}, preview: {str(message.content)[:200]}"
                    )

                    should_handle = handler.should_handle_directly(tool_name, user_id=current_user_id)
                    print(
                        f"🔍 [Graph] Tool: {tool_name}, should_handle_directly: {should_handle}"
                    )

                    if should_handle:
                        print(
                            f"🔧 [Graph] Tool {tool_name} should be handled directly. Using handler."
                        )
                        response = handler.handle_tool_response(message, user_id=current_user_id)
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
                # Try to format JSON nicely if the content is JSON
                formatted_content = docs_content
                try:
                    import json

                    # Try to parse as JSON and pretty-print it
                    parsed = json.loads(docs_content)
                    formatted_content = json.dumps(parsed, indent=2)
                    print(f"✅ [Graph] Formatted tool output as JSON")
                except (json.JSONDecodeError, ValueError):
                    # Not JSON, use as-is
                    pass
                return {"messages": [AIMessage(content=formatted_content)]}

        # If no tool outputs, let LLM respond normally
        # But if we somehow reach here with tool outputs (shouldn't happen, but safety check)
        if tool_messages and docs_content and docs_content.strip():
            print(
                f"⚠️ [Graph] WARNING: Reached LLM processing with tool outputs! This shouldn't happen. Showing tool output directly instead."
            )
            return {"messages": [AIMessage(content=docs_content)]}

        prompt_template = get_prompt_template("")

        # When we have tool results, the LLM should use them to decide next steps
        # (call more tools or provide final answer), NOT summarize them
        # Tool outputs are already shown to the user, so LLM should just use them for decision-making
        if tool_messages and docs_content and docs_content.strip():
            system_message_content = (
                f"{prompt_template}"
                f"\n\nCRITICAL INSTRUCTIONS FOR TOOL RESULTS:\n"
                f"- Tool results have already been shown to the user\n"
                f"- Use the tool results to decide your next action:\n"
                f"  * If more tools need to be called, call them immediately\n"
                f"  * If the task is complete, provide a final answer\n"
                f"- Do NOT summarize, explain, or rephrase the tool output\n"
                f"- Do NOT say 'The tool found...' or 'The results show...'\n"
                f"- Simply use the results to proceed with the next step\n"
                f"\nTOOL RESULTS (for reference):\n{docs_content}"
            )
        else:
            system_message_content = prompt_template

        print(f"\n🧠 [Graph] System Prompt:\n{system_message_content}\n")

        # Filter messages to ensure proper role alternation and avoid consecutive assistant messages
        conversation_messages = []
        last_type = None
        for message in messages:
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

        # Use custom condition to ensure early returns from query_or_respond go to END
        def custom_tools_condition(state: MessagesState):
            """Custom condition that checks for tool calls"""
            messages = state.get("messages", [])
            if messages:
                last_message = messages[-1]
                # Check if last message has tool_calls
                if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                    return "tools"
            # No tool calls - route to END (not generate) to avoid double LLM calls
            # This handles both early returns (generation requests) and normal responses without tools
            return END

        builder.add_conditional_edges(
            "query_or_respond",
            custom_tools_condition,
            {END: END, "tools": "tools"},
        )
        builder.add_edge("tools", "generate")

        # Conditional routing from generate:
        # - If user request has multi-step indicators ("then", "next", etc.), check if all steps are done
        # - If all steps appear complete, route to END
        # - Otherwise, route back to query_or_respond to continue
        # - Single-step requests always route to END
        def generate_condition(state: MessagesState):
            """Check if we should continue (multi-step) or end (single-step or all steps complete)"""
            messages = state.get("messages", [])

            # Find the original user request (most recent human message)
            original_request = ""
            most_recent_human_idx = -1
            # Iterate from the end to find the most recent human message
            for idx in range(len(messages) - 1, -1, -1):
                msg = messages[idx]
                if hasattr(msg, "type") and msg.type == "human":
                    original_request = str(getattr(msg, "content", ""))
                    most_recent_human_idx = idx
                    break

            # Check for explicit multi-step indicators (generic)
            multi_step_indicators = [
                "then",
                "next",
                "after",
                "also",
                "and then",
                "followed by",
            ]
            is_multi_step = any(
                indicator in original_request.lower()
                for indicator in multi_step_indicators
            )

            # Generic multi-step detection: look for multiple distinct action verbs/requests
            # This works for any tools, not just specific ones
            if not is_multi_step:
                import re

                # Common action verbs that indicate tool usage (generic list)
                # These are domain-agnostic and work for any type of agent/tool
                action_verbs = [
                    r"\b(find|search|look|locate|grep|scan|seek)\b",
                    r"\b(refactor|refactoring|improve|optimize|clean|enhance)\b",
                    r"\b(format|formatting|style|indent|beautify)\b",
                    r"\b(validate|validation|check|verify|test|inspect)\b",
                    r"\b(analyze|analysis|examine|review|audit)\b",
                    r"\b(generate|create|build|make|write|produce)\b",
                    r"\b(convert|transform|translate|change|modify)\b",
                    r"\b(fix|repair|correct|resolve|debug)\b",
                    r"\b(organize|sort|arrange|structure)\b",
                    r"\b(extract|parse|process|handle)\b",
                ]

                request_lower = original_request.lower()
                found_actions = set()

                for pattern in action_verbs:
                    if re.search(pattern, request_lower):
                        # Extract the base verb (first word of the pattern)
                        base_verb = (
                            pattern.split("|")[0]
                            .replace(r"\b", "")
                            .replace("(", "")
                            .replace(")", "")
                        )
                        found_actions.add(base_verb)

                # If we found 2+ distinct action verbs, it's likely multi-step
                if len(found_actions) >= 2:
                    is_multi_step = True
                    print(
                        f"🔍 [Graph] Detected multi-step query based on multiple action verbs: {list(found_actions)}"
                    )

                # Also check for sentence boundaries that might indicate multiple requests
                # Count sentences that contain action verbs or imperative statements
                if not is_multi_step:
                    # Split by common sentence separators
                    sentences = re.split(r"[.!?]\s+", original_request)
                    # Filter out very short sentences (likely not separate requests)
                    meaningful_sentences = [
                        s.strip() for s in sentences if len(s.strip()) > 10
                    ]

                    # Count sentences that contain action verbs or imperative patterns
                    action_sentences = 0
                    for sentence in meaningful_sentences:
                        sentence_lower = sentence.lower()
                        # Check if sentence contains action verbs
                        if any(
                            re.search(pattern, sentence_lower)
                            for pattern in action_verbs
                        ):
                            action_sentences += 1
                        # Also check for imperative patterns (verb at start of sentence)
                        elif re.match(r"^\s*[a-z]+\s+", sentence_lower):
                            # Simple heuristic: if sentence starts with a verb-like word
                            first_word = (
                                sentence_lower.split()[0]
                                if sentence_lower.split()
                                else ""
                            )
                            if len(first_word) > 3 and first_word not in [
                                "the",
                                "this",
                                "that",
                                "these",
                                "those",
                                "please",
                            ]:
                                action_sentences += 1

                    if action_sentences >= 2:
                        is_multi_step = True
                        print(
                            f"🔍 [Graph] Detected multi-step query based on multiple action sentences: {action_sentences} sentences"
                        )

            if not is_multi_step:
                print(f"✅ [Graph] Single-step request complete, routing to END")
                return END

            # For multi-step requests, check if all steps are likely complete
            # Count tool calls ONLY from the current execution (after the most recent human message)
            tool_calls_count = 0
            if most_recent_human_idx >= 0:
                for idx in range(most_recent_human_idx + 1, len(messages)):
                    msg = messages[idx]
                    if hasattr(msg, "type") and msg.type == "ai":
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            tool_calls_count += len(msg.tool_calls)

            # Count steps in user request (generic heuristic)
            import re

            step_count = 1  # At least one step
            # Count numbered steps (1., 2., etc.)
            numbered_steps = len(re.findall(r"\d+\.", original_request))
            if numbered_steps > 1:
                step_count = numbered_steps
            else:
                # Count explicit multi-step indicators
                then_count = original_request.lower().count(
                    "then"
                ) + original_request.lower().count("next")
                if then_count > 0:
                    step_count = then_count + 1
                else:
                    # Generic step counting: count distinct action verbs or action sentences
                    action_verbs = [
                        r"\b(find|search|look|locate|grep|scan)\b",
                        r"\b(refactor|refactoring|improve|optimize|clean)\b",
                        r"\b(format|formatting|style|indent)\b",
                        r"\b(validate|validation|check|verify|test)\b",
                        r"\b(analyze|analysis|examine|inspect)\b",
                        r"\b(generate|create|build|make|write)\b",
                        r"\b(convert|transform|translate|change)\b",
                        r"\b(fix|repair|correct|resolve)\b",
                    ]

                    request_lower = original_request.lower()
                    found_actions = set()

                    for pattern in action_verbs:
                        if re.search(pattern, request_lower):
                            base_verb = (
                                pattern.split("|")[0]
                                .replace(r"\b", "")
                                .replace("(", "")
                                .replace(")", "")
                            )
                            found_actions.add(base_verb)

                    if len(found_actions) >= 2:
                        step_count = len(found_actions)
                        print(
                            f"🔍 [Graph] Detected {step_count} steps based on action verbs: {list(found_actions)}"
                        )
                    else:
                        # Fallback: count action sentences
                        sentences = re.split(r"[.!?]\s+", original_request)
                        meaningful_sentences = [
                            s.strip() for s in sentences if len(s.strip()) > 10
                        ]
                        action_sentences = 0
                        for sentence in meaningful_sentences:
                            sentence_lower = sentence.lower()
                            if any(
                                re.search(pattern, sentence_lower)
                                for pattern in action_verbs
                            ):
                                action_sentences += 1

                        if action_sentences >= 2:
                            step_count = action_sentences
                            print(
                                f"🔍 [Graph] Detected {step_count} steps based on action sentences"
                            )

            print(
                f"🔍 [Graph] Multi-step request: {step_count} steps detected, {tool_calls_count} tool calls executed"
            )

            # If we've executed at least as many tools as steps, likely complete
            if tool_calls_count >= step_count:
                print(
                    f"✅ [Graph] All steps appear complete ({tool_calls_count} tools >= {step_count} steps), routing to END"
                )
                return END
            else:
                print(
                    f"🔄 [Graph] More steps remaining ({tool_calls_count} < {step_count}), routing back to query_or_respond"
                )
                return "query_or_respond"

        builder.add_conditional_edges(
            "generate",
            generate_condition,
            {END: END, "query_or_respond": "query_or_respond"},
        )
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
