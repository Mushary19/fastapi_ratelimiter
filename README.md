# API Rate Limiter Gateway

A production-grade API gateway with leaky bucket rate limiting, built with FastAPI, Redis, and PostgreSQL. Every dropped request is logged to a Redis Stream, consumed asynchronously, and persisted to PostgreSQL for observability.

---

## Architecture

```
Client Request
    │
    ▼
┌─────────────────────────────┐
│        FastAPI Gateway       │  :8000
│                             │
│  1. Extract X-API-Key       │
│  2. Validate (Redis → PG)   │
│  3. Leaky Bucket Check      │──── DROP ──→ Redis Stream
│  4. Proxy to Upstream       │                   │
└─────────────────────────────┘                   │
    │                                    Stream Consumer
    ▼                                    (background task)
┌─────────────────────────────┐                   │
│      Upstream Service        │  :8001            ▼
│  GET  /data                 │          ┌─────────────────┐
│  POST /process              │          │   PostgreSQL     │
└─────────────────────────────┘          │ dropped_requests│
                                         └─────────────────┘
```

---

## How It Works

### Leaky Bucket Rate Limiting

The leaky bucket algorithm smooths traffic by draining requests at a fixed rate regardless of burst patterns. State is stored in Redis and updated atomically via a Lua script — eliminating race conditions entirely.

```
bucket has a level (requests currently sitting in it)
requests leak out at a fixed rate per second

on each incoming request:
  → calculate leaked amount since last check
  → subtract from current level
  → if level < capacity → allow, increment level
  → if level >= capacity → drop, return 429
```

Why Lua? Redis executes Lua scripts atomically. Without it, two concurrent requests could both read the same bucket level and both get allowed — a classic race condition. The Lua script runs as a single atomic operation with no interruption possible.

### API Key Authentication

Every client registers once and receives an API key. On each request:

1. Key extracted from `X-API-Key` header
2. Redis cache checked first (`HGETALL apikey:{key}`)
3. On cache miss → PostgreSQL lookup, then repopulate cache with 5 min TTL
4. Client ID and tier retrieved, rate limit applied per client

This means each client gets their own isolated bucket — one abusive client never affects others.

### Dropped Request Logging

When a request is dropped, the gateway writes to a Redis Stream (`XADD`) synchronously — microseconds, no DB latency added to the response. A background consumer (`XREAD block=2000`) reads the stream in batches and persists to PostgreSQL asynchronously. The client gets their 429 instantly regardless of DB write speed.

---

## Stack

| Layer            | Technology                    |
| ---------------- | ----------------------------- |
| Gateway          | FastAPI + Uvicorn             |
| Rate Limit State | Redis (Lua atomic scripts)    |
| Auth Cache       | Redis Hash (HSET/HGETALL)     |
| Drop Log Queue   | Redis Streams (XADD/XREAD)    |
| Persistence      | PostgreSQL + SQLAlchemy async |
| Upstream         | FastAPI (dummy service)       |
| Infrastructure   | Docker Compose                |

---

## Project Structure

```
fastapi_ratelimiter/
├── gateway/
│   ├── main.py            # FastAPI app, lifespan, middleware registration
│   ├── middleware.py      # Request interceptor — auth, rate limit, proxy
│   ├── rate_limiter.py    # Leaky bucket — Lua script, Redis atomicity
│   ├── auth.py            # API key validation, Redis cache + PG fallback
│   ├── stream_logger.py   # Producer (xadd) + Consumer (xread → PG)
│   ├── routes.py          # Registration, dashboard endpoints
│   ├── models.py          # SQLAlchemy models
│   ├── database.py        # Async engine, session, init_db
│   ├── config.py          # Pydantic settings
│   ├── static/
│   │   └── dashboard.html # Observability dashboard
│   ├── Dockerfile
│   └── requirements.txt
├── upstream/
│   ├── main.py            # Dummy API endpoints
│   ├── Dockerfile
│   └── requirements.txt
├── docker-compose.yml
└── .env
```

---

## Getting Started

**Prerequisites:** Docker and Docker Compose installed.

**1. Clone and configure:**

```bash
git clone https://github.com/Mushary19/fastapi-ratelimiter
cd fastapi_ratelimiter
```

Create `.env` in the root:

