#!/usr/bin/env python3
"""
Database initialization and migration script for job persistence.
Run this after setting up Redis to initialize the database tables.
"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Attempt to load .env from project root using python-dotenv (optional)
try:
    from dotenv import load_dotenv
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(project_root, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
    else:
        # Fallback to default loader (looks in CWD and parent dirs)
        load_dotenv()
except Exception:
    # If python-dotenv isn't installed or loading fails, continue —
    # the script will rely on system environment variables.
    pass


async def init_database():
    """Initialize database tables for job persistence."""
    try:
        from database import DatabaseManager
        from models.job import JobModel, JobLogModel

        print("🔄 Initializing database...")
        db_manager = DatabaseManager()

        # Initialize database tables
        await db_manager.init_db(drop_existing=False)

        print("✅ Database tables created successfully!")
        print("   - jobs")
        print("   - job_logs")

        # Test connection
        async with db_manager.get_session() as session:
            print("\n✅ Database connection test passed!")

        return True

    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("   Make sure all dependencies are installed: uv sync")
        return False
    except Exception as e:
        print(f"❌ Error initializing database: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_redis():
    """Test Redis connection."""
    try:
        from core.wizelit_agent_wrapper.streaming import LogStreamer

        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        print(f"\n🔄 Testing Redis connection at {redis_url}...")

        streamer = LogStreamer(redis_url)
        await streamer._ensure_connected()

        # Test pub/sub
        test_job_id = "TEST-12345678"
        await streamer.publish_log(test_job_id, "Test message", "INFO")

        await streamer.close()

        print("✅ Redis connection test passed!")
        return True

    except ImportError:
        print("⚠️  Redis package not installed")
        print("   Install with: uv pip install redis")
        return False
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        print("   Make sure Redis is running: docker-compose up -d redis")
        return False


async def verify_environment():
    """Verify environment configuration."""
    print("\n📋 Environment Configuration:")

    required_vars = [
        "POSTGRES_HOST",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "POSTGRES_PORT",
    ]

    optional_vars = [
        "REDIS_URL",
        "ENABLE_LOG_STREAMING",
        "LOG_STREAM_TIMEOUT_SECONDS",
        "JOB_LOG_TAIL",
    ]

    all_ok = True

    for var in required_vars:
        value = os.getenv(var)
        if value:
            # Mask sensitive values
            if "PASSWORD" in var:
                display = "***" + value[-3:] if len(value) > 3 else "***"
            else:
                display = value
            print(f"   ✅ {var}={display}")
        else:
            print(f"   ❌ {var} not set")
            all_ok = False

    print("\n   Optional variables:")
    for var in optional_vars:
        value = os.getenv(var, "not set")
        print(f"      {var}={value}")

    return all_ok


async def main():
    """Main entry point."""
    print("=" * 60)
    print("🚀 Wizelit Job Persistence Setup")
    print("=" * 60)

    # Step 1: Verify environment
    env_ok = await verify_environment()
    if not env_ok:
        print("\n❌ Environment configuration incomplete")
        print("   Copy .env.template to .env and configure all required variables")
        return 1

    # Step 2: Test Redis
    redis_ok = await test_redis()

    # Step 3: Initialize database
    db_ok = await init_database()

    # Summary
    print("\n" + "=" * 60)
    print("📊 Setup Summary:")
    print("=" * 60)
    print(f"   Environment:  {'✅ OK' if env_ok else '❌ Failed'}")
    print(f"   Redis:        {'✅ OK' if redis_ok else '⚠️  Failed (optional)'}")
    print(f"   Database:     {'✅ OK' if db_ok else '❌ Failed'}")

    if db_ok:
        print("\n✅ Setup complete! You can now:")
        print("   1. Start the refactoring agent: python mcp_servers/refactoring-agent/main.py")
        print("   2. Start the hub: chainlit run main.py")
        print("   3. Submit a refactoring job and watch real-time logs!")

        if not redis_ok:
            print("\n⚠️  Redis is not available. The system will work but will fall back to polling.")
            print("   To enable real-time streaming:")
            print("   1. Start Redis: docker-compose up -d redis")
            print("   2. Set ENABLE_LOG_STREAMING=true in .env")

        return 0
    else:
        print("\n❌ Setup failed. Please fix the errors above and try again.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
