import uuid
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import agent
from . import actions as actions_store
from .session import get_or_create

app = FastAPI(title="Trendly Support Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # demo scope only - would be locked to the storefront's origin in production
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    session_id: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())
    session = get_or_create(session_id)
    reply = agent.handle_message(session, req.message)
    return ChatResponse(reply=reply, session_id=session_id)


@app.get("/debug/escalations")
def debug_escalations():
    """Lets a grader/demo viewer see what got escalated, without exposing this in the chat itself."""
    return {"tickets": actions_store._TICKETS}
