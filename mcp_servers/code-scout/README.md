# Code Scout MCP Server

Fast synchronous symbol scanner for Python codebases. Supports both local directories and GitHub repositories.

## Features

- **Symbol Scanning**: AST-based Python code analysis
- **Impact Analysis**: Understand the blast radius of changes
- **Dependency Graphing**: Build and visualize dependencies
- **Grep Search**: Fast text-based searches across codebases
- **Git Blame**: Track code ownership and history
- **GitHub Support**: Analyze repositories directly via URL

## Installation

```bash
# From the wizelit root directory
cd mcp_servers/code-scout
```

## Usage

### Run as MCP Server (SSE)

Starts an SSE FastMCP server on port **1338** (0.0.0.0 by default).

```bash
python server.py
```

### Available Tools

1. **scan_directory** - Scan and analyze Python files

   - `root_directory`: Path or GitHub URL
   - `pattern`: File pattern (default: \*.py)
   - `github_token`: Optional GitHub token

2. **find_symbol** - Find symbol usages

   - `root_directory`: Path or GitHub URL
   - `symbol_name`: Symbol to find
   - `github_token`: Optional GitHub token

3. **analyze_impact** - Impact analysis

   - `root_directory`: Path or GitHub URL
   - `symbol_name`: Symbol to analyze
   - `github_token`: Optional GitHub token

4. **grep_search** - Text search

   - `root_directory`: Path or GitHub URL
   - `pattern`: Search pattern
   - `file_pattern`: File pattern (default: \*.py)
   - `github_token`: Optional GitHub token

5. **git_blame** - Git history

   - `root_directory`: Path or GitHub URL
   - `file_path`: File to blame
   - `line_number`: Line number
   - `github_token`: Optional GitHub token

6. **build_dependency_graph** - Dependency analysis
   - `root_directory`: Path or GitHub URL
   - `github_token`: Optional GitHub token

## Configuration

Add to your MCP client configuration (expects SSE transport on port 1338):

```json
{
  "mcpServers": {
    "code-scout": {
      "command": "python",
      "args": ["/path/to/wizelit/mcp_servers/code-scout/server.py"]
    }
  }
}
```

## Examples

### Scan a local directory

```json
{
  "name": "scan_directory",
  "arguments": {
    "root_directory": "/path/to/project",
    "pattern": "*.py"
  }
}
```

### Analyze a GitHub repository

```json
{
  "name": "find_symbol",
  "arguments": {
    "root_directory": "https://github.com/pallets/flask",
    "symbol_name": "Flask"
  }
}
```

### Search for patterns

```json
{
  "name": "grep_search",
  "arguments": {
    "root_directory": "/path/to/project",
    "pattern": "TODO",
    "file_pattern": "*.py"
  }
}
```

## Requirements

- Python 3.13+
- mcp package
- Dependencies from wizelit/code_scout module
