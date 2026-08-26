import uuid
from dotenv import load_dotenv

load_dotenv()

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

from . import agent
from . import actions as actions_store
from .session import get_or_create

app = FastAPI(title="Trendly Support Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://yellow-ai-fde-role-assignment.vercel.app", "http://127.0.0.1:5500"],
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None

    @field_validator("message")
    @classmethod
    def message_not_too_long(cls, v: str) -> str:
        if len(v) > 2000:
            raise ValueError("Message too long (max 2000 characters).")
        if not v.strip():
            raise ValueError("Message cannot be empty.")
        return v

class ChatResponse(BaseModel):
    reply: str
    session_id: str


@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/")
def root():
    return {
        "service": "Trendly Support Assistant API",
        "docs": "/docs",
        "health": "/health",
        "chat": "POST /chat",
    }

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())
    session = get_or_create(session_id)
    reply = agent.handle_message(session, req.message)
    return ChatResponse(reply=reply, session_id=session_id)

@app.get("/debug/escalations")
def debug_escalations(key: str | None = None):
    """Lets a grader/demo viewer see what got escalated, without exposing this in the chat itself.

    Gated behind DEBUG_KEY (set in .env / deployment env vars) since this
    surfaces customer PII (names, emails, order contents) with no other auth
    layer. Not meant as real security - just enough to stop this being wide
    open to anyone who finds the URL on a public deployment.
    """
    expected = os.environ.get("DEBUG_KEY")
    if not expected or key != expected:
        raise HTTPException(status_code=404, detail="Not found")
    return {"tickets": actions_store._TICKETS}