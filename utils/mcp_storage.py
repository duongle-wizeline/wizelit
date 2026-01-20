"""
In-memory storage for MCP server metadata.
Replaces agents.yaml file to avoid file I/O issues.
"""

from typing import Dict, Any, Optional, Set
import logging
import json
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Global in-memory storage for MCP server metadata
_mcp_servers: Dict[str, Dict[str, Any]] = {}

# File to persist removed servers list
_REMOVED_SERVERS_FILE = Path(__file__).parent.parent / ".removed_mcp_servers.json"


def _load_removed_servers() -> Set[str]:
    """Load removed servers list from file."""
    if _REMOVED_SERVERS_FILE.exists():
        try:
            with open(_REMOVED_SERVERS_FILE, "r") as f:
                data = json.load(f)
                return set(data.get("removed_servers", []))
        except Exception as e:
            logger.warning(f"⚠️ [Storage] Failed to load removed servers list: {e}")
    return set()


def _save_removed_servers() -> None:
    """Save removed servers list to file."""
    try:
        _REMOVED_SERVERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_REMOVED_SERVERS_FILE, "w") as f:
            json.dump({"removed_servers": list(_removed_servers)}, f)
    except Exception as e:
        logger.warning(f"⚠️ [Storage] Failed to save removed servers list: {e}")


# Track servers that have been explicitly removed (to prevent auto-reconnection)
# This persists across restarts via a file
# Load removed servers on module import
_removed_servers: Set[str] = _load_removed_servers()
if _removed_servers:
    logger.info(
        f"📋 [Storage] Loaded {len(_removed_servers)} removed server(s) from file"
    )


def get_mcp_servers() -> Dict[str, Dict[str, Any]]:
    """Get all MCP server metadata."""
    return _mcp_servers.copy()


def add_mcp_server(server_name: str, server_config: Dict[str, Any]) -> None:
    """Add or update an MCP server in storage."""
    _mcp_servers[server_name] = server_config
    logger.info(f"✅ [Storage] Added/updated MCP server '{server_name}' in memory")


def remove_mcp_server(server_name: str) -> None:
    """Remove an MCP server from storage and mark it as removed."""
    if server_name in _mcp_servers:
        del _mcp_servers[server_name]
        logger.info(f"✅ [Storage] Removed MCP server '{server_name}' from memory")
    else:
        logger.debug(f"⚠️ [Storage] MCP server '{server_name}' not found in storage")

    # Mark as removed to prevent auto-reconnection (persists across restarts)
    _removed_servers.add(server_name)
    _save_removed_servers()
    logger.info(
        f"🚫 [Storage] Marked '{server_name}' as removed (will reject auto-reconnect)"
    )


def get_mcp_server(server_name: str) -> Optional[Dict[str, Any]]:
    """Get a specific MCP server by name."""
    return _mcp_servers.get(server_name)


def clear_all() -> None:
    """Clear all MCP server metadata (for testing/debugging)."""
    _mcp_servers.clear()
    logger.info("✅ [Storage] Cleared all MCP server metadata")


def is_server_removed(server_name: str) -> bool:
    """Check if a server has been explicitly removed (should reject auto-reconnect)."""
    return server_name in _removed_servers


def clear_removed_servers() -> None:
    """Clear the removed servers list (called on startup to allow fresh connections)."""
    _removed_servers.clear()
    _save_removed_servers()
    logger.info(
        "✅ [Storage] Cleared removed servers list (allowing fresh connections)"
    )


def allow_server_reconnect(server_name: str) -> None:
    """Allow a previously removed server to reconnect (remove from blacklist)."""
    if server_name in _removed_servers:
        _removed_servers.remove(server_name)
        _save_removed_servers()
        logger.info(
            f"✅ [Storage] Removed '{server_name}' from blacklist (can reconnect now)"
        )