```env
POSTGRES_USER=admin
POSTGRES_PASSWORD=secret
POSTGRES_DB=ratelimiter
DATABASE_URL=postgresql+asyncpg://admin:secret@postgres:5432/ratelimiter
REDIS_URL=redis://redis:6379/0
UPSTREAM_URL=http://upstream:8001
```

**2. Start all services:**

```bash
docker compose up --build
```

Four services start: `gateway:8000`, `upstream:8001`, `redis:6379`, `postgres:5432`.

**3. Register a client:**

```bash
curl -X POST http://localhost:8000/register \
  -H "Content-Type: application/json" \
  -d '{"name": "your name", "email": "you@example.com"}'
```

Response:

```json
{
  "client_id": 1,
  "name": "your name",
  "email": "you@example.com",
  "api_key": "sk_xxxxxxxxxxxxxxxxxxxxx",
  "tier": "free"
}
```

Save your `api_key`.

**4. Hit a rate-limited endpoint:**

```bash
curl http://localhost:8000/data \
  -H "X-API-Key: your_api_key"
```

**5. Trigger rate limiting:**

```bash
for i in {1..25}; do
  curl -s -o /dev/null -w "%{http_code}\n" \
  http://localhost:8000/data \
  -H "X-API-Key: your_api_key"
done
```

You'll see `200` responses until the bucket fills, then `429 Too Many Requests`.

**6. Check dropped requests:**

```bash
curl http://localhost:8000/dashboard/dropped
curl http://localhost:8000/dashboard/stats
```

**7. Open the dashboard:**

Visit `http://localhost:8000/static/dashboard.html`, paste your API key, and hit ⚡ Hammer.

---

## Rate Limit Tiers

| Tier | Limit      | Leak Rate     |
| ---- | ---------- | ------------- |
| free | 10 req/min | 0.166 req/sec |
| pro  | 60 req/min | 1 req/sec     |

Configurable via `.env`:

```env
RATE_LIMIT_FREE=10
RATE_LIMIT_PRO=60
```

---

## API Endpoints

| Method | Endpoint             | Auth | Description                      |
| ------ | -------------------- | ---- | -------------------------------- |
| POST   | `/register`          | No   | Register client, receive API key |
| GET    | `/data`              | Yes  | Dummy endpoint (rate limited)    |
| POST   | `/process`           | Yes  | Dummy endpoint (rate limited)    |
| GET    | `/dashboard/dropped` | No   | List dropped requests            |
| GET    | `/dashboard/stats`   | No   | Drop counts by reason            |
| GET    | `/health`            | No   | Gateway health check             |

---

## Failure Modes & Design Decisions

**Race condition prevention**
Redis Lua scripts execute atomically. Two concurrent requests checking the same bucket cannot both pass — the script reads, calculates, and writes in a single uninterruptible operation.

**Redis script invalidation on restart**
If Redis restarts, cached Lua script SHAs become invalid. The rate limiter catches `NoScriptError`, reloads the script, and retries transparently.

**Stream consumer crash recovery**
The consumer runs inside a watchdog loop. If any exception escapes — Redis disconnect, Postgres down, serialization error — the watchdog catches it, logs it, waits 2 seconds, and restarts the consumer automatically.

**Cache miss on valid keys**
API key cache has a 5-minute TTL. On expiry, middleware falls back to PostgreSQL, revalidates, and repopulates the cache. A valid client is never incorrectly rejected due to cache expiry.

**Dropped request logging latency**
`xadd` to Redis Stream is non-blocking and takes microseconds. The 429 response is returned immediately — DB persistence happens asynchronously via the stream consumer. Client latency is never affected by PostgreSQL write speed.

---

## What I'd Add in Production

- **Redis Consumer Groups** — track consumer progress with ACKs so no messages are lost on restart (current implementation skips messages added before startup)
- **Alembic migrations** — replace `init_db` with versioned schema migrations
- **Per-endpoint rate limits** — different buckets for `/data` vs `/process`
- **Key deactivation propagation** — currently takes up to 5 min (cache TTL) to take effect
- **Prometheus metrics** — expose request counts, drop rates, bucket levels as metrics
- **Rate limit headers** — return `X-RateLimit-Remaining` and `Retry-After` on every response

---

## License

MIT
