#!/usr/bin/env python3
"""
Python-based Redis monitor that listens for job messages
More reliable than shell script for debugging
"""
import asyncio
import json
import sys
from datetime import datetime

async def monitor_redis(job_pattern="job:*"):
    """Monitor Redis Pub/Sub messages."""
    try:
        import redis.asyncio as redis

        print("=" * 70)
        print("🔍 Wizelit Redis Monitor (Python)")
        print("=" * 70)
        print(f"Pattern: {job_pattern}")
        print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("\n📡 Listening for messages... (Ctrl+C to stop)\n")

        # Connect to Redis
        client = redis.from_url("redis://localhost:6379", decode_responses=True)
        await client.ping()
        print("✅ Connected to Redis\n")

        # Subscribe to pattern
        pubsub = client.pubsub()
        await pubsub.psubscribe(job_pattern + ":logs", job_pattern + ":status")
        print(f"✅ Subscribed to: {job_pattern}:logs, {job_pattern}:status\n")
        print("-" * 70)

        # Listen for messages
        message_count = 0
        async for message in pubsub.listen():
            if message['type'] in ['pmessage', 'message']:
                message_count += 1
                channel = message.get('channel', 'unknown')
                data = message.get('data', '')

                # Parse JSON if possible
                try:
                    parsed = json.loads(data)

                    if ':logs' in channel:
                        # Log message
                        ts = parsed.get('timestamp', '')[:19]
                        level = parsed.get('level', 'INFO')
                        msg = parsed.get('message', '')
                        job_id = parsed.get('job_id', 'unknown')

                        print(f"[{message_count:04d}] 📝 {ts} [{level:5s}] {job_id}")
                        print(f"       {msg}")

                    elif ':status' in channel:
                        # Status change
                        ts = parsed.get('timestamp', '')[:19]
                        status = parsed.get('status', 'unknown')
                        job_id = parsed.get('job_id', 'unknown')

                        print(f"[{message_count:04d}] 🔄 {ts} STATUS: {status.upper()} ({job_id})")

                    print()

                except json.JSONDecodeError:
                    # Raw message
                    print(f"[{message_count:04d}] 📨 {channel}")
                    print(f"       {data[:200]}")
                    print()

            elif message['type'] == 'psubscribe':
                print(f"✅ Confirmed subscription: {message['pattern']}")

    except KeyboardInterrupt:
        print("\n\n⏹️  Stopped by user")
        print(f"📊 Total messages received: {message_count}")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            await pubsub.unsubscribe()
            await client.aclose()
        except:
            pass

if __name__ == "__main__":
    pattern = sys.argv[1] if len(sys.argv) > 1 else "job:*"
    asyncio.run(monitor_redis(pattern))
