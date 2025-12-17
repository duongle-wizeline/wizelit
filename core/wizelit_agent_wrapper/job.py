"""
Job class for managing execution context and logging in Wizelit Agent Wrapper.
"""
import logging
import asyncio
import uuid
import time
from typing import List, Optional, Awaitable, Any
from fastmcp import Context


class MemoryLogHandler(logging.Handler):
    """
    Custom logging handler that stores log messages in a list.
    """

    def __init__(self, logs_list: List[str]):
        super().__init__()
        self.logs_list = logs_list
        self.setFormatter(logging.Formatter('%(message)s'))

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record by appending it to the logs list.
        """
        try:
            # Format timestamp
            ts = time.strftime("%H:%M:%S")

            # Format message with level and timestamp
            formatted_message = f"[{record.levelname}] [{ts}] {record.getMessage()}"

            # Append to logs list
            self.logs_list.append(formatted_message)
        except Exception:
            # Prevent exceptions in logging handler from breaking execution
            self.handleError(record)


class Job:
    """
    Job instance that provides logging capabilities and execution context.
    Each decorated function execution gets a Job instance injected.
    """

    def __init__(self, ctx: Context, job_id: Optional[str] = None):
        """
        Initialize a Job instance.

        Args:
            ctx: FastMCP Context for progress reporting
            job_id: Optional job identifier (generates UUID if not provided)
        """
        self._ctx = ctx
        self._id = job_id or f"JOB-{str(uuid.uuid4())[:8]}"
        self._status = "running"
        self._logs: List[str] = []
        self._result: Optional[str] = None
        self._error: Optional[str] = None

        # Set up logger
        self._setup_logger(ctx)

    @property
    def id(self) -> str:
        """Unique job identifier."""
        return self._id

    @property
    def logger(self) -> logging.Logger:
        """Python Logger instance configured with MCP streaming handler."""
        return self._logger

    @property
    def logs(self) -> List[str]:
        """List of log messages (timestamped strings)."""
        return self._logs

    @property
    def status(self) -> str:
        """Job status: 'running', 'completed', or 'failed'."""
        return self._status

    @status.setter
    def status(self, value: str) -> None:
        """Set job status."""
        self._status = value

    @property
    def result(self) -> Optional[str]:
        """Job result (if completed successfully)."""
        return self._result

    @result.setter
    def result(self, value: Optional[str]) -> None:
        """Set job result."""
        self._result = value

    @property
    def error(self) -> Optional[str]:
        """Job error message (if failed)."""
        return self._error

    @error.setter
    def error(self, value: Optional[str]) -> None:
        """Set job error message."""
        self._error = value

    async def _heartbeat(self, interval_seconds: float = 5.0) -> None:
        """
        Periodically append a heartbeat log while a job is running so the UI
        has visible progress even during long operations.
        """
        start = time.monotonic()
        while self._status == "running":
            await asyncio.sleep(interval_seconds)
            # Re-check in case status changed while sleeping
            if self._status != "running":
                break
            elapsed = int(time.monotonic() - start)
            # Use logger so logs are captured in memory and streamed if enabled
            self.logger.info(f"⏳ Still working... ({elapsed}s)")

    def run(
        self,
        coro: Awaitable[Any],
        *,
        heartbeat_interval: float = 5.0,
    ) -> "asyncio.Task[Any]":
        """
        Run a coroutine in the background, managing heartbeat, status, result, and error.

        This is intended for long-running jobs. It:
        - Marks the job as running
        - Starts a heartbeat logger
        - Awaits the provided coroutine
        - On success: stores the result (if string) and marks status 'completed'
        - On failure: stores the error message and marks status 'failed'
        """
        import asyncio

        async def _runner() -> Any:
            self._status = "running"
            heartbeat_task = asyncio.create_task(self._heartbeat(heartbeat_interval))
            try:
                result = await coro
                # Store string results for convenience
                if isinstance(result, str):
                    self._result = result
                if self._status == "running":
                    self._status = "completed"
                return result
            except Exception as e:  # noqa: BLE001 - we deliberately capture all
                self._error = str(e)
                self._status = "failed"
                # Also log the error so it shows up in logs UI
                self.logger.error(f"❌ [System] Error: {e}")
                raise
            finally:
                # Stop heartbeat
                heartbeat_task.cancel()
                try:
                    import contextlib

                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat_task
                except Exception:
                    # Ignore heartbeat shutdown errors
                    pass

        # Schedule the runner in the current event loop and return the Task
        return asyncio.create_task(_runner())

    def _setup_logger(self, ctx: Context) -> None:
        """
        Configure logger with custom handlers for streaming and storage.

        Args:
            ctx: FastMCP Context for progress reporting
        """
        _ = ctx  # ctx reserved for potential streaming handler setup
        # Create logger with unique name per job
        logger_name = f"wizelit.job.{self._id}"
        self._logger = logging.getLogger(logger_name)

        # Set level to INFO by default
        self._logger.setLevel(logging.INFO)

        # Remove any existing handlers to avoid duplicates
        self._logger.handlers.clear()

        # Add MemoryLogHandler for internal storage
        memory_handler = MemoryLogHandler(self._logs)
        memory_handler.setLevel(logging.INFO)
        self._logger.addHandler(memory_handler)

        # Prevent propagation to root logger
        self._logger.propagate = False

