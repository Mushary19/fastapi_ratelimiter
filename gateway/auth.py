import redis.asyncio as aioredis
from fastapi import HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from models import ApiKey, Client
from config import settings
from database import get_db


from fastapi import Depends

# tells fastapi to extract X-API-Key header automatically
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# module level redis client, shared across requests
redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)


async def get_client_from_key(
    api_key: str = Security(api_key_header), db: AsyncSession = None
) -> dict:

    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    # 1. check redis cache first
    cache_key = f"apikey:{api_key}"
    cached = await redis_client.hgetall(cache_key)

    if cached:
        return {
            "client_id": int(cached["client_id"]),
            "tier": cached["tier"],
            "api_key": api_key,
        }

    # 2. cache miss — hit postgres
    result = await db.execute(
        select(ApiKey, Client)
        .join(Client, ApiKey.client_id == Client.id)
        .where(ApiKey.key == api_key)
        .where(ApiKey.is_active == True)
    )
    row = result.first()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    api_key_obj, client = row

    # 3. store in redis cache with 5 min TTL
    await redis_client.hset(
        cache_key, mapping={"client_id": client.id, "tier": api_key_obj.tier}
    )
    await redis_client.expire(cache_key, 300)  # 5 minutes

    return {"client_id": client.id, "tier": api_key_obj.tier, "api_key": api_key}


async def get_current_client(
    api_key: str = Security(api_key_header), db: AsyncSession = Depends(get_db)
) -> dict:
    return await get_client_from_key(api_key, db)
