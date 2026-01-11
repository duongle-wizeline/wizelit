"""
Job class for managing execution context and logging in Wizelit Agent Wrapper.
"""
import logging
import asyncio
import uuid
import time
from datetime import datetime, UTC
from typing import List, Optional, Awaitable, Any, TYPE_CHECKING
from fastmcp import Context

if TYPE_CHECKING:
    from database import DatabaseManager
    from core.wizelit_agent_wrapper.streaming import LogStreamer


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


class DatabaseLogHandler(logging.Handler):
    """
    Logging handler that persists log messages to PostgreSQL database.
    Writes asynchronously to avoid blocking the logging thread.
    """

    def __init__(self, job_id: str, db_manager: 'DatabaseManager'):
        super().__init__()
        self.job_id = job_id
        self.db_manager = db_manager
        self.setFormatter(logging.Formatter('%(message)s'))

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record by writing it to the database asynchronously.
        """
        try:
            # Import here to avoid circular dependency
            from models.job import JobLogModel
            from datetime import datetime

            async def write_log():
                try:
                    async with self.db_manager.get_session() as session:
                        log = JobLogModel(
                            job_id=self.job_id,
                            message=record.getMessage(),
                            level=record.levelname,
                            timestamp=datetime.now(UTC).replace(tzinfo=None)
                        )
                        session.add(log)
                        await session.commit()
                except Exception as e:
                    # Log to stderr but don't break execution
                    print(f"Error writing log to database: {e}", flush=True)

            # Schedule async write without awaiting
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(write_log())
            except RuntimeError:
                # No event loop running - log warning
                print(f"Warning: No event loop running, cannot write log to database", flush=True)
        except Exception:
            # Prevent exceptions in logging handler from breaking execution
            self.handleError(record)


class StreamingLogHandler(logging.Handler):
    """
    Logging handler that publishes log messages to Redis for real-time streaming.
    Enables push-based log delivery without polling.
    """

    def __init__(self, job_id: str, log_streamer: 'LogStreamer'):
        super().__init__()
        self.job_id = job_id
        self.log_streamer = log_streamer
        self.setFormatter(logging.Formatter('%(message)s'))

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record by publishing it to Redis Pub/Sub.
        """
        try:
            async def publish_log():
                try:
                    await self.log_streamer.publish_log(
                        self.job_id,
                        record.getMessage(),
                        record.levelname
                    )
                except Exception as e:
                    # Log to stderr but don't break execution
                    print(f"Error streaming log: {e}", flush=True)

            # Schedule async publish without awaiting
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(publish_log())
            except RuntimeError:
                # No event loop running - log warning
                print(f"Warning: No event loop running, cannot stream log to Redis", flush=True)
        except Exception as e:
            # Prevent exceptions in logging handler from breaking execution
            print(f"Error in StreamingLogHandler.emit: {e}", flush=True)
            self.handleError(record)


class Job:
    """
    Job instance that provides logging capabilities and execution context.
    Each decorated function execution gets a Job instance injected.
    """

    def __init__(
        self,
        ctx: Context,
        job_id: Optional[str] = None,
        db_manager: Optional['DatabaseManager'] = None,
        log_streamer: Optional['LogStreamer'] = None
    ):
        """
        Initialize a Job instance.

        Args:
            ctx: FastMCP Context for progress reporting
            job_id: Optional job identifier (generates UUID if not provided)
            db_manager: Optional DatabaseManager for persisting logs
            log_streamer: Optional LogStreamer for real-time log streaming
        """
        self._ctx = ctx
        self._id = job_id or f"JOB-{str(uuid.uuid4())[:8]}"
        self._status = "running"
        self._logs: List[str] = []
        self._result: Optional[str] = None
        self._error: Optional[str] = None
        self._db_manager = db_manager
        self._log_streamer = log_streamer

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
        """Set job status and publish status change event."""
        self._status = value
        # Note: Status changes are published explicitly via publish_status_change_sync()
        # This setter just updates the internal state

    @property
    def result(self) -> Optional[str | dict[str, Any]]:
        """Job result (if completed successfully)."""
        return self._result

    @result.setter
    def result(self, value: Optional[str | dict[str, Any]]) -> None:
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

    async def publish_status_change_sync(self) -> None:
        """Publish status change to Redis and wait for it to complete."""
        if self._log_streamer:
            try:
                await self._log_streamer.publish_status_change(
                    self._id,
                    self._status,
                    result=self._result,
                    error=self._error
                )
            except Exception as e:
                print(f"Error publishing status change: {e}", flush=True)

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
            self.status = "running"
            await self.publish_status_change_sync()  # Ensure published
            # Persist initial job state
            await self.persist_to_db()

            heartbeat_task = asyncio.create_task(self._heartbeat(heartbeat_interval))
            try:
                result = await coro
                # Store string/dict results for convenience
                if isinstance(result, str|dict):
                    self.result = result
                if self.status == "running":
                    self.status = "completed"
                    await self.publish_status_change_sync()  # Ensure published before returning
                    # Persist completion state
                    await self.persist_to_db()
                return result
            except Exception as e:  # noqa: BLE001 - we deliberately capture all
                self.error = str(e)
                self.status = "failed"
                await self.publish_status_change_sync()  # Ensure published before raising
                # Persist failure state
                await self.persist_to_db()
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

        # Add MemoryLogHandler for internal storage (backward compatibility)
        memory_handler = MemoryLogHandler(self._logs)
        memory_handler.setLevel(logging.INFO)
        self._logger.addHandler(memory_handler)

        # Add DatabaseLogHandler if db_manager provided
        if self._db_manager:
            db_handler = DatabaseLogHandler(self._id, self._db_manager)
            db_handler.setLevel(logging.INFO)
            self._logger.addHandler(db_handler)

        # Add StreamingLogHandler if log_streamer provided
        if self._log_streamer:
            streaming_handler = StreamingLogHandler(self._id, self._log_streamer)
            streaming_handler.setLevel(logging.INFO)
            self._logger.addHandler(streaming_handler)

        # Prevent propagation to root logger
        self._logger.propagate = False

    async def persist_to_db(self) -> None:
        """
        Persist the job state to the database.
        Creates or updates the job record.
        """
        if not self._db_manager:
            return

        try:
            from models.job import JobModel

            async with self._db_manager.get_session() as session:
                # Check if job already exists
                existing_job = await session.get(JobModel, self._id)

                if existing_job:
                    # Update existing job
                    existing_job.status = self._status
                    existing_job.result = self._result
                    existing_job.error = self._error
                    existing_job.updated_at = datetime.now(UTC).replace(tzinfo=None)
                else:
                    # Create new job
                    job = JobModel(
                        id=self._id,
                        status=self._status,
                        result=self._result,
                        error=self._error
                    )
                    session.add(job)

                await session.commit()
        except Exception as e:
            # Log error but don't break execution
            print(f"Error persisting job to database: {e}", flush=True)
