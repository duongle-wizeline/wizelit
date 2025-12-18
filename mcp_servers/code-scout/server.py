"""
Code Scout MCP Server (FastMCP wrapper)
Fast synchronous symbol scanner exposed via HTTP/SSE transport (like refactoring-agent).
Supports local directories and GitHub repositories.
"""

import asyncio
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

# Ensure repo root on path so local packages resolve BEFORE imports
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from core.wizelit_agent_wrapper import WizelitAgentWrapper, Job

from code_scout.code_scout import CodeScout

# Initialize FastMCP wrapper (SSE transport, port 1338 to avoid clashing with refactoring-agent)
mcp = WizelitAgentWrapper("CodeScoutAgent", transport="sse", port=1338)


def _init_scout(root_directory: str, github_token: Optional[str]) -> CodeScout:
    """Create a CodeScout instance with caching disabled to ensure fresh results."""
    return CodeScout(root_directory, github_token=github_token, use_cache=False)


def _convert_usage_paths(usages: list, scout: CodeScout) -> list:
    """Convert cached file paths in usage objects back to GitHub URLs when applicable."""
    from code_scout.github_helper import GitHubHelper
    parsed = None

    if scout.original_input and "github.com" in scout.original_input.lower():
        parsed = GitHubHelper.parse_github_url(scout.original_input)

    root_path = Path(scout.root_directory).resolve()
    converted = []

    for usage in usages:
        usage_dict = asdict(usage) if hasattr(usage, '__dataclass_fields__') else usage
        if isinstance(usage_dict, dict) and "file_path" in usage_dict:
            try:
                file_path_obj = Path(usage_dict["file_path"]).resolve()
                if file_path_obj.is_relative_to(root_path):
                    rel_path = str(file_path_obj.relative_to(root_path))
                else:
                    rel_path = str(file_path_obj)

                # If GitHub repo, convert to GitHub URL
                if parsed:
                    owner = parsed.get("owner")
                    repo = parsed.get("repo")
                    ref = parsed.get("ref", "main")
                    usage_dict["file_path"] = f"https://github.com/{owner}/{repo}/blob/{ref}/{rel_path}"
                else:
                    # For local paths, keep the relative path
                    usage_dict["file_path"] = rel_path
            except Exception:
                pass
        converted.append(usage_dict)

    return converted


def _relative_to_root(root: Path, file_path: str, original_input: Optional[str] = None) -> str:
    """Convert absolute paths to paths relative to the scan root when possible.

    If original_input is a GitHub URL, convert file paths to GitHub file URLs.
    """
    try:
        file_path_obj = Path(file_path).resolve()
        root_resolved = root.resolve()

        # If this is a GitHub repository, convert to GitHub URL format
        if original_input and "github.com" in original_input.lower():
            from code_scout.github_helper import GitHubHelper
            parsed = GitHubHelper.parse_github_url(original_input)
            if parsed:
                # Get relative path from root
                if file_path_obj.is_relative_to(root_resolved):
                    rel_path = str(file_path_obj.relative_to(root_resolved))
                else:
                    rel_path = str(file_path_obj)

                # Build GitHub URL
                owner = parsed.get("owner")
                repo = parsed.get("repo")
                ref = parsed.get("ref", "main")  # Default to main branch

                # Construct the GitHub blob URL
                return f"https://github.com/{owner}/{repo}/blob/{ref}/{rel_path}"

        # For local paths, return relative path if possible
        if file_path_obj.is_relative_to(root_resolved):
            return str(file_path_obj.relative_to(root_resolved))
        return str(file_path_obj)
    except Exception:
        return file_path


@mcp.ingest(is_long_running=False, description="Scan a directory or GitHub repo for Python files and symbol usages.")
async def scan_directory(
    root_directory: str,
    pattern: str = "*.py",
    github_token: Optional[str] = None,
):
    def _run():
        scout = _init_scout(root_directory, github_token)
        try:
            result = scout.scan_directory(pattern)
            converted = {}
            for symbol, usages in result.items():
                converted_usages = _convert_usage_paths(usages, scout)
                converted[symbol] = converted_usages
            return converted
        finally:
            scout.cleanup()

    return await asyncio.to_thread(_run)


