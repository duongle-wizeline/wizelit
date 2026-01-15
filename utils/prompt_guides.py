import yaml
from pathlib import Path

def _generate_prompt_guides():
    """Generate prompt guides from config/agents.yaml"""
    config_path = Path(__file__).parent.parent / "config" / "agents.yaml"
    with open(config_path, 'r') as file:
        mcp_servers = yaml.safe_load(file) or {}

    config_tools = []
    for server in mcp_servers.values():
        config_tools.extend(server.get('tools', []))

    guides = ""
    for tool in config_tools:
        guides += f"\n- Use tool `{tool['name']}` for purpose: {tool.get('description', tool['name'])}"

    return (
        "You are Wizelit, an Engineering Manager with these toolsets:\n"
        f"{guides}"
        "\n\nRules:\n1) Never write Python code yourself.\n"
    )

prompt_guides = _generate_prompt_guides()

def refresh_prompt_guides():
    """Refresh the global prompt guides variable."""
    global prompt_guides
    prompt_guides = _generate_prompt_guides()

