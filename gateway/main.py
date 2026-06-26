import asyncio

from fastapi import FastAPI
import httpx
from database import init_db
from stream_logger import consume_dropped_requests
from middleware import RateLimitMiddleware
from routes import router

app = FastAPI()
app.include_router(router)

from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)


app.mount("/static", StaticFiles(directory="static"), name="static")


async def watchdog_consumer():
    while True:
        try:
            await consume_dropped_requests()
        except Exception as e:
            print(f"Consumer died, restarting: {e}")
            await asyncio.sleep(2)


@app.on_event("startup")
async def startup():
    await init_db()
    app.state.http_client = httpx.AsyncClient()
    asyncio.create_task(watchdog_consumer())


@app.on_event("shutdown")
async def shutdown():
    await app.state.http_client.aclose()


@app.get("/health")
async def health():
    return {"status": "ok"}