@mcp.ingest(is_long_running=False, description="Find all usages of a symbol in a local path or GitHub repo.")
async def find_symbol(
    root_directory: str,
    symbol_name: str,
    pattern: str = "*.py",
    github_token: Optional[str] = None,
):
    def _run():
        scout = _init_scout(root_directory, github_token)
        try:
            if not scout.symbol_usages:
                scout.scan_directory(pattern)
            result = scout.find_symbol(symbol_name)
            converted = _convert_usage_paths(result, scout)
            return converted
        finally:
            scout.cleanup()

    return await asyncio.to_thread(_run)


@mcp.ingest(is_long_running=False, description="Analyze impact of changing a symbol.")
async def analyze_impact(
    root_directory: str,
    symbol_name: str,
    pattern: str = "*.py",
    github_token: Optional[str] = None,
):
    def _run():
        scout = _init_scout(root_directory, github_token)
        try:
            if not scout.symbol_usages:
                scout.scan_directory(pattern)
            return scout.analyze_impact(symbol_name)
        finally:
            scout.cleanup()

    return await asyncio.to_thread(_run)


@mcp.ingest(is_long_running=False, description="Grep for a pattern across a directory or GitHub repo.")
async def grep_search(
    root_directory: str,
    pattern: str,
    file_pattern: str = "*.py",
    github_token: Optional[str] = None,
):
    def _run():
        scout = _init_scout(root_directory, github_token)
        try:
            matches = scout.grep_search(pattern=pattern, file_pattern=file_pattern)

            # Convert file paths in matches if it's a GitHub repo
            if scout.original_input and "github.com" in scout.original_input.lower():
                from code_scout.github_helper import GitHubHelper
                parsed = GitHubHelper.parse_github_url(scout.original_input)
                if parsed:
                    owner = parsed.get("owner")
                    repo = parsed.get("repo")
                    ref = parsed.get("ref", "main")
                    root_path = Path(scout.root_directory).resolve()

                    for match in matches:
                        if "file" in match:
                            try:
                                file_path_obj = Path(match["file"]).resolve()
                                if file_path_obj.is_relative_to(root_path):
                                    rel_path = str(file_path_obj.relative_to(root_path))
                                else:
                                    rel_path = str(file_path_obj)
                                match["file"] = f"https://github.com/{owner}/{repo}/blob/{ref}/{rel_path}"
                            except Exception:
                                pass

            return matches
        finally:
            scout.cleanup()

    return await asyncio.to_thread(_run)


@mcp.ingest(is_long_running=False, description="Git blame for a specific line.")
async def git_blame(
    root_directory: str,
    file_path: str,
    line_number: int,
    github_token: Optional[str] = None,
):
    def _run():
        scout = _init_scout(root_directory, github_token)
        try:
            return scout.git_blame(file_path, line_number)
        finally:
            scout.cleanup()

    return await asyncio.to_thread(_run)


