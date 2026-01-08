"""
Real-time log streaming using Redis Pub/Sub.
Enables push-based log delivery from workers to hub without polling.
"""
import json
import logging
from datetime import datetime, UTC
from typing import AsyncGenerator, Optional, Dict, Any
import asyncio

try:
    import redis.asyncio as redis
except ImportError:
    redis = None

logger = logging.getLogger(__name__)


class LogStreamer:
    """
    Manages real-time log streaming via Redis Pub/Sub.

    Workers publish log events to job-specific channels.
    Hub subscribes to these channels for real-time updates.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        """
        Initialize the log streamer.

        Args:
            redis_url: Redis connection URL
        """
        if redis is None:
            raise ImportError(
                "redis package is required for log streaming. "
                "Install it with: pip install redis"
            )

        self.redis_url = redis_url
        self._redis: Optional[redis.Redis] = None
        self._pubsub: Optional[redis.client.PubSub] = None

    async def _ensure_connected(self) -> redis.Redis:
        """Ensure Redis connection is established."""
        if self._redis is None:
            try:
                self._redis = redis.from_url(
                    self.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=5,
                    socket_keepalive=True,
                )
                # Test connection
                await self._redis.ping()
                logger.info(f"Connected to Redis at {self.redis_url}")
            except Exception as e:
                logger.error(f"Failed to connect to Redis: {e}")
                raise
        return self._redis

    async def publish_log(
        self,
        job_id: str,
        message: str,
        level: str = "INFO",
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Publish a log event to Redis.

        Args:
            job_id: Job identifier
            message: Log message
            level: Log level (INFO, ERROR, WARNING, DEBUG)
            metadata: Additional metadata to include
        """
        try:
            redis_client = await self._ensure_connected()

            event = {
                "job_id": job_id,
                "message": message,
                "level": level,
                "timestamp": datetime.now(UTC).isoformat(),
            }

            if metadata:
                event["metadata"] = metadata

            channel = f"job:{job_id}:logs"
            await redis_client.publish(channel, json.dumps(event))

        except Exception as e:
            # Don't let streaming errors break the main execution
            logger.error(f"Failed to publish log for job {job_id}: {e}")

    async def publish_status_change(
        self,
        job_id: str,
        status: str,
        result: Optional[Any] = None,
        error: Optional[str] = None
    ) -> None:
        """
        Publish a job status change event.

        Args:
            job_id: Job identifier
            status: New status (running, completed, failed)
            result: Job result (for completed jobs)
            error: Error message (for failed jobs)
        """
        try:
            redis_client = await self._ensure_connected()

            event = {
                "job_id": job_id,
                "status": status,
                "timestamp": datetime.now(UTC).isoformat(),
            }

            if result is not None:
                event["result"] = result
            if error is not None:
                event["error"] = error

            channel = f"job:{job_id}:status"
            await redis_client.publish(channel, json.dumps(event))
            logger.info(f"Published status change for job {job_id}: {status}")

        except Exception as e:
            logger.error(f"Failed to publish status change for job {job_id}: {e}")

    async def subscribe_logs(
        self,
        job_id: str,
        timeout: Optional[float] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Subscribe to log stream for a specific job.

        Args:
            job_id: Job identifier
            timeout: Optional timeout in seconds

        Yields:
            Dict containing log event data
        """
        redis_client = await self._ensure_connected()
        pubsub = redis_client.pubsub()

        try:
            # Subscribe to both logs and status channels
            log_channel = f"job:{job_id}:logs"
            status_channel = f"job:{job_id}:status"
            await pubsub.subscribe(log_channel, status_channel)
            logger.info(f"Subscribed to channels for job {job_id}")

            start_time = asyncio.get_event_loop().time() if timeout else None

            async for message in pubsub.listen():
                # Check timeout
                if timeout and start_time:
                    elapsed = asyncio.get_event_loop().time() - start_time
                    if elapsed > timeout:
                        logger.info(f"Subscription timeout for job {job_id}")
                        break

                if message["type"] == "message":
                    try:
                        event = json.loads(message["data"])
                        yield event

                        # Stop listening if job is completed or failed
                        if event.get("status") in ["completed", "failed"]:
                            logger.info(f"Job {job_id} finished with status: {event.get('status')}")
                            break

                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to decode message: {e}")

        except asyncio.CancelledError:
            logger.info(f"Subscription cancelled for job {job_id}")
        except Exception as e:
            logger.error(f"Error in subscription for job {job_id}: {e}")
        finally:
            await pubsub.unsubscribe()
            await pubsub.close()

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            await self._redis.connection_pool.disconnect()
            self._redis = None
            logger.info("Redis connection closed")

    async def __aenter__(self):
        """Async context manager entry."""
        await self._ensure_connected()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
