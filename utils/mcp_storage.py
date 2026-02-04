"""
In-memory storage for MCP server metadata.

MULTI-USER SUPPORT:
- Storage is keyed by user_id to isolate each user's MCP connections
- User A's actions don't affect User B's connections
- Each user has their own set of MCP servers and blacklist
"""

from typing import Dict, Any, Optional
import logging
import time

logger = logging.getLogger(__name__)

# Per-user storage for MCP server metadata
# Structure: user_id -> server_name -> server_config
_mcp_servers: Dict[str, Dict[str, Dict[str, Any]]] = {}

# Cooldown period after removal before allowing reconnect (seconds)
REMOVAL_COOLDOWN_SECONDS = 10

# Per-user blacklist for recently removed servers
# Structure: user_id -> server_name -> removal_timestamp
_removed_servers: Dict[str, Dict[str, float]] = {}

# Default user ID for backward compatibility (single-user mode)
DEFAULT_USER_ID = "__default__"


def get_mcp_servers(user_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Get MCP server metadata for a specific user."""
    uid = user_id or DEFAULT_USER_ID
    return _mcp_servers.get(uid, {}).copy()


def add_mcp_server(server_name: str, server_config: Dict[str, Any], user_id: Optional[str] = None) -> None:
    """Add or update an MCP server for a specific user."""
    uid = user_id or DEFAULT_USER_ID
    if uid not in _mcp_servers:
        _mcp_servers[uid] = {}
    _mcp_servers[uid][server_name] = server_config
    logger.info(f"✅ [Storage] Added/updated MCP server '{server_name}' for user '{uid}'")


def remove_mcp_server(server_name: str, user_id: Optional[str] = None) -> None:
    """Remove an MCP server for a specific user and mark it as removed."""
    uid = user_id or DEFAULT_USER_ID

    if uid in _mcp_servers and server_name in _mcp_servers[uid]:
        del _mcp_servers[uid][server_name]
        logger.info(f"✅ [Storage] Removed MCP server '{server_name}' for user '{uid}'")
    else:
        logger.debug(f"⚠️ [Storage] MCP server '{server_name}' not found for user '{uid}'")

    # Mark as removed with timestamp to prevent auto-reconnection during cooldown
    if uid not in _removed_servers:
        _removed_servers[uid] = {}
    _removed_servers[uid][server_name] = time.time()
    logger.info(
        f"🚫 [Storage] Marked '{server_name}' as removed for user '{uid}' (cooldown: {REMOVAL_COOLDOWN_SECONDS}s)"
    )


def get_mcp_server(server_name: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get a specific MCP server for a user."""
    uid = user_id or DEFAULT_USER_ID
    return _mcp_servers.get(uid, {}).get(server_name)


def clear_all(user_id: Optional[str] = None) -> None:
    """Clear MCP server metadata for a user (or all users if user_id is None)."""
    if user_id:
        if user_id in _mcp_servers:
            _mcp_servers[user_id].clear()
        logger.info(f"✅ [Storage] Cleared MCP servers for user '{user_id}'")
    else:
        _mcp_servers.clear()
        logger.info("✅ [Storage] Cleared all MCP server metadata for all users")


def is_server_removed(server_name: str, user_id: Optional[str] = None) -> bool:
    """Check if a server was recently removed for a user and is still in cooldown."""
    uid = user_id or DEFAULT_USER_ID

    if uid not in _removed_servers or server_name not in _removed_servers[uid]:
        return False

    removal_time = _removed_servers[uid][server_name]
    time_since_removal = time.time() - removal_time

    if time_since_removal < REMOVAL_COOLDOWN_SECONDS:
        logger.debug(
            f"🚫 [Storage] '{server_name}' is in removal cooldown for user '{uid}' ({time_since_removal:.1f}s < {REMOVAL_COOLDOWN_SECONDS}s)"
        )
        return True
    else:
        # Cooldown expired, remove from blacklist
        logger.info(
            f"✅ [Storage] Cooldown expired for '{server_name}' (user '{uid}'), allowing reconnect"
        )
        del _removed_servers[uid][server_name]
        return False


def clear_removed_servers(user_id: Optional[str] = None) -> None:
    """Clear the removed servers list for a user (or all users if user_id is None)."""
    if user_id:
        if user_id in _removed_servers:
            _removed_servers[user_id].clear()
        logger.info(f"✅ [Storage] Cleared removed servers for user '{user_id}'")
    else:
        _removed_servers.clear()
        logger.info("✅ [Storage] Cleared removed servers for all users")


def allow_server_reconnect(server_name: str, user_id: Optional[str] = None) -> None:
    """Allow a previously removed server to reconnect for a user."""
    uid = user_id or DEFAULT_USER_ID
    if uid in _removed_servers and server_name in _removed_servers[uid]:
        del _removed_servers[uid][server_name]
        logger.info(
            f"✅ [Storage] Removed '{server_name}' from blacklist for user '{uid}'"
        )


def get_removal_cooldown_remaining(server_name: str, user_id: Optional[str] = None) -> float:
    """Get remaining cooldown time for a removed server (0 if not in cooldown)."""
    uid = user_id or DEFAULT_USER_ID
    if uid not in _removed_servers or server_name not in _removed_servers[uid]:
        return 0
    removal_time = _removed_servers[uid][server_name]
    remaining = REMOVAL_COOLDOWN_SECONDS - (time.time() - removal_time)
    return max(0, remaining)


def get_user_count() -> int:
    """Get the number of users with MCP servers (for debugging)."""
    return len(_mcp_servers)


def get_all_user_ids() -> list:
    """Get all user IDs with MCP servers (for debugging)."""
    return list(_mcp_servers.keys())
