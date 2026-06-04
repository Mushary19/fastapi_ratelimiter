import json
from datetime import datetime, timezone
import redis.asyncio as aioredis
from config import settings
import asyncio
from database import AsyncSessionLocal
from models import DroppedRequest

redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

STREAM_KEY = "dropped_requests_stream"


async def log_dropped_request(
    client_id: int | None, api_key: str | None, endpoint: str, method: str, reason: str
):
    payload = {
        "client_id": str(client_id) if client_id else "unknown",
        "api_key": api_key if api_key else "unknown",
        "endpoint": endpoint,
        "method": method,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await redis_client.xadd(STREAM_KEY, payload, maxlen=10000, approximate=True)


async def consume_dropped_requests():
    """
    Runs as a background task on startup.
    Reads from Redis Stream and persists to Postgres.
    """
    last_id = "$"  # only read new messages from this point
    print("Consumer started")

    while True:
        try:
            # block for 2 seconds waiting for new messages
            messages = await redis_client.xread(
                {STREAM_KEY: last_id},
                block=2000,
                count=10,  # process up to 10 at a time
            )

            if not messages:
                continue

            print(f"Got {len(messages)} messages")

            # messages = [("stream_key", [(id, {fields}), ...])]
            for stream_key, entries in messages:
                async with AsyncSessionLocal() as db:
                    for entry_id, fields in entries:
                        dropped = DroppedRequest(
                            client_id=(
                                int(fields["client_id"])
                                if fields["client_id"] != "unknown"
                                else None
                            ),
                            api_key=(
                                fields["api_key"]
                                if fields["api_key"] != "unknown"
                                else None
                            ),
                            endpoint=fields["endpoint"],
                            method=fields["method"],
                            reason=fields["reason"],
                            created_at=datetime.fromisoformat(fields["timestamp"]),
                        )
                        db.add(dropped)

                    await db.commit()
                    last_id = entries[-1][0]  # update cursor to last processed id

        except Exception as e:
            print(f"Stream consumer error: {e}")
            await asyncio.sleep(2)  # backoff before retrying
