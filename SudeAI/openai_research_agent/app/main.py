import json
import os
import uuid
from html import escape
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
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
GOOGLE_CSE_API_KEY = os.getenv("GOOGLE_CSE_API_KEY")
GOOGLE_CSE_CX = os.getenv("GOOGLE_CSE_CX")

agent = LiveResearchAgent(
    AgentConfig(
        base_dir=BASE_DIR,
        gemini_api_key=GEMINI_API_KEY,
        gemini_model=GEMINI_MODEL,
        google_cse_api_key=GOOGLE_CSE_API_KEY,
        google_cse_cx=GOOGLE_CSE_CX,
        demo_mode=DEMO_MODE,
    )
)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=4000)
    session_id: str | None = None
    strict_sources: bool = False


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
    agent_panels: dict[str, str]
    source_snapshots: list[dict[str, str]]


class HistoryResponse(BaseModel):
    session_id: str
    messages: list[dict[str, str]]


class ReportCreateRequest(BaseModel):
    session_id: str
    question: str
    answer: str
    verification_notes: str
    confidence: str
    sources: list[dict[str, str]]
    history: list[dict[str, str]]


class ReportCreateResponse(BaseModel):
    report_id: str
    public_url: str


app = FastAPI(title="Live AI Assistant", version="2.6.0")
PUBLIC_REPORTS: dict[str, dict] = {}

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


def _build_response(question: str, session_id: str, strict_sources: bool = False) -> AskResponse:
    try:
        result = agent.run(question=question, session_id=session_id, strict_sources=strict_sources)
    except Exception as exc:
        message = str(exc)
        if "Gemini is not configured" in message:
            message = "Gemini is not configured. Set GEMINI_API_KEY (or enable DEMO_MODE=true)."
        raise HTTPException(status_code=502, detail=f"Assistant error: {message}")

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
        agent_panels=result["agent_panels"],
        source_snapshots=result.get("source_snapshots", []),
    )


@app.post("/ask", response_model=AskResponse)
async def ask(payload: AskRequest) -> AskResponse:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is required.")

    session_id = payload.session_id or f"session-{uuid.uuid4().hex[:12]}"
    return _build_response(question=question, session_id=session_id, strict_sources=payload.strict_sources)


@app.get("/ask_stream")
async def ask_stream(question: str, session_id: str | None = None, strict_sources: bool = False) -> StreamingResponse:
    q = (question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Question is required.")
    sid = session_id or f"session-{uuid.uuid4().hex[:12]}"

    def event_stream() -> Iterator[str]:
        yield "event: status\ndata: researching\n\n"
        try:
            response = _build_response(question=q, session_id=sid, strict_sources=strict_sources)
            payload = response.model_dump()
            answer = payload.get("answer", "")
            for i in range(0, len(answer), 56):
                chunk = answer[i : i + 56]
                yield f"event: chunk\ndata: {json.dumps({'text': chunk})}\n\n"
            yield f"event: final\ndata: {json.dumps(payload)}\n\n"
        except HTTPException as exc:
            detail = str(exc.detail)
            fallback = {
                "session_id": sid,
                "question": q,
                "answer": detail,
                "verification_notes": "Confidence: Low\nVerification Notes:\n- Request failed before model response.",
                "confidence": "Low",
                "sources": [],
                "verified_at_utc": "",
                "model": "error",
                "tool_trace": ["stream_error"],
                "memory_used": 0,
                "source_snapshots": [],
                "agent_panels": {
                    "retriever": "No retrieval completed due to request error.",
                    "analyst": detail,
                    "verifier": "Request failed before verification.",
                    "summarizer": detail,
                },
            }
            yield "event: status\ndata: error\n\n"
            yield f"event: final\ndata: {json.dumps(fallback)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/history/{session_id}", response_model=HistoryResponse)
async def history(session_id: str) -> HistoryResponse:
    messages = agent.memory.recent(session_id, limit=30)
    return HistoryResponse(session_id=session_id, messages=messages)


@app.post("/reports", response_model=ReportCreateResponse)
async def create_report(payload: ReportCreateRequest) -> ReportCreateResponse:
    report_id = f"rep-{uuid.uuid4().hex[:10]}"
    PUBLIC_REPORTS[report_id] = payload.model_dump()
    return ReportCreateResponse(report_id=report_id, public_url=f"/r/{report_id}")


@app.get("/r/{report_id}", response_class=HTMLResponse)
async def public_report(report_id: str) -> HTMLResponse:
    report = PUBLIC_REPORTS.get(report_id)
    if not report:
        return HTMLResponse(status_code=404, content="<h1>Report not found</h1>")

    history_html = ""
    for msg in report.get("history", []):
        role = escape(str(msg.get("role", "assistant")).upper())
        content = escape(str(msg.get("content", "")))
        history_html += f"<h3>{role}</h3><pre>{content}</pre>"

    sources_html = ""
    for src in report.get("sources", []):
        title = escape(str(src.get("title") or src.get("url") or "Source"))
        url = escape(str(src.get("url") or ""))
        sources_html += f'<li><a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a></li>'

    html = f"""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Live AI Assistant Report</title>
    <style>
      body {{ font-family: Manrope, Arial, sans-serif; max-width: 900px; margin: 24px auto; padding: 0 16px; }}
      pre {{ white-space: pre-wrap; background: #f5f7f7; border: 1px solid #dde4e3; border-radius: 10px; padding: 12px; }}
      a {{ color: #0f5fca; }}
    </style>
  </head>
  <body>
    <h1>Live AI Assistant Public Report</h1>
    <p><strong>Session:</strong> {escape(report.get("session_id", ""))}</p>
    <h2>Question</h2>
    <pre>{escape(report.get("question", ""))}</pre>
    <h2>Answer</h2>
    <pre>{escape(report.get("answer", ""))}</pre>
    <h2>Verification</h2>
    <p><strong>Confidence:</strong> {escape(report.get("confidence", ""))}</p>
    <pre>{escape(report.get("verification_notes", ""))}</pre>
    <h2>Sources</h2>
    <ul>{sources_html}</ul>
    <h2>Timeline</h2>
    {history_html}
  </body>
</html>
"""
    return HTMLResponse(content=html)
