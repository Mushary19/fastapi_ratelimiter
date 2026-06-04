from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


# --- Schemas ---


class ProcessRequest(BaseModel):
    data: str


# --- Routes ---


@app.get("/health")
async def health():
    return {"status": "upstream ok"}


@app.get("/data")
async def get_data():
    return {
        "message": "here is your data",
        "items": [
            {"id": 1, "name": "item one"},
            {"id": 2, "name": "item two"},
            {"id": 3, "name": "item three"},
        ],
    }


@app.post("/process")
async def process(payload: ProcessRequest):
    return {
        "message": "processed successfully",
        "received": payload.data,
        "result": f"processed → {payload.data.upper()}",
    }
