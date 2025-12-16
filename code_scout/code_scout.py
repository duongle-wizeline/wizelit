"""
Code Scout Agent - Fast synchronous symbol scanner.
Copied from legacy-code-modernizer and embedded for Wizelit standalone use.
Scans local directories or GitHub repositories for symbol usages using AST and grep.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .github_helper import GitHubHelper, is_github_url
from .github_cache import get_github_cache


@dataclass
class SymbolUsage:
    """Represents a symbol usage in the codebase."""

    file_path: str
    line_number: int
    column: int
    context: str
    usage_type: str  # 'import', 'call', 'definition', 'reference'


@dataclass
class DependencyNode:
    """Represents a node in the dependency graph."""

    symbol: str
    file_path: str
    dependencies: List[str]
    dependents: List[str]


class CodeScout:
    """
    Fast agent for code analysis and symbol tracking.
    Provides impact analysis, dependency graphing, and git blame lookups.
    Supports both local directories and GitHub repositories.
    """

    def __init__(self, root_directory: str, github_token: Optional[str] = None, use_cache: bool = True):
        """
        Initialize Code Scout.

        Args:
            root_directory: Local directory path or GitHub repository URL
            github_token: GitHub token for private repositories (optional)
            use_cache: Use GitHub repository cache (recommended for Fargate)
        """

        self.original_input = root_directory
        self.is_github = is_github_url(root_directory)
        self.github_helper = GitHubHelper(github_token) if self.is_github else None
        self.temp_dir: Optional[str] = None
        self.owns_temp_dir = False  # Track if we created the temp dir

        if self.is_github:
            if use_cache:
                cache = get_github_cache()
                cached_path = cache.get_or_clone(
                    root_directory,
                    github_token=github_token,
                    shallow=True,
                )
                if cached_path:
                    self.root_directory = Path(cached_path)
                    self.temp_dir = None
                    self.owns_temp_dir = False
                else:
                    raise ValueError(f"Failed to clone/cache GitHub repository: {root_directory}")
            else:
                self.temp_dir = self.github_helper.clone_repository(root_directory)
                if self.temp_dir:
                    self.root_directory = Path(self.temp_dir)
                    self.owns_temp_dir = True
                else:
                    raise ValueError(f"Failed to clone GitHub repository: {root_directory}")
        else:
            self.root_directory = Path(root_directory)

        self.symbol_usages: Dict[str, List[SymbolUsage]] = {}

    def cleanup(self) -> None:
        """Remove any temporary directory that was created by the scout."""
        if self.owns_temp_dir and self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception:
                # Best-effort cleanup; safe to ignore failure
                pass
            finally:
                self.temp_dir = None
                self.owns_temp_dir = False

    def __del__(self):
        """Cleanup temp directory if we created it."""
        self.cleanup()

    def scan_directory(self, pattern: Optional[str] = "*.py") -> Dict[str, List[SymbolUsage]]:
        """
        Scan directory for Python files and analyze them.

        Args:
            pattern: File pattern to match (default: *.py)

        Returns:
            Dictionary mapping symbols to their usages
        """
        python_files = list(self.root_directory.rglob(pattern))

        for file_path in python_files:
            try:
                self._analyze_file(file_path)
            except Exception as exc:
                print(f"Error analyzing {file_path}: {exc}")

        return self.symbol_usages

    def _analyze_file(self, file_path: Path) -> None:
        """Analyze a single Python file using AST."""
        with open(file_path, "r", encoding="utf-8") as file_handle:
            content = file_handle.read()

        try:
            tree = ast.parse(content, filename=str(file_path))
            visitor = SymbolVisitor(str(file_path), content)
            visitor.visit(tree)

            # Merge results
            for symbol, usages in visitor.symbol_usages.items():
                if symbol not in self.symbol_usages:
                    self.symbol_usages[symbol] = []
                self.symbol_usages[symbol].extend(usages)
        except SyntaxError as exc:
            print(f"Syntax error in {file_path}: {exc}")

    def analyze_github_file(self, github_url: str) -> Dict[str, List[SymbolUsage]]:
        """
        Analyze a single file from GitHub URL.

        Args:
            github_url: GitHub file URL

        Returns:
            Dictionary mapping symbols to their usages
        """
        if not self.github_helper:
            self.github_helper = GitHubHelper()

        result = self.github_helper.fetch_file_content(github_url)
        if not result:
            return {}

        content, filename = result

        try:
            tree = ast.parse(content, filename=filename)
            visitor = SymbolVisitor(github_url, content)
            visitor.visit(tree)

            for symbol, usages in visitor.symbol_usages.items():
                if symbol not in self.symbol_usages:
                    self.symbol_usages[symbol] = []
                self.symbol_usages[symbol].extend(usages)

            return visitor.symbol_usages
        except SyntaxError as exc:
            print(f"Syntax error in {github_url}: {exc}")
            return {}

    def find_symbol(self, symbol_name: str) -> List[SymbolUsage]:
        """
        Find all usages of a specific symbol.

        Args:
            symbol_name: Name of the symbol to find

        Returns:
            List of SymbolUsage objects
        """
        return self.symbol_usages.get(symbol_name, [])

    def grep_search(self, pattern: str, file_pattern: str = "*.py") -> List[Dict]:
        """
        Perform a grep search for a pattern in the directory.

        Args:
            pattern: Pattern to search for
            file_pattern: File pattern to search in

        Returns:
            List of matches with file, line number, and content
        """
        results = []

        try:
            cmd = [
                "grep",
                "-rn",
                "--include",
                file_pattern,
                pattern,
                str(self.root_directory),
            ]

            output = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )

            if output.stdout:
                for line in output.stdout.strip().split("\n"):
                    if ":" in line:
                        parts = line.split(":", 2)
                        if len(parts) >= 3:
                            results.append(
                                {
                                    "file": parts[0],
                                    "line_number": parts[1],
                                    "content": parts[2].strip(),
                                }
                            )
        except Exception as exc:
            print(f"Grep search error: {exc}")

        return results

    def build_dependency_graph(self) -> Dict[str, DependencyNode]:
        """
        Build a dependency graph from symbol usages.

        Returns:
            Dictionary mapping symbols to DependencyNode objects
        """
        graph: Dict[str, DependencyNode] = {}

        for symbol, usages in self.symbol_usages.items():
            definitions = [usage for usage in usages if usage.usage_type == "definition"]
            if definitions:
                node = DependencyNode(
                    symbol=symbol,
                    file_path=definitions[0].file_path,
                    dependencies=[],
                    dependents=[],
                )
                graph[symbol] = node

        for symbol, node in graph.items():
            file_usages = [
                usage
                for usage in self.symbol_usages.get(symbol, [])
                if usage.file_path == node.file_path
            ]

            for usage in file_usages:
                if usage.usage_type in ["import", "call", "reference"]:
                    for other_symbol in graph:
                        if other_symbol in usage.context and other_symbol != symbol:
                            if other_symbol not in node.dependencies:
                                node.dependencies.append(other_symbol)
                            if symbol not in graph[other_symbol].dependents:
                                graph[other_symbol].dependents.append(symbol)

        return graph

    def git_blame(self, file_path: str, line_number: int) -> Optional[Dict]:
        """
        Get git blame information for a specific line.

        Args:
            file_path: Path to the file
            line_number: Line number to blame

        Returns:
            Dictionary with blame information or None
        """
        try:
            cmd = [
                "git",
                "blame",
                "-L",
                f"{line_number},{line_number}",
                "--porcelain",
                file_path,
            ]

            output = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                cwd=self.root_directory,
            )

            lines = output.stdout.split("\n")
            blame_info = {}

            for line in lines:
                if line.startswith("author "):
                    blame_info["author"] = line.split(" ", 1)[1]
                elif line.startswith("author-time "):
                    blame_info["timestamp"] = line.split(" ", 1)[1]
                elif line.startswith("summary "):
                    blame_info["commit_message"] = line.split(" ", 1)[1]

            return blame_info
        except Exception as exc:
            print(f"Git blame error: {exc}")
            return None

    def analyze_impact(self, symbol_name: str) -> Dict:
        """
        Analyze the impact of changing a symbol.

        Args:
            symbol_name: Name of the symbol to analyze

        Returns:
            Dictionary with impact analysis results
        """
        usages = self.find_symbol(symbol_name)
        graph = self.build_dependency_graph()

        affected_files = {usage.file_path for usage in usages}

        impact = {
            "symbol": symbol_name,
            "total_usages": len(usages),
            "affected_files": list(affected_files),
            "file_count": len(affected_files),
            "usage_breakdown": {
                "imports": len([usage for usage in usages if usage.usage_type == "import"]),
                "calls": len([usage for usage in usages if usage.usage_type == "call"]),
                "references": len([usage for usage in usages if usage.usage_type == "reference"]),
                "definitions": len([usage for usage in usages if usage.usage_type == "definition"]),
            },
        }

        if symbol_name in graph:
            impact["dependencies"] = graph[symbol_name].dependencies
            impact["dependents"] = graph[symbol_name].dependents

        return impact


class SymbolVisitor(ast.NodeVisitor):
    """AST visitor to extract symbol information."""

    def __init__(self, file_path: str, content: str):
        self.file_path = file_path
        self.content = content
        self.lines = content.split("\n")
        self.symbol_usages: Dict[str, List[SymbolUsage]] = {}

    def _add_usage(self, symbol: str, node: ast.AST, usage_type: str):
        """Add a symbol usage."""
        if not hasattr(node, "lineno"):
            return

        line_idx = node.lineno - 1
        context = self.lines[line_idx] if line_idx < len(self.lines) else ""

        usage = SymbolUsage(
            file_path=self.file_path,
            line_number=node.lineno,
            column=node.col_offset if hasattr(node, "col_offset") else 0,
            context=context.strip(),
            usage_type=usage_type,
        )

        if symbol not in self.symbol_usages:
            self.symbol_usages[symbol] = []
        self.symbol_usages[symbol].append(usage)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        """Visit function definitions."""
        self._add_usage(node.name, node, "definition")
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        """Visit async function definitions."""
        self._add_usage(node.name, node, "definition")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        """Visit class definitions."""
        self._add_usage(node.name, node, "definition")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        """Visit import statements."""
        for alias in node.names:
            self._add_usage(alias.name, node, "import")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        """Visit from-import statements."""
        if node.module:
            for alias in node.names:
                self._add_usage(alias.name, node, "import")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        """Visit function calls."""
        if isinstance(node.func, ast.Name):
            self._add_usage(node.func.id, node, "call")
        elif isinstance(node.func, ast.Attribute):
            self._add_usage(node.func.attr, node, "call")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        """Visit name references."""
        if isinstance(node.ctx, (ast.Load, ast.Store)):
            self._add_usage(node.id, node, "reference")
        self.generic_visit(node)


if __name__ == "__main__":
    scout = CodeScout(".")
    scout.scan_directory()
    impact = scout.analyze_impact("some_function")
    import json

    print(json.dumps(impact, indent=2))
