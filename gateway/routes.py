import secrets
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select, desc
from models import Client, ApiKey, DroppedRequest

from pydantic import BaseModel, ConfigDict
from database import get_db
from models import Client, ApiKey
from datetime import datetime

router = APIRouter()


class RegisterRequest(BaseModel):
    name: str
    email: str


class RegisterResponse(BaseModel):
    client_id: int
    name: str
    email: str
    api_key: str
    tier: str


class DroppedRequestResponse(BaseModel):
    id: int
    client_id: int
    api_key: str
    endpoint: str
    method: str
    reason: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DroppedRequestsListResponse(BaseModel):
    total: int
    requests: list[DroppedRequestResponse]


@router.post("/register", response_model=RegisterResponse)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    # check if email already registered
    result = await db.execute(select(Client).where(Client.email == payload.email))
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # create client
    client = Client(name=payload.name, email=payload.email)
    db.add(client)
    await db.flush()  # get client.id without committing yet

    # generate api key
    raw_key = secrets.token_urlsafe(32)

    api_key = ApiKey(key=raw_key, tier="free", client_id=client.id)
    db.add(api_key)
    await db.commit()
    await db.refresh(client)

    return RegisterResponse(
        client_id=client.id,
        name=client.name,
        email=client.email,
        api_key=raw_key,
        tier=api_key.tier,
    )


@router.get("/dashboard/dropped", response_model=DroppedRequestsListResponse)
async def get_dropped_requests(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(DroppedRequest).order_by(desc(DroppedRequest.created_at)).limit(limit)
    )
    dropped = result.scalars().all()

    return {"total": len(dropped), "requests": dropped}


@router.get("/dashboard/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import func

    result = await db.execute(
        select(
            DroppedRequest.reason,
            func.count(DroppedRequest.id).label("count"),
        ).group_by(DroppedRequest.reason)
    )
    rows = result.all()

    return {"by_reason": {row.reason: row.count for row in rows}}
