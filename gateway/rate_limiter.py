import time
import redis.asyncio as aioredis
from config import settings

redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

# leaky bucket lua script
# runs atomically in redis — no race conditions
LEAKY_BUCKET_SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local leak_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

-- get current bucket state
local bucket = redis.call('HMGET', key, 'level', 'last_checked')
local level = tonumber(bucket[1]) or 0
local last_checked = tonumber(bucket[2]) or now

-- leak: remove tokens based on time passed
local elapsed = now - last_checked
local leaked = elapsed * leak_rate
level = math.max(0, level - leaked)

-- check if bucket is full
if level >= capacity then
    return 0  -- dropped
end

-- add one request to bucket
level = level + 1

-- save updated state
redis.call('HMSET', key, 'level', level, 'last_checked', now)
redis.call('EXPIRE', key, 60)

return 1  -- allowed
"""


class LeakyBucketRateLimiter:

    def __init__(self):
        self.script_sha = None

    async def _load_script(self):
        """load lua script into redis once, reuse sha after"""
        if not self.script_sha:
            self.script_sha = await redis_client.script_load(LEAKY_BUCKET_SCRIPT)

    async def is_allowed(self, client_id: int, tier: str) -> bool:
        await self._load_script()

        # get limits based on tier
        if tier == "pro":
            capacity = settings.RATE_LIMIT_PRO
            leak_rate = settings.RATE_LIMIT_PRO / 60  # requests per second
        else:
            capacity = settings.RATE_LIMIT_FREE
            leak_rate = settings.RATE_LIMIT_FREE / 60

        key = f"bucket:{client_id}"
        now = time.time()

        result = await redis_client.evalsha(
            self.script_sha,
            1,  # number of keys
            key,  # KEYS[1]
            capacity,  # ARGV[1]
            leak_rate,  # ARGV[2]
            now,  # ARGV[3]
        )

        return result == 1


rate_limiter = LeakyBucketRateLimiter()
