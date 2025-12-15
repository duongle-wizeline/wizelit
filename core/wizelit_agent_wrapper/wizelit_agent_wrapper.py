# wizelit_sdk/core.py
import asyncio
import inspect
from typing import Callable, Any, Optional, Literal
from functools import wraps
from fastmcp import FastMCP, Context
from fastmcp.dependencies import CurrentContext

# Reusable framework constants
LLM_FRAMEWORK_CREWAI = "crewai"
LLM_FRAMEWORK_LANGCHAIN = "langchain"
LLM_FRAMEWORK_LANGGRAPH = "langraph"

LlmFrameworkType = Literal['crewai', 'langchain', 'langraph', None]


class WizelitAgentWrapper:
    """
    Main wrapper class that converts Python functions into MCP server tools.
    Built on top of fast-mcp with enhanced streaming and agent framework support.
    """

    def __init__(self, name: str,transport: str = "streamable-http", host: str = "0.0.0.0", port: int = 8080, version: str = "1.0.0"):
        """
        Initialize the Wizelit Agent.

        Args:
            name: Name of the MCP server
            version: Version string for the server
        """
        self._mcp = FastMCP(name=name)
        self._name = name
        self._version = version
        self._tools = {}
        self._host = host
        self._transport = transport
        self._port = port
        print(f"WizelitAgentWrapper initialized with name: {name}, transport: {transport}, host: {host}, port: {port}")

    def ingest(
        self,
        is_long_running: bool = False,
        description: Optional[str] = None,
        llm_framework: LlmFrameworkType = None,
        stream_logs: bool = True
    ):
        """
        Decorator to convert a function into an MCP tool.

        Args:
            is_long_running: If True, enables progress reporting
            description: Human-readable description of the tool
            llm_framework: LLM framework name ("crewai", "langchain", "langraph", or None)
            stream_logs: If True, captures and streams print statements

        Usage:
            @agent.ingest(is_long_running=True, description="Forecasts revenue")
            def forecast_revenue(region: str) -> str:
                return "Revenue projection: $5M"
        """
        def decorator(func: Callable) -> Callable:
            # Store original function metadata
            tool_name = func.__name__
            tool_description = description or func.__doc__ or f"Execute {tool_name}"

            # Detect if function is async
            is_async = inspect.iscoroutinefunction(func)

            # Get function signature
            sig = inspect.signature(func)

            # Build new signature with ctx: Context = CurrentContext() as LAST parameter
            # This follows fast-mcp v2.14+ convention for dependency injection
            params_list = list(sig.parameters.values())

            # Add ctx as the last parameter with CurrentContext() as default
            ctx_param = inspect.Parameter(
                'ctx',
                inspect.Parameter.KEYWORD_ONLY,
                default=CurrentContext(),
                annotation=Context
            )
            params_list.append(ctx_param)

            new_sig = sig.replace(parameters=params_list)

            # Create the wrapper function
            async def tool_wrapper(*args, **kwargs):
                """MCP-compliant wrapper with streaming."""
                # Extract ctx from kwargs (injected by fast-mcp via CurrentContext())
                ctx = kwargs.pop('ctx', None)
                if ctx is None:
                    raise ValueError("Context not injected by fast-mcp")

                return await self._execute_tool(
                    func, ctx, is_async, is_long_running,
                    stream_logs, llm_framework, tool_name, *args, **kwargs
                )


            # Set the signature with ctx as last parameter with CurrentContext() default
            tool_wrapper.__signature__ = new_sig
            tool_wrapper.__name__ = tool_name
            tool_wrapper.__doc__ = tool_description

            # Copy annotations and add Context
            new_annotations = {}
            if hasattr(func, '__annotations__'):
                new_annotations.update(func.__annotations__)
            new_annotations['ctx'] = Context
            tool_wrapper.__annotations__ = new_annotations

            # Register with fast-mcp
            registered_tool = self._mcp.tool(description=tool_description)(tool_wrapper)

            # Store tool metadata
            self._tools[tool_name] = {
                'function': func,
                'wrapper': registered_tool,
                'is_long_running': is_long_running,
                'llm_framework': llm_framework
            }

            # Return original function so it can still be called directly
            return func
        return decorator

    async def _execute_tool(
        self,
        func: Callable,
        ctx: Context,
        is_async: bool,
        is_long_running: bool,
        stream_logs: bool,
        llm_framework: list[str],
        tool_name: str,
        **kwargs
    ) -> Any:
        """Central execution method for all tools."""

        # Start progress reporting for long-running tasks
        if is_long_running:
            await ctx.report_progress(
                progress=0,
                total=100,
                message=f"Starting {tool_name}..."
            )

        try:
            # Execute with appropriate streaming based on detected frameworks
            if llm_framework:
                result = await self._execute_with_framework_streaming(
                    func, ctx, llm_framework, is_async, stream_logs, **kwargs
                )
            else:
                result = await self._execute_with_basic_streaming(
                    func, ctx, is_async, stream_logs, is_long_running, **kwargs
                )

            # Report completion
            if is_long_running:
                await ctx.report_progress(
                    progress=100,
                    total=100,
                    message=f"Completed {tool_name}"
                )

            return result

        except Exception as e:
            # Stream error information
            await ctx.report_progress(
                progress=0,
                message=f"Error in {tool_name}: {str(e)}"
            )
            raise

    async def _execute_with_basic_streaming(
        self,
        func: Callable,
        ctx: Context,
        is_async: bool,
        stream_logs: bool,
        is_long_running: bool,
        **kwargs
    ) -> Any:
        """Execute function with basic log streaming."""

        if stream_logs:
            # Capture print statements and stream them
            import sys
            from io import StringIO

            captured_output = StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured_output

            try:
                # Execute function
                if is_async:
                    result = await func(**kwargs)
                else:
                    result = await asyncio.to_thread(func, **kwargs)

                # Stream captured output
                output = captured_output.getvalue()
                if output:
                    await ctx.report_progress(progress=100,message=output)

                return result
            finally:
                sys.stdout = old_stdout
        else:
            # Simple execution without log capture
            if is_async:
                return await func(**kwargs)
            else:
                return await asyncio.to_thread(func, **kwargs)

    async def _execute_with_framework_streaming(
        self,
        func: Callable,
        ctx: Context,
        llm_framework: LlmFrameworkType,
        is_async: bool,
        stream_logs: bool,
        **kwargs
    ) -> Any:
        """
        Execute function with framework-specific streaming.
        This is where CrewAI, LangGraph, etc. integrations hook in.
        """

        # For now, fall back to basic streaming
        # Framework-specific integrations will be added in separate modules
        if llm_framework=='crewai':
            # TODO: Import and use CrewAI streamer
            pass

        if llm_framework == 'langchain' or llm_framework=='langgraph' :
            # TODO: Import and use LangGraph streamer
            pass

        # Fallback to basic streaming
        return await self._execute_with_basic_streaming(
            func, ctx, is_async, stream_logs, True, **kwargs
        )

    def run(
        self,
        transport: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        **kwargs
    ):
        """
        Start the MCP server.

        Args:
            transport: MCP transport type ('stdio', 'http', 'streamable-http')
            host: Host to bind to (for HTTP transports)
            port: Port to bind to (for HTTP transports)
            **kwargs: Additional arguments passed to fast-mcp
        """
        transport = transport or self._transport
        host = host or self._host
        port = port or self._port
        print(f"🚀 Starting {self._name} MCP Server")
        print(f"📡 Transport: {transport}")

        if transport in ["http", "streamable-http"]:
            print(f"🌐 Listening on {host}:{port}")

        print(f"🔧 Registered {len(self._tools)} tool(s):")
        for tool_name, tool_info in self._tools.items():
            lr_status = "⏱️  long-running" if tool_info['is_long_running'] else "⚡ fast"
            llm_framework = tool_info['llm_framework'] if tool_info['llm_framework'] else "none"
            print(f"   • {tool_name} [{lr_status}] [llm_framework: {llm_framework}]")

        # Start the server
        self._mcp.run(transport=transport, host=host, port=port, **kwargs)

    def list_tools(self) -> dict:
        """Return metadata about all registered tools."""
        return {
            name: {
                'is_long_running': info['is_long_running'],
                'llm_framework': info['llm_framework']
            }
            for name, info in self._tools.items()
        }

