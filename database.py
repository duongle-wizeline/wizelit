from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
    AsyncEngine
)
from contextlib import asynccontextmanager
from typing import AsyncGenerator
import logging
import os
import threading

from models import BaseModel

logger = logging.getLogger(__name__)

class DatabaseManager:
    """Singleton database manager with connection pooling."""

    DATABASE_URL: str = ""
    POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "5"))
    MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    POOL_TIMEOUT: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))
    POOL_RECYCLE: int = int(os.getenv("DB_POOL_RECYCLE", "3600"))
    ECHO_SQL: bool = os.getenv("DB_ECHO_SQL", "false").lower() == "true"

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                # Double-check pattern to ensure thread safety
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        USERNAME = os.getenv("POSTGRES_USER")
        PASSWORD = os.getenv("POSTGRES_PASSWORD")
        HOST = os.getenv("POSTGRES_HOST")
        PORT = os.getenv("POSTGRES_PORT")
        DATABASE = os.getenv("POSTGRES_DB")
        self.DATABASE_URL = f"postgresql+asyncpg://{USERNAME}:{PASSWORD}@{HOST}:{PORT}/{DATABASE}"

        # Create async engine
        self.engine: AsyncEngine = create_async_engine(
            self.DATABASE_URL,
            pool_size=self.POOL_SIZE,
            max_overflow=self.MAX_OVERFLOW,
            pool_timeout=self.POOL_TIMEOUT,
            pool_recycle=self.POOL_RECYCLE,
            echo=self.ECHO_SQL,
            pool_pre_ping=True,
        )

        # Create async session factory
        self.async_session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )

        self._initialized = True
        logger.info("Database manager initialized")

    async def init_db(self, drop_existing: bool = False):
        """
        Initialize database tables.

        Args:
            drop_existing: If True, drops all existing tables before creating
        """
        try:
            async with self.engine.begin() as conn:
                if drop_existing:
                    logger.warning("Dropping all existing tables")
                    await conn.run_sync(BaseModel.metadata.drop_all)

                await conn.run_sync(BaseModel.metadata.create_all)

            logger.info("Database tables initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Async context manager for database sessions.

        Usage:
            async with db_manager.get_session() as session:
                user = await session.get(User, user_id)
                await session.commit()
        """
        session = self.async_session_factory()
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Session error: {e}")
            raise
        finally:
            await session.close()

    async def get_db(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Dependency injection for FastAPI/similar frameworks.

        Usage:
            @app.get("/users")
            async def get_users(db: AsyncSession = Depends(db_manager.get_db)):
                result = await db.execute(select(User))
                return result.scalars().all()
        """
        session = self.async_session_factory()
        try:
            yield session
        finally:
            await session.close()

    async def close(self):
        """Dispose of the engine and close all connections."""
        await self.engine.dispose()
        logger.info("Database connections closed")

    async def health_check(self) -> bool:
        """Check if database connection is healthy."""
        try:
            async with self.get_session() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
