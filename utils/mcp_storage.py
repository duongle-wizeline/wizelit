"""
In-memory storage for MCP server metadata.

MULTI-USER SUPPORT:
- Storage is keyed by user_id to isolate each user's MCP connections
- User A's actions don't affect User B's connections
- Each user has their own set of MCP servers and blacklist

MEMORY MANAGEMENT:
- User data has a TTL (time-to-live) to prevent memory leaks
- Inactive users are automatically cleaned up after USER_INACTIVITY_TTL_SECONDS
- Cleanup runs periodically when storage is accessed
"""

from typing import Dict, Any, Optional
import logging
import time
import threading

logger = logging.getLogger(__name__)

# Per-user storage for MCP server metadata
# Structure: user_id -> server_name -> server_config
_mcp_servers: Dict[str, Dict[str, Dict[str, Any]]] = {}

# Per-user last activity timestamp for TTL-based cleanup
# Structure: user_id -> last_activity_timestamp
_user_last_activity: Dict[str, float] = {}

# Cooldown period after removal before allowing reconnect (seconds)
REMOVAL_COOLDOWN_SECONDS = 10

# User inactivity TTL - cleanup users inactive for this duration (1 hour)
USER_INACTIVITY_TTL_SECONDS = 3600

# Cleanup interval - run cleanup at most once per this interval (5 minutes)
_CLEANUP_INTERVAL_SECONDS = 300
_last_cleanup_time: float = 0
_cleanup_lock = threading.Lock()

# Per-user blacklist for recently removed servers
# Structure: user_id -> server_name -> removal_timestamp
_removed_servers: Dict[str, Dict[str, float]] = {}

# Default user ID for backward compatibility (single-user mode)
DEFAULT_USER_ID = "__default__"


# Callbacks for cleanup notifications (allows other modules to sync cleanup)
_cleanup_callbacks: list = []


def register_cleanup_callback(callback) -> None:
    """
    Register a callback to be called when a user is cleaned up.

    Args:
        callback: Function that takes user_id as argument
    """
    _cleanup_callbacks.append(callback)


def _cleanup_inactive_users() -> int:
    """
    Remove data for users who have been inactive longer than USER_INACTIVITY_TTL_SECONDS.

    Returns:
        Number of users cleaned up
    """
    global _last_cleanup_time

    current_time = time.time()

    # Check if cleanup is needed (rate-limit cleanup calls)
    with _cleanup_lock:
        if current_time - _last_cleanup_time < _CLEANUP_INTERVAL_SECONDS:
            return 0
        _last_cleanup_time = current_time

    cleaned_count = 0
    users_to_remove = []

    # Find inactive users
    for user_id, last_activity in list(_user_last_activity.items()):
        if current_time - last_activity > USER_INACTIVITY_TTL_SECONDS:
            users_to_remove.append(user_id)

    # Remove inactive users
    for user_id in users_to_remove:
        if user_id in _mcp_servers:
            del _mcp_servers[user_id]
        if user_id in _removed_servers:
            del _removed_servers[user_id]
        if user_id in _user_last_activity:
            del _user_last_activity[user_id]
        cleaned_count += 1
        logger.info(f"🧹 [Storage] Cleaned up inactive user '{user_id}'")

        # Notify registered callbacks about the cleanup
        for callback in _cleanup_callbacks:
            try:
                callback(user_id)
            except Exception as e:
                logger.warning(f"⚠️ [Storage] Cleanup callback failed for user '{user_id}': {e}")

    if cleaned_count > 0:
        logger.info(f"🧹 [Storage] Cleaned up {cleaned_count} inactive user(s). Active users: {len(_mcp_servers)}")

    return cleaned_count


def _touch_user(user_id: str) -> None:
    """Update last activity timestamp for a user."""
    _user_last_activity[user_id] = time.time()
    # Trigger cleanup check (rate-limited)
    _cleanup_inactive_users()


def get_mcp_servers(user_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Get MCP server metadata for a specific user."""
    uid = user_id or DEFAULT_USER_ID
    _touch_user(uid)
    return _mcp_servers.get(uid, {}).copy()


def add_mcp_server(server_name: str, server_config: Dict[str, Any], user_id: Optional[str] = None) -> None:
    """Add or update an MCP server for a specific user."""
    uid = user_id or DEFAULT_USER_ID
    _touch_user(uid)
    if uid not in _mcp_servers:
        _mcp_servers[uid] = {}
    _mcp_servers[uid][server_name] = server_config
    logger.info(f"✅ [Storage] Added/updated MCP server '{server_name}' for user '{uid}'")


def remove_mcp_server(server_name: str, user_id: Optional[str] = None) -> None:
    """Remove an MCP server for a specific user and mark it as removed."""
    uid = user_id or DEFAULT_USER_ID
    _touch_user(uid)

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


def get_storage_stats() -> Dict[str, Any]:
    """
    Get storage statistics for monitoring and debugging.

    Returns:
        Dict with storage stats including user count, server count, and memory info
    """
    total_servers = sum(len(servers) for servers in _mcp_servers.values())
    return {
        "user_count": len(_mcp_servers),
        "total_servers": total_servers,
        "removed_servers_count": sum(len(removed) for removed in _removed_servers.values()),
        "user_ids": list(_mcp_servers.keys()),
        "cleanup_interval_seconds": _CLEANUP_INTERVAL_SECONDS,
        "inactivity_ttl_seconds": USER_INACTIVITY_TTL_SECONDS,
    }


def force_cleanup() -> int:
    """
    Force cleanup of inactive users (ignores rate limiting).
    Useful for manual cleanup or testing.

    Returns:
        Number of users cleaned up
    """
    global _last_cleanup_time
    with _cleanup_lock:
        _last_cleanup_time = 0  # Reset to force cleanup
    return _cleanup_inactive_users()


def cleanup_user(user_id: str) -> bool:
    """
    Manually cleanup a specific user's data.

    Args:
        user_id: The user ID to cleanup

    Returns:
        True if user was found and cleaned up, False otherwise
    """
    found = False
    if user_id in _mcp_servers:
        del _mcp_servers[user_id]
        found = True
    if user_id in _removed_servers:
        del _removed_servers[user_id]
        found = True
    if user_id in _user_last_activity:
        del _user_last_activity[user_id]
        found = True

    if found:
        logger.info(f"🧹 [Storage] Manually cleaned up user '{user_id}'")
        # Notify registered callbacks about the cleanup
        for callback in _cleanup_callbacks:
            try:
                callback(user_id)
            except Exception as e:
                logger.warning(f"⚠️ [Storage] Cleanup callback failed for user '{user_id}': {e}")
    return found
