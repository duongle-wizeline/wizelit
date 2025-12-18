# wizelit_sdk/core.py
import asyncio
import inspect
import logging
from typing import Callable, Any, Optional, Literal, Dict
from contextvars import ContextVar
from fastmcp import FastMCP, Context
from fastmcp.dependencies import CurrentContext
from core.wizelit_agent_wrapper.job import Job

# Reusable framework constants
LLM_FRAMEWORK_CREWAI = "crewai"
LLM_FRAMEWORK_LANGCHAIN = "langchain"
LLM_FRAMEWORK_LANGGRAPH = "langraph"

LlmFrameworkType = Literal['crewai', 'langchain', 'langraph', None]

# Context variable for current Job instance
_current_job: ContextVar[Optional[Job]] = ContextVar('_current_job', default=None)


class CurrentJob:
    """
    Dependency injection class for Job instances.
    Similar to CurrentContext(), returns the current Job instance from context.
    """
    def __call__(self) -> Optional[Job]:
        """Return the current Job instance from context."""
        return _current_job.get()


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
        self._jobs: Dict[str, Job] = {}  # Store jobs by job_id
        self._host = host
        self._transport = transport
        self._port = port
        print(f"WizelitAgentWrapper initialized with name: {name}, transport: {transport}, host: {host}, port: {port}")

    def ingest(
        self,
        is_long_running: bool = False,
        description: Optional[str] = None,
    ):
        """
        Decorator to convert a function into an MCP tool.

        Args:
            is_long_running: If True, enables progress reporting
            description: Human-readable description of the tool

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

            # Check if function has 'job' parameter (for backward compatibility)
            has_job_param = sig.parameters.get('job') is not None


            if is_long_running and not has_job_param:
                raise ValueError("is_long_running is True but 'job' parameter is not provided")


            # Remove original 'job' parameter if it exists
            if has_job_param:
                params_list = [p for p in params_list if p.name != 'job']

            # Add ctx as the last parameter with CurrentContext() as default
            ctx_param = inspect.Parameter(
                'ctx',
                inspect.Parameter.KEYWORD_ONLY,
                default=CurrentContext(),
                annotation=Context
            )
            params_list.append(ctx_param)

            # Add job parameter if function signature includes it
            # Use None as default - we'll resolve CurrentJob() in the wrapper at call time
            if has_job_param:
                job_param = inspect.Parameter(
                    'job',
                    inspect.Parameter.KEYWORD_ONLY,
                    default=None,
                    annotation=Any  # Use Any to avoid Pydantic issues
                )
                params_list.append(job_param)

            new_sig = sig.replace(parameters=params_list)

            # Create the wrapper function
            async def tool_wrapper(*args, **kwargs):
                """MCP-compliant wrapper with streaming."""
                # Extract ctx from kwargs (injected by fast-mcp via CurrentContext())
                ctx = kwargs.pop('ctx', None)
                if ctx is None:
                    raise ValueError("Context not injected by fast-mcp")

                # Extract job from kwargs if present
                # Handle case where fast-mcp might pass CurrentJob instance instead of Job
                job = None
                if has_job_param:
                    job = kwargs.pop('job', None)
                    # If job is a CurrentJob instance, call it to get the actual Job
                    if isinstance(job, CurrentJob):
                        job = job()
                    # If job is still None, _execute_tool will create it

                # Bind all arguments (including positional) to the original function signature
                # This ensures parameters are correctly passed even if fast-mcp uses positional args
                # Create a signature without 'job' since we've already extracted it
                func_sig = inspect.signature(func)
                if has_job_param and 'job' in func_sig.parameters:
                    # Remove 'job' from signature for binding since we handle it separately
                    params_without_job = {
                        name: param for name, param in func_sig.parameters.items()
                        if name != 'job'
                    }
                    func_sig = func_sig.replace(parameters=list(params_without_job.values()))

                try:
                    bound_args = func_sig.bind(*args, **kwargs)
                    bound_args.apply_defaults()
                    func_kwargs = bound_args.arguments
                except TypeError as e:
                    # Fallback: if binding fails, use kwargs as-is (shouldn't happen normally)
                    logging.warning(f"Failed to bind arguments for {tool_name}: {e}. Args: {args}, Kwargs: {kwargs}")
                    func_kwargs = kwargs

                return await self._execute_tool(
                    func, ctx, is_async, is_long_running,
                    tool_name, job, **func_kwargs
                )


            # Set the signature with ctx as last parameter with CurrentContext() default
            tool_wrapper.__signature__ = new_sig
            tool_wrapper.__name__ = tool_name
            tool_wrapper.__doc__ = tool_description

            # Copy annotations and add Context
            # Note: We don't add job annotation here since we use Any and exclude it from schema
            new_annotations = {}
            if hasattr(func, '__annotations__'):
                new_annotations.update(func.__annotations__)
            new_annotations['ctx'] = Context
            if has_job_param:
                new_annotations['job'] = Any  # Use Any instead of Job to avoid Pydantic schema issues
            tool_wrapper.__annotations__ = new_annotations

            # Register with fast-mcp
            # Exclude ctx and job from schema generation since they're dependency-injected
            exclude_args = ['ctx']
            if has_job_param:
                exclude_args.append('job')
            registered_tool = self._mcp.tool(description=tool_description, exclude_args=exclude_args)(tool_wrapper)

            # Store tool metadata
            self._tools[tool_name] = {
                'function': func,
                'wrapper': registered_tool,
                'is_long_running': is_long_running,
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
        tool_name: str,
        job: Optional[Job] = None,
        **kwargs
    ) -> Any:
        """Central execution method for all tools."""

        token = None
        # Create Job instance if not provided
        if job is None and is_long_running:
            job = Job(ctx)

            # Store job in jobs dictionary for later retrieval
            self._jobs[job.id] = job

            # Set CurrentJob context so CurrentJob() can retrieve it
            token = _current_job.set(job)

        try:
            try:
                # Add job to kwargs if function signature includes it
                func_sig = inspect.signature(func)
                if 'job' in func_sig.parameters and job is not None:
                    kwargs['job'] = job

                # Execute function (async or sync)
                logging.info(f"kwargs: {kwargs}")
                if is_async:
                    result = await func(**kwargs)
                else:
                    result = await asyncio.to_thread(func, **kwargs)

                # Ensure result is never None for functions that should return strings
                func_sig = inspect.signature(func)
                if result is None:
                    return_annotation = func_sig.return_annotation
                    # Check if return type is str (handle both direct str and Optional[str])
                    is_str_return = (
                        return_annotation == str or
                        (hasattr(return_annotation, '__origin__') and return_annotation.__origin__ is str) or
                        (hasattr(return_annotation, '__args__') and str in getattr(return_annotation, '__args__', []))
                    )
                    if is_str_return:
                        logging.warning(f"Function {tool_name} returned None but should return str. Returning empty string.")
                        result = ""

                return result

            except Exception as e:
                # Mark job as failed
                job.status = "failed"

                # Stream error information
                await ctx.report_progress(
                    progress=0,
                    message=f"Error in {tool_name}: {str(e)}"
                )
                raise
        finally:
            # Reset CurrentJob context only if we set it
            if token is not None:
                _current_job.reset(token)

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


        if transport in ["http", "streamable-http"]:
            print(f"🌐 Listening on {host}:{port}")

        print(f"🔧 Registered {len(self._tools)} tool(s):")
        for tool_name, tool_info in self._tools.items():
            lr_status = "⏱️  long-running" if tool_info['is_long_running'] else "⚡ fast"
            print(f"   • {tool_name} [{lr_status}]")

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

    def get_job_logs(self, job_id: str) -> Optional[list]:
        """
        Get logs for a specific job by job_id.

        Args:
            job_id: The job identifier

        Returns:
            List of log messages (timestamped strings) if job exists, None otherwise
        """
        job = self._jobs.get(job_id)
        if job is None:
            return None
        return job.logs

    def get_job_status(self, job_id: str) -> Optional[str]:
        """
        Get status for a specific job by job_id.

        Args:
            job_id: The job identifier

        Returns:
            Job status ("running", "completed", "failed") if job exists, None otherwise
        """
        job = self._jobs.get(job_id)
        if job is None:
            return None
        return job.status

    def get_job(self, job_id: str) -> Optional[Job]:
        """
        Get a Job instance by job_id.

        Args:
            job_id: The job identifier

        Returns:
            Job instance if exists, None otherwise
        """
        return self._jobs.get(job_id)

    def get_jobs(self) -> list[Job]:
        """
        Get all Job instances.

        Returns:
            List of Job instances
        """
        return list(self._jobs.values())

    def set_job_status(self, job_id: str, status: str) -> bool:
        """
        Set the status of a job by job_id.

        Args:
            job_id: The job identifier
            status: New status ("running", "completed", "failed")

        Returns:
            True if job exists and status was updated, False otherwise
        """
        job = self._jobs.get(job_id)
        if job is None:
            return False
        job.status = status
        return True

    def set_job_result(self, job_id: str, result: Optional[str | dict[str, Any]]) -> bool:
        """
        Set the result of a job by job_id.

        Args:
            job_id: The job identifier
            result: The job result

        Returns:
            True if job exists and result was updated, False otherwise
        """
        job = self._jobs.get(job_id)
        if job is None:
            return False
        job.result = result
        return True

    def set_job_error(self, job_id: str, error: Optional[str]) -> bool:
        """
        Set the error message of a job by job_id.

        Args:
            job_id: The job identifier
            error: The error message

        Returns:
            True if job exists and error was updated, False otherwise
        """
        job = self._jobs.get(job_id)
        if job is None:
            return False
        job.error = error
        return True

