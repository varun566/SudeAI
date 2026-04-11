import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request

from app.agent import AgentConfig, LiveResearchAgent

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent.parent

DEMO_MODE = os.getenv("DEMO_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

agent = LiveResearchAgent(
    AgentConfig(
        base_dir=BASE_DIR,
        gemini_api_key=GEMINI_API_KEY,
        gemini_model=GEMINI_MODEL,
        demo_mode=DEMO_MODE,
    )
)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=4000)
    session_id: str | None = None


class AskResponse(BaseModel):
    session_id: str
    question: str
    answer: str
    verification_notes: str
    confidence: str
    sources: list[dict[str, str]]
    verified_at_utc: str
    model: str
    tool_trace: list[str]
    memory_used: int


class HistoryResponse(BaseModel):
    session_id: str
    messages: list[dict[str, str]]


app = FastAPI(title="Live AI Assistant", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.post("/ask", response_model=AskResponse)
async def ask(payload: AskRequest) -> AskResponse:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is required.")

    session_id = payload.session_id or f"session-{uuid.uuid4().hex[:12]}"

    try:
        result = agent.run(question=question, session_id=session_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Assistant error: {str(exc)}")

    return AskResponse(
        session_id=session_id,
        question=question,
        answer=result["answer"],
        verification_notes=result["verification_notes"],
        confidence=result["confidence"],
        sources=result["sources"],
        verified_at_utc=result["verified_at_utc"],
        model=result["model"],
        tool_trace=result["tool_trace"],
        memory_used=result["memory_used"],
    )


@app.get("/history/{session_id}", response_model=HistoryResponse)
async def history(session_id: str) -> HistoryResponse:
    messages = agent.memory.recent(session_id, limit=30)
    return HistoryResponse(session_id=session_id, messages=messages)
