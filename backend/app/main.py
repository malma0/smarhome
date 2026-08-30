from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.agent import build_default_agent

app = FastAPI(title="Jarvis Backend")
agent = build_default_agent()


class ChatRequest(BaseModel):
    session_id: str
    resident_id: str
    message: str


class ChatResponse(BaseModel):
    response: str
    actions: list[dict[str, Any]] = []


class SettingsResponse(BaseModel):
    persona_mode: str
    proactivity_level: str


class SettingsUpdate(BaseModel):
    persona_mode: str | None = None
    proactivity_level: str | None = None


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    result = await agent.chat(req.session_id, req.resident_id, req.message)
    return ChatResponse(**result)


@app.post("/sessions/{session_id}/reset")
async def reset_session(session_id: str):
    agent.reset(session_id)
    return {"status": "reset", "session_id": session_id}


@app.get("/settings", response_model=SettingsResponse)
async def get_settings():
    return SettingsResponse(
        persona_mode=agent.memory.get_persona_mode(),
        proactivity_level=agent.memory.get_proactivity_level(),
    )


@app.put("/settings", response_model=SettingsResponse)
async def update_settings(update: SettingsUpdate):
    # Stand-in for the iPad settings screen (phase 8) - same underlying fields.
    try:
        if update.persona_mode is not None:
            agent.memory.set_persona_mode(update.persona_mode)
        if update.proactivity_level is not None:
            agent.memory.set_proactivity_level(update.proactivity_level)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SettingsResponse(
        persona_mode=agent.memory.get_persona_mode(),
        proactivity_level=agent.memory.get_proactivity_level(),
    )
