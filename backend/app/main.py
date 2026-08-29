from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from app.agent import agent

app = FastAPI(title="Jarvis Backend")


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    response: str
    actions: list[dict[str, Any]] = []


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    result = await agent.chat(req.session_id, req.message)
    return ChatResponse(**result)


@app.post("/sessions/{session_id}/reset")
async def reset_session(session_id: str):
    agent.reset(session_id)
    return {"status": "reset", "session_id": session_id}
