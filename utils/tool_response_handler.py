"""
Generic tool response handler based on metadata from agent code.

This module provides a metadata-driven approach to handling tool responses,
eliminating the need for hardcoded tool-specific logic in graph.py.

Metadata is defined in agent code via WizelitAgent's @mcp.ingest()
decorator and exposed via MCP protocol's meta field.

MULTI-USER SUPPORT:
- Tool metadata is stored per-user to prevent cross-user interference
- Each user has their own isolated tool metadata cache
"""

import json
import logging
from typing import Dict, Any, Optional
from langchain_core.messages import ToolMessage, AIMessage
from utils.mcp_storage import get_mcp_servers

logger = logging.getLogger(__name__)


class ToolResponseHandler:
    """Handles tool responses based on metadata from agent code (via MCP protocol)."""

    def __init__(self):
        """Initialize handler with per-user tool response metadata."""
        # Per-user metadata storage to prevent cross-user interference
        # Structure: user_id -> tool_name -> metadata
        self._user_tool_metadata: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # Note: Don't load metadata in __init__ since we need user_id context
        # Metadata will be refreshed when MCP servers connect

    def _load_tool_metadata(self, user_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """
        Load tool response handling metadata from in-memory storage.

        Metadata comes from agent code via MCP protocol's meta field.
        It's stored in memory when Chainlit connects to MCP servers.

        Args:
            user_id: Optional user ID to load metadata for. If None, loads for ALL users.
        """
        metadata = {}

        try:
            # Get MCP servers from in-memory storage
            # If user_id is provided, get only that user's servers
            # Otherwise, aggregate from ALL users (for backward compatibility)
            if user_id:
                agents_config = get_mcp_servers(user_id=user_id)
            else:
                # For global refresh (like on startup), get all users' servers
                from utils.mcp_storage import get_all_user_ids
                agents_config = {}
                for uid in get_all_user_ids():
                    user_servers = get_mcp_servers(user_id=uid)
                    agents_config.update(user_servers)

            if not agents_config:
                logger.debug("No MCP servers found in storage")
                return metadata

            # Extract response_handling from in-memory storage
            # It can be stored in two places:
            # 1. tool['response_handling'] - direct field (how it's saved)
            # 2. tool['meta']['wizelit_response_handling'] - in meta field (from MCP protocol)
            # Note: If multiple servers have the same tool name, the last one wins
            for server_name, server_config in agents_config.items():
                tools = server_config.get("tools", [])
                for tool in tools:
                    tool_name = tool.get("name")
                    if not tool_name:
                        continue

                    # Priority 1: Check direct response_handling field
                    if "response_handling" in tool:
                        response_handling = tool["response_handling"]
                        if isinstance(response_handling, dict):
                            metadata[tool_name] = response_handling
                            logger.info(
                                f"✅ Loaded response handling for {tool_name} from {server_name} (response_handling field): {response_handling}"
                            )
                        else:
                            logger.warning(
                                f"⚠️ response_handling for {tool_name} is not a dict: {type(response_handling)}, value: {response_handling}"
                            )
                    # Priority 2: Check meta field (from MCP protocol)
                    elif "meta" in tool:
                        tool_meta = tool.get("meta", {})
                        if (
                            isinstance(tool_meta, dict)
                            and "wizelit_response_handling" in tool_meta
                        ):
                            metadata[tool_name] = tool_meta["wizelit_response_handling"]
                            logger.info(
                                f"✅ Loaded response handling for {tool_name} from {server_name} (MCP meta): {tool_meta['wizelit_response_handling']}"
                            )
                    # Only log warning if we haven't found it yet (might be in another server)
                    if tool_name not in metadata:
                        logger.debug(
                            f"⚠️ No response_handling found for {tool_name} in {server_name}"
                        )
        except Exception as e:
            logger.error(f"Failed to load tool metadata from storage: {e}")

        return metadata

    def _extract_value(self, content: Any, extract_path: str) -> Any:
        """
        Extract value from content using a simple path syntax.

        Supports:
        - "content[0].text" -> content[0]["text"] (if content is a list)
        - "content.text" -> content["text"] (if content is a dict)
        - "content" -> content (direct access)
        - Handles string content that might be JSON
        - Handles MCP tool response format: [{'type': 'text', 'text': 'value'}]

        Args:
            content: The content to extract from (can be dict, list, string, or any value)
            extract_path: Path string like "content[0].text" or "result"

        Returns:
            Extracted value or None if path is invalid
        """
        if not extract_path or extract_path == "content":
            return content

        # If content is a string, try to parse it as JSON first
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, ValueError):
                # If it's not JSON, treat as plain string
                if extract_path == "content":
                    return content
                # For other paths, we can't extract from a plain string
                logger.warning(
                    f"Cannot extract '{extract_path}' from plain string content"
                )
                return None

        # Handle direct list access (MCP format: [{'type': 'text', 'text': 'value'}])
        if isinstance(content, list) and len(content) > 0:
            # If extract_path starts with [0], handle it directly
            if extract_path.startswith("content[0]") or extract_path.startswith("[0]"):
                # Remove 'content' prefix if present
                remaining_path = (
                    extract_path.replace("content[0]", "")
                    .replace("[0]", "")
                    .lstrip(".")
                )
                first_item = content[0]

                if not remaining_path:
                    # Just want the first item
                    return first_item

                # Continue extraction from first item
                if isinstance(first_item, dict):
                    # Extract from dict: e.g., "text" from {'type': 'text', 'text': 'value'}
                    return first_item.get(remaining_path)
                else:
                    return None

        # Wrap content in a dict with 'content' key for path resolution
        if not isinstance(content, dict):
            data = {"content": content}
        else:
            data = content

        current = data
        parts = extract_path.split(".")

        try:
            for part in parts:
                if "[" in part and "]" in part:
                    # Handle array access: "content[0]"
                    key = part[: part.index("[")]
                    index_str = part[part.index("[") + 1 : part.index("]")]
                    index = int(index_str)

                    # Get the value (could be from dict or direct access)
                    if isinstance(current, dict):
                        current = current.get(key, [])
                    elif hasattr(current, key):
                        current = getattr(current, key)
                    else:
                        current = None

                    # Access list element
                    if isinstance(current, list) and 0 <= index < len(current):
                        current = current[index]
                    else:
                        logger.warning(
                            f"Invalid index {index} for list of length {len(current) if isinstance(current, list) else 0}"
                        )
                        return None
                else:
                    # Handle dict access
                    if isinstance(current, dict):
                        current = current.get(part)
                    elif hasattr(current, part):
                        current = getattr(current, part)
                    else:
                        current = None

                if current is None:
                    break

            return current
        except (KeyError, IndexError, AttributeError, ValueError, TypeError) as e:
            logger.warning(f"Failed to extract value from path '{extract_path}': {e}")
            return None

    def _format_content(self, content: Any, content_type: str = "auto") -> str:
        """
        Format content based on type.

        Args:
            content: Content to format
            content_type: "text", "json", or "auto"

        Returns:
            Formatted string
        """
        if content_type == "text":
            return str(content)
        elif content_type == "json":
            if isinstance(content, str):
                try:
                    # Try to parse and re-format for pretty printing
                    parsed = json.loads(content)
                    return json.dumps(parsed, indent=2)
                except json.JSONDecodeError:
                    return content
            else:
                return json.dumps(content, indent=2)
        else:  # auto
            if isinstance(content, str):
                return content
            elif isinstance(content, (dict, list)):
                return json.dumps(content, indent=2)
            else:
                return str(content)

    def _get_user_metadata(self, user_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """Get metadata for a specific user."""
        from utils.mcp_storage import DEFAULT_USER_ID
        uid = user_id or DEFAULT_USER_ID
        return self._user_tool_metadata.get(uid, {})

    def should_handle_directly(self, tool_name: str, user_id: Optional[str] = None) -> bool:
        """
        Check if tool should be handled directly (skip LLM processing).

        Args:
            tool_name: Name of the tool
            user_id: Optional user ID for per-user metadata lookup

        Returns:
            True if tool should be handled directly, False otherwise
        """
        user_metadata = self._get_user_metadata(user_id)
        metadata = user_metadata.get(tool_name, {})
        mode = metadata.get("mode", "default")
        should_handle = mode in ("direct", "formatted")
        logger.info(
            f"🔍 [Handler] should_handle_directly({tool_name}, user={user_id}): metadata={metadata}, mode={mode}, should_handle={should_handle}"
        )
        if tool_name not in user_metadata:
            logger.warning(
                f"⚠️ [Handler] Tool '{tool_name}' not found in metadata for user '{user_id}'. Available tools: {list(user_metadata.keys())}"
            )
        return should_handle

    def handle_tool_response(self, message: ToolMessage, user_id: Optional[str] = None) -> Optional[AIMessage]:
        """
        Handle tool response based on metadata configuration.

        Args:
            message: ToolMessage from tool execution
            user_id: Optional user ID for per-user metadata lookup

        Returns:
            AIMessage if handled directly, None if should use default processing
        """
        tool_name = message.name
        user_metadata = self._get_user_metadata(user_id)
        metadata = user_metadata.get(tool_name, {})

        # Default mode: let LLM process normally
        if not metadata or metadata.get("mode", "default") == "default":
            return None

        mode = metadata.get("mode")
        # Default to "content[0].text" for MCP format responses: [{'type': 'text', 'text': 'value'}]
        extract_path = metadata.get("extract_path", "content[0].text")
        # Default to "text" since most tools return human-readable string responses
        content_type = metadata.get("content_type", "text")
        template = metadata.get("template", "{value}")

        # Extract value from message content
        try:
            content = message.content
            logger.info(
                f"🔍 [Handler] Extracting from {tool_name}. Content type: {type(content)}, Content preview: {str(content)[:500]}, Extract path: {extract_path}"
            )

            # Handle different content formats
            value = None

            # Case 1: Content is already a list (MCP format: [{'type': 'text', 'text': 'value'}])
            if isinstance(content, list) and len(content) > 0:
                if extract_path == "content[0].text" or extract_path.endswith(
                    "[0].text"
                ):
                    first_item = content[0]
                    if isinstance(first_item, dict):
                        # Try 'text' key first (MCP format)
                        if "text" in first_item:
                            value = first_item["text"]
                        # Fallback to 'result' key
                        elif "result" in first_item:
                            value = first_item["result"]
                        else:
                            logger.warning(
                                f"Content dict missing 'text' or 'result' key. Keys: {list(first_item.keys())}"
                            )
                            # Try to get the first string value
                            for v in first_item.values():
                                if isinstance(v, str):
                                    value = v
                                    break
                elif extract_path == "content":
                    value = content
                else:
                    # Use generic extraction
                    value = self._extract_value({"content": content}, extract_path)

            # Case 2: Content is a string (might be JSON)
            elif isinstance(content, str):
                if extract_path == "content":
                    value = content
                else:
                    # Try parsing as JSON first (MCP may serialize dicts to JSON strings)
                    try:
                        parsed = json.loads(content)
                        # If parsed is a dict and extract_path is "content[0].text",
                        # the dict was serialized - extract the whole dict
                        if (
                            isinstance(parsed, dict)
                            and extract_path == "content[0].text"
                        ):
                            # For dict responses, return the dict as JSON string for direct mode
                            value = json.dumps(parsed, indent=2)
                        else:
                            value = self._extract_value(
                                {"content": parsed}, extract_path
                            )
                    except (json.JSONDecodeError, ValueError):
                        # Not JSON, can't extract from string
                        logger.warning(
                            f"Cannot extract '{extract_path}' from plain string"
                        )
                        value = None

            # Case 3: Content is a dict
            elif isinstance(content, dict):
                if extract_path == "content":
                    value = content
                else:
                    value = self._extract_value({"content": content}, extract_path)

            # Case 4: Other types - use generic extraction
            else:
                if extract_path == "content":
                    value = content
                else:
                    value = self._extract_value({"content": content}, extract_path)

            if value is None:
                logger.error(
                    f"❌ Failed to extract value for {tool_name} using path '{extract_path}'. Content type: {type(content)}, Content: {content}"
                )
                return None

            logger.debug(f"✅ Successfully extracted value for {tool_name}: {value}")

            # Format the value
            formatted_value = self._format_content(value, content_type)

            # Apply template if provided
            if mode == "formatted" and template:
                try:
                    response_text = template.format(value=formatted_value)
                except KeyError as e:
                    logger.warning(
                        f"Template missing key {e} for {tool_name}, using value directly"
                    )
                    response_text = formatted_value
            else:
                response_text = formatted_value

            # Log full response length for debugging
            response_length = (
                len(response_text) if isinstance(response_text, str) else 0
            )
            logger.info(
                f"✅ Handled tool response for {tool_name} with mode {mode}. Response length: {response_length} chars. First 200 chars: {response_text[:200] if isinstance(response_text, str) else response_text}"
            )

            # Ensure we return the full content
            return AIMessage(content=response_text)

        except Exception as e:
            # If extraction fails, fall back to default processing
            logger.warning(
                f"Failed to handle tool response for {tool_name}: {e}", exc_info=True
            )
            return None

    def refresh_metadata(self, user_id: Optional[str] = None):
        """
        Reload tool metadata from in-memory storage for a specific user.

        Args:
            user_id: User ID to refresh metadata for. Required for per-user isolation.
        """
        from utils.mcp_storage import DEFAULT_USER_ID
        uid = user_id or DEFAULT_USER_ID

        logger.info(f"🔄 [Handler] Refreshing tool response metadata from storage (user_id={uid})")

        old_tools = set(self._user_tool_metadata.get(uid, {}).keys())
        new_metadata = self._load_tool_metadata(user_id=uid)
        self._user_tool_metadata[uid] = new_metadata
        new_tools = set(new_metadata.keys())

        logger.info(
            f"✅ [Handler] Metadata refreshed for user '{uid}'. Old tools: {old_tools}, New tools: {new_tools}, Added: {new_tools - old_tools}, Removed: {old_tools - new_tools}"
        )
        # Log all tools with direct mode
        direct_tools = {
            name: meta
            for name, meta in new_metadata.items()
            if meta.get("mode") in ("direct", "formatted")
        }
        logger.info(
            f"📋 [Handler] Tools with direct/formatted mode for user '{uid}': {list(direct_tools.keys())}"
        )

    def clear_user_metadata(self, user_id: str) -> None:
        """
        Clear metadata for a specific user (e.g., when user disconnects all servers).

        Args:
            user_id: User ID to clear metadata for
        """
        if user_id in self._user_tool_metadata:
            del self._user_tool_metadata[user_id]
            logger.info(f"🧹 [Handler] Cleared metadata for user '{user_id}'")


# Module-level singleton for efficiency
_tool_response_handler = ToolResponseHandler()

# Register cleanup callback to sync with mcp_storage cleanup
# This ensures handler metadata is cleaned up when users become inactive
def _on_user_cleanup(user_id: str) -> None:
    """Callback to clear handler metadata when a user is cleaned up."""
    _tool_response_handler.clear_user_metadata(user_id)

# Register the callback with mcp_storage
from utils.mcp_storage import register_cleanup_callback
register_cleanup_callback(_on_user_cleanup)