@mcp.ingest(is_long_running=False, description="Build a dependency graph from symbol usages.")
async def build_dependency_graph(
    root_directory: str,
    pattern: str = "*.py",
    github_token: Optional[str] = None,
):
    def _run():
        scout = _init_scout(root_directory, github_token)
        try:
            if not scout.symbol_usages:
                scout.scan_directory(pattern)
            graph = scout.build_dependency_graph()

            # Convert file paths in nodes if it's a GitHub repo
            result = {}
            for symbol, node in graph.items():
                node_dict = asdict(node)
                if scout.original_input and "github.com" in scout.original_input.lower():
                    from code_scout.github_helper import GitHubHelper
                    parsed = GitHubHelper.parse_github_url(scout.original_input)
                    if parsed and "file_path" in node_dict:
                        owner = parsed.get("owner")
                        repo = parsed.get("repo")
                        ref = parsed.get("ref", "main")
                        root_path = Path(scout.root_directory).resolve()
                        try:
                            file_path_obj = Path(node_dict["file_path"]).resolve()
                            if file_path_obj.is_relative_to(root_path):
                                rel_path = str(file_path_obj.relative_to(root_path))
                            else:
                                rel_path = str(file_path_obj)
                            node_dict["file_path"] = f"https://github.com/{owner}/{repo}/blob/{ref}/{rel_path}"
                        except Exception:
                            pass
                result[symbol] = node_dict

            return result
        finally:
            scout.cleanup()

    return await asyncio.to_thread(_run)


# Text-oriented tools matching the previous refactoring-agent behavior


@mcp.ingest(is_long_running=False, description="Analyze symbol usages and impact (formatted text).")
async def code_scout_symbol_usage(
    target: str,
    symbol: str,
    file_pattern: str = "*.py",
    github_token: Optional[str] = None,
    max_results: int = 50,
    include_graph: bool = True,
):
    def _run() -> str:
        scout = _init_scout(target, github_token)
        try:
            scout.scan_directory(pattern=file_pattern)
            usages = scout.find_symbol(symbol)

            if not usages:
                return f"No usages for '{symbol}' found in {target}."

            impact = scout.analyze_impact(symbol)
            root_path = Path(scout.root_directory).resolve()

            lines = [
                f"Code Scout report for '{symbol}'",
                f"Target: {target}",
                f"Total usages: {impact.get('total_usages', len(usages))}",
            ]

            breakdown = impact.get("usage_breakdown", {})
            if breakdown:
                lines.append(
                    "Breakdown: "
                    + ", ".join(
                        f"{key}={value}"
                        for key, value in breakdown.items()
                    )
                )

            if include_graph:
                deps = impact.get("dependencies", [])
                dependents = impact.get("dependents", [])
                if deps:
                    lines.append("Depends on: " + ", ".join(sorted(deps)))
                if dependents:
                    lines.append("Used by: " + ", ".join(sorted(dependents)))

            lines.append("Top matches:")
            for usage in usages[:max_results]:
                rel_path = _relative_to_root(root_path, usage.file_path, scout.original_input)
                lines.append(
                    f"- {rel_path}:{usage.line_number} [{usage.usage_type}] {usage.context}"
                )

            if len(usages) > max_results:
                lines.append(f"(trimmed to first {max_results} results)")

            return "\n".join(lines)
        finally:
            scout.cleanup()

    return await asyncio.to_thread(_run)


@mcp.ingest(is_long_running=False, description="Run grep via Code Scout (formatted text).")
async def code_scout_grep(
    job: Job,
    target: str,
    pattern: str,
    file_pattern: str = "*.py",
    github_token: Optional[str] = None,
    max_results: int = 50,
):
    def _run() -> str:
        scout = _init_scout(target, github_token)
        try:
            matches = scout.grep_search(pattern=pattern, file_pattern=file_pattern)
            if not matches:
                return f"No matches for '{pattern}' found in {target}."

            root_path = Path(scout.root_directory).resolve()
            lines = [f"Grep results for '{pattern}'", f"Target: {target}", "Matches:"]

            for match in matches[:max_results]:
                rel_path = _relative_to_root(root_path, match.get("file", match.get("path", "?")), scout.original_input)
                lines.append(
                    f"- {rel_path}:{match.get('line_number')} {match.get('content')}"
                )

            if len(matches) > max_results:
                lines.append(f"(trimmed to first {max_results} results)")

            return "\n".join(lines)
        finally:
            scout.cleanup()

    return await asyncio.to_thread(_run)


if __name__ == "__main__":
    # Run with HTTP/SSE transport so it behaves like refactoring-agent
    mcp.run()
