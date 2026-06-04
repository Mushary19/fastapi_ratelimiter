import httpx
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from auth import get_current_client, api_key_header, redis_client
from rate_limiter import rate_limiter
from stream_logger import log_dropped_request
from config import settings

EXCLUDED_PATHS = [
    "/register",
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/dashboard/dropped",
    "/dashboard/stats",
]


class RateLimitMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):

        # skip auth and rate limiting for excluded paths
        if request.url.path in EXCLUDED_PATHS:
            return await call_next(request)

        # 1. extract api key from header
        api_key = await api_key_header(request)

        if not api_key:
            await log_dropped_request(
                client_id=None,
                api_key=None,
                endpoint=request.url.path,
                method=request.method,
                reason="missing_key",
            )
            return JSONResponse(status_code=401, content={"error": "Missing API key"})

        # 2. validate api key against redis cache
        cache_key = f"apikey:{api_key}"
        cached = await redis_client.hgetall(cache_key)

        if not cached:
            # fall back to postgres
            from database import AsyncSessionLocal
            from sqlalchemy import select
            from models import ApiKey, Client

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(ApiKey, Client)
                    .join(Client, ApiKey.client_id == Client.id)
                    .where(ApiKey.key == api_key)
                    .where(ApiKey.is_active == True)
                )
                row = result.first()

                if not row:
                    # genuinely invalid key
                    await log_dropped_request(
                        client_id=None,
                        api_key=api_key,
                        endpoint=request.url.path,
                        method=request.method,
                        reason="invalid_key",
                    )
                    return JSONResponse(
                        status_code=401,
                        content={"error": "Invalid or inactive API key"},
                    )

                api_key_obj, client = row
                client_id = client.id
                tier = api_key_obj.tier

                # repopulate cache
                await redis_client.hset(
                    cache_key, mapping={"client_id": client_id, "tier": tier}
                )
                await redis_client.expire(cache_key, 300)
        else:
            client_id = int(cached["client_id"])
            tier = cached["tier"]

        # 3. check rate limit
        allowed = await rate_limiter.is_allowed(client_id, tier)

        if not allowed:
            await log_dropped_request(
                client_id=client_id,
                api_key=api_key,
                endpoint=request.url.path,
                method=request.method,
                reason="rate_limited",
            )
            return JSONResponse(
                status_code=429,
                content={"error": "Rate limit exceeded", "retry_after": 60},
            )

        # 4. allowed — proxy to upstream
        client = request.app.state.http_client
        upstream_url = f"{settings.UPSTREAM_URL}{request.url.path}"

        upstream_response = await client.request(
            method=request.method,
            url=upstream_url,
            headers={k: v for k, v in request.headers.items() if k != "host"},
            content=await request.body(),
            params=request.query_params,
        )

        return JSONResponse(
            status_code=upstream_response.status_code, content=upstream_response.json()
        )
