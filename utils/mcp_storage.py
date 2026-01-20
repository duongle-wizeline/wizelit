"""
In-memory storage for MCP server metadata.
Replaces agents.yaml file to avoid file I/O issues.
"""
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

# Global in-memory storage for MCP server metadata
_mcp_servers: Dict[str, Dict[str, Any]] = {}


def get_mcp_servers() -> Dict[str, Dict[str, Any]]:
    """Get all MCP server metadata."""
    return _mcp_servers.copy()


def add_mcp_server(server_name: str, server_config: Dict[str, Any]) -> None:
    """Add or update an MCP server in storage."""
    _mcp_servers[server_name] = server_config
    logger.info(f"✅ [Storage] Added/updated MCP server '{server_name}' in memory")


def remove_mcp_server(server_name: str) -> None:
    """Remove an MCP server from storage."""
    if server_name in _mcp_servers:
        del _mcp_servers[server_name]
        logger.info(f"✅ [Storage] Removed MCP server '{server_name}' from memory")
    else:
        logger.debug(f"⚠️ [Storage] MCP server '{server_name}' not found in storage")


def get_mcp_server(server_name: str) -> Optional[Dict[str, Any]]:
    """Get a specific MCP server by name."""
    return _mcp_servers.get(server_name)


def clear_all() -> None:
    """Clear all MCP server metadata (for testing/debugging)."""
    _mcp_servers.clear()
    logger.info("✅ [Storage] Cleared all MCP server metadata")
