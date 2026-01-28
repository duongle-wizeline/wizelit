"""
Custom exceptions for Wizelit with helpful error messages and suggestions.
"""


class WizelitException(Exception):
    """Base exception class for all Wizelit errors."""

    def __init__(self, message: str, suggestion: str = ""):
        self.message = message
        self.suggestion = suggestion
        full_message = message
        if suggestion:
            full_message = f"{message}\n💡 Suggestion: {suggestion}"
        super().__init__(full_message)


class MCPConnectionError(WizelitException):
    """Raised when MCP (Model Context Protocol) connection fails."""

    def __init__(self, server_name: str, url: str, original_error: str = ""):
        message = f"Failed to connect to MCP server '{server_name}' at {url}"
        if original_error:
            message += f": {original_error}"
        suggestion = (
            f"1. Verify {server_name} is running and accessible at {url}\n"
            f"2. Check network connectivity and firewall settings\n"
            f"3. Try removing and re-adding the server via the Chainlit UI\n"
            f"4. Check the server logs for detailed error information"
        )
        super().__init__(message, suggestion)


class MCPToolLoadError(WizelitException):
    """Raised when MCP tools cannot be loaded from a server."""

    def __init__(self, server_name: str, original_error: str = ""):
        message = f"Failed to load tools from MCP server '{server_name}'"
        if original_error:
            message += f": {original_error}"
        suggestion = (
            f"1. Verify {server_name} is properly configured and running\n"
            f"2. Check if the server implements required MCP protocol\n"
            f"3. Verify the server has tools/resources defined\n"
            f"4. Check {server_name}'s logs for initialization errors"
        )
        super().__init__(message, suggestion)


class GraphBuildError(WizelitException):
    """Raised when the agent graph cannot be built."""

    def __init__(self, original_error: str = ""):
        message = "Failed to build the agent graph"
        if original_error:
            message += f": {original_error}"
        suggestion = (
            "1. Ensure at least one MCP server is connected\n"
            "2. Check that all MCP servers are running and accessible\n"
            "3. Verify AWS credentials are set (for Bedrock LLM)\n"
            "4. Check application logs for detailed error information\n"
            "5. Try restarting the application"
        )
        super().__init__(message, suggestion)


class GraphExecutionError(WizelitException):
    """Raised when graph execution fails during a query."""

    def __init__(self, original_error: str = "", error_type: str = ""):
        message = "Error executing the agent graph"
        if original_error:
            message += f": {original_error}"

        if "closedresourceerror" in (error_type or "").lower():
            suggestion = (
                "1. MCP server connection was interrupted\n"
                "2. Try restarting the MCP servers or the application\n"
                "3. Check if MCP servers are still running\n"
                "4. Verify network connectivity"
            )
        elif "no running event loop" in (error_type or "").lower():
            suggestion = (
                "1. Asyncio event loop is not running\n"
                "2. This may indicate a threading issue\n"
                "3. Try restarting the application\n"
                "4. Check if you're mixing sync and async code"
            )
        else:
            suggestion = (
                "1. Check the full error log for detailed information\n"
                "2. Verify all MCP servers are running\n"
                "3. Try submitting the query again\n"
                "4. If the error persists, restart the application"
            )
        super().__init__(message, suggestion)


class DatabaseError(WizelitException):
    """Raised when database operations fail."""

    def __init__(self, operation: str, original_error: str = ""):
        message = f"Database error during {operation}"
        if original_error:
            message += f": {original_error}"
        suggestion = (
            f"1. Verify the database is running and accessible\n"
            f"2. Check database credentials and connection string\n"
            f"3. Ensure the database has sufficient disk space\n"
            f"4. Try running database migrations: make db-migrate\n"
            f"5. Check database logs for detailed information"
        )
        super().__init__(message, suggestion)


class ConfigurationError(WizelitException):
    """Raised when configuration is invalid or missing."""

    def __init__(self, config_key: str, original_error: str = ""):
        message = f"Configuration error: {config_key}"
        if original_error:
            message += f" ({original_error})"
        suggestion = (
            f"1. Check if {config_key} is set in environment variables\n"
            f"2. Verify the value is valid and properly formatted\n"
            f"3. Check the .env file if using local configuration\n"
            f"4. Refer to README.md for configuration documentation\n"
            f"5. Restart the application after changing configuration"
        )
        super().__init__(message, suggestion)


class StreamingError(WizelitException):
    """Raised when log streaming fails."""

    def __init__(self, original_error: str = ""):
        message = "Error in log streaming"
        if original_error:
            message += f": {original_error}"
        suggestion = (
            "1. Check if Redis is running and accessible\n"
            "2. Verify Redis connection string (REDIS_URL)\n"
            "3. Check network connectivity to Redis\n"
            "4. Try restarting Redis and the application\n"
            "5. Log streaming is optional; the application will continue without it"
        )
        super().__init__(message, suggestion)


class JobExecutionError(WizelitException):
    """Raised when a job fails during execution."""

    def __init__(self, job_id: str, original_error: str = ""):
        message = f"Job {job_id} failed during execution"
        if original_error:
            message += f": {original_error}"
        suggestion = (
            f"1. Check the job logs for detailed error information\n"
            f"2. Verify the job inputs are valid and complete\n"
            f"3. Check if required MCP tools are available\n"
            f"4. Try running the job again\n"
            f"5. If the issue persists, contact support"
        )
        super().__init__(message, suggestion)


class InvalidInputError(WizelitException):
    """Raised when user input is invalid or malformed."""

    def __init__(self, field: str, expected_format: str = "", original_error: str = ""):
        message = f"Invalid input for {field}"
        if expected_format:
            message += f". Expected: {expected_format}"
        if original_error:
            message += f". {original_error}"
        suggestion = (
            f"1. Check the format of your {field} input\n"
            f"2. Refer to the API documentation for valid formats\n"
            f"3. Ensure all required fields are provided\n"
            f"4. Verify special characters are properly escaped"
        )
        super().__init__(message, suggestion)


class TimeoutError(WizelitException):
    """Raised when an operation exceeds the timeout limit."""

    def __init__(self, operation: str, timeout_seconds: int):
        message = f"Operation '{operation}' exceeded timeout limit of {timeout_seconds} seconds"
        suggestion = (
            f"1. The {operation} took too long to complete\n"
            f"2. Check if resources are sufficient (CPU, memory)\n"
            f"3. Try simplifying the query or input\n"
            f"4. Check MCP server logs for slow operations\n"
            f"5. Consider increasing TASK_TIMEOUT environment variable if appropriate"
        )
        super().__init__(message, suggestion)
