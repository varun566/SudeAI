import json
import os
import sqlite3
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Iterator

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
REPORT_WRITE_TOKEN = os.getenv("REPORT_WRITE_TOKEN", "").strip()
APP_API_KEY = os.getenv("APP_API_KEY", "").strip()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except Exception:
        return default


RATE_LIMIT_PER_MINUTE = _env_int("RATE_LIMIT_PER_MINUTE", 60)


class AppStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    report_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    is_public INTEGER NOT NULL DEFAULT 1,
                    session_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    verification_notes TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    history_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analytics_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    source_count INTEGER NOT NULL,
                    high_trust_count INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    strict_sources INTEGER NOT NULL
                )
                """
            )
            # Lightweight migration for existing DBs created before v2.8.
            columns = conn.execute("PRAGMA table_info(reports)").fetchall()
            col_names = {c[1] for c in columns}
            if "is_public" not in col_names:
                conn.execute("ALTER TABLE reports ADD COLUMN is_public INTEGER NOT NULL DEFAULT 1")
            conn.commit()

    def create_report(self, report_id: str, owner_id: str, payload: dict[str, Any], is_public: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reports(
                    report_id, owner_id, created_at, is_public, session_id, question, answer,
                    verification_notes, confidence, sources_json, history_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    owner_id,
                    now,
                    1 if is_public else 0,
                    payload.get("session_id", ""),
                    payload.get("question", ""),
                    payload.get("answer", ""),
                    payload.get("verification_notes", ""),
                    payload.get("confidence", ""),
                    json.dumps(payload.get("sources", [])),
                    json.dumps(payload.get("history", [])),
                ),
            )
            conn.commit()

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT report_id, owner_id, created_at, is_public, session_id, question, answer,
                       verification_notes, confidence, sources_json, history_json
                FROM reports
                WHERE report_id = ?
                """,
                (report_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "report_id": row[0],
            "owner_id": row[1],
            "created_at": row[2],
            "is_public": bool(row[3]),
            "session_id": row[4],
            "question": row[5],
            "answer": row[6],
            "verification_notes": row[7],
            "confidence": row[8],
            "sources": json.loads(row[9] or "[]"),
            "history": json.loads(row[10] or "[]"),
        }

    def list_reports(self, owner_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT report_id, created_at, question, is_public
                FROM reports
                WHERE owner_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (owner_id, limit),
            ).fetchall()
        return [{"report_id": r[0], "created_at": r[1], "question": r[2], "is_public": bool(r[3])} for r in rows]

    def update_report_visibility(self, report_id: str, owner_id: str, is_public: bool) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE reports SET is_public = ? WHERE report_id = ? AND owner_id = ?",
                (1 if is_public else 0, report_id, owner_id),
            )
            conn.commit()
        return cur.rowcount > 0

    def delete_report(self, report_id: str, owner_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM reports WHERE report_id = ? AND owner_id = ?",
                (report_id, owner_id),
            )
            conn.commit()
        return cur.rowcount > 0

    def log_analytics(self, event: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO analytics_events(
                    created_at, session_id, question, confidence, source_count,
                    high_trust_count, model, strict_sources
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    event.get("session_id", ""),
                    event.get("question", ""),
                    event.get("confidence", ""),
                    int(event.get("source_count", 0)),
                    int(event.get("high_trust_count", 0)),
                    event.get("model", "unknown"),
                    1 if event.get("strict_sources") else 0,
                ),
            )
            conn.commit()

    def analytics_summary(self, days: int = 7) -> dict[str, Any]:
        days = max(1, min(days, 30))
        with self._connect() as conn:
            totals = conn.execute(
                """
                SELECT COUNT(*),
                       AVG(source_count),
                       AVG(high_trust_count)
                FROM analytics_events
                WHERE datetime(created_at) >= datetime('now', ?)
                """,
                (f"-{days} days",),
            ).fetchone()
            daily = conn.execute(
                """
                SELECT substr(created_at, 1, 10) AS day,
                       COUNT(*) AS runs,
                       AVG(source_count) AS avg_sources,
                       AVG(high_trust_count) AS avg_high_trust
                FROM analytics_events
                WHERE datetime(created_at) >= datetime('now', ?)
                GROUP BY day
                ORDER BY day DESC
                """,
                (f"-{days} days",),
            ).fetchall()
        return {
            "window_days": days,
            "total_runs": int((totals[0] or 0) if totals else 0),
            "avg_sources": float((totals[1] or 0.0) if totals else 0.0),
            "avg_high_trust_sources": float((totals[2] or 0.0) if totals else 0.0),
            "daily": [
                {
                    "day": r[0],
                    "runs": int(r[1] or 0),
                    "avg_sources": float(r[2] or 0.0),
                    "avg_high_trust_sources": float(r[3] or 0.0),
                }
                for r in daily
            ],
        }


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
store = AppStore(BASE_DIR / "data" / "app_state.db")


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
    owner_id: str = Field(min_length=4, max_length=100)
    access_token: str | None = None
    session_id: str
    question: str
    answer: str
    verification_notes: str
    confidence: str
    is_public: bool = True
    sources: list[dict[str, str]]
    history: list[dict[str, str]]


class ReportCreateResponse(BaseModel):
    report_id: str
    public_url: str


class ReportListResponse(BaseModel):
    owner_id: str
    reports: list[dict[str, Any]]


class ReportVisibilityUpdateRequest(BaseModel):
    owner_id: str = Field(min_length=4, max_length=100)
    access_token: str | None = None
    is_public: bool


class ReportDeleteRequest(BaseModel):
    owner_id: str = Field(min_length=4, max_length=100)
    access_token: str | None = None


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    utc: str


app = FastAPI(title="Live AI Assistant", version="4.0.0")
REQUEST_LOG: dict[str, deque[float]] = defaultdict(deque)

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

    response = AskResponse(
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
    high_trust_count = sum(1 for s in response.sources if s.get("trust_tier") == "high")
    store.log_analytics(
        {
            "session_id": session_id,
            "question": question,
            "confidence": response.confidence,
            "source_count": len(response.sources),
            "high_trust_count": high_trust_count,
            "model": response.model,
            "strict_sources": strict_sources,
        }
    )
    return response


def _fallback_stream_payload(session_id: str, question: str, detail: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "question": question,
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


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "").strip()
    if xff:
        return xff.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _enforce_rate_limit(request: Request, scope: str) -> None:
    if RATE_LIMIT_PER_MINUTE <= 0:
        return
    now_ts = datetime.now(timezone.utc).timestamp()
    key = f"{_client_ip(request)}:{scope}"
    window = REQUEST_LOG[key]
    while window and (now_ts - window[0]) > 60:
        window.popleft()
    if len(window) >= RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again in a minute.")
    window.append(now_ts)


def _enforce_app_api_key(request: Request) -> None:
    if not APP_API_KEY:
        return
    supplied = request.headers.get("x-api-key") or request.query_params.get("api_key") or ""
    if supplied != APP_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key.")


def _enforce_protection(request: Request, scope: str) -> None:
    _enforce_app_api_key(request)
    _enforce_rate_limit(request, scope)


@app.post("/ask", response_model=AskResponse)
async def ask(payload: AskRequest, request: Request) -> AskResponse:
    _enforce_protection(request, "ask")
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is required.")

    session_id = payload.session_id or f"session-{uuid.uuid4().hex[:12]}"
    return _build_response(question=question, session_id=session_id, strict_sources=payload.strict_sources)


@app.get("/ask_stream")
async def ask_stream(
    request: Request,
    question: str,
    session_id: str | None = None,
    strict_sources: bool = False,
    api_key: str | None = None,
) -> StreamingResponse:
    _enforce_protection(request, "ask_stream")
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
            detail = str(exc.detail or "Request failed.")
            fallback = _fallback_stream_payload(session_id=sid, question=q, detail=detail)
            yield "event: status\ndata: error\n\n"
            yield f"event: final\ndata: {json.dumps(fallback)}\n\n"
        except Exception as exc:
            detail = f"Unexpected stream error: {str(exc)}"
            fallback = _fallback_stream_payload(session_id=sid, question=q, detail=detail)
            yield "event: status\ndata: error\n\n"
            yield f"event: final\ndata: {json.dumps(fallback)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/history/{session_id}", response_model=HistoryResponse)
async def history(session_id: str, request: Request) -> HistoryResponse:
    _enforce_protection(request, "history")
    messages = agent.memory.recent(session_id, limit=30)
    return HistoryResponse(session_id=session_id, messages=messages)


@app.post("/reports", response_model=ReportCreateResponse)
async def create_report(payload: ReportCreateRequest, request: Request) -> ReportCreateResponse:
    _enforce_protection(request, "reports_create")
    if REPORT_WRITE_TOKEN and (payload.access_token or "") != REPORT_WRITE_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid report access token")
    report_id = f"rep-{uuid.uuid4().hex[:10]}"
    store.create_report(
        report_id=report_id,
        owner_id=payload.owner_id,
        payload=payload.model_dump(),
        is_public=payload.is_public,
    )
    return ReportCreateResponse(report_id=report_id, public_url=f"/r/{report_id}")


@app.get("/reports/my/{owner_id}", response_model=ReportListResponse)
async def list_my_reports(owner_id: str, request: Request) -> ReportListResponse:
    _enforce_protection(request, "reports_list")
    return ReportListResponse(owner_id=owner_id, reports=store.list_reports(owner_id))


@app.patch("/reports/{report_id}/visibility")
async def update_report_visibility(
    report_id: str,
    payload: ReportVisibilityUpdateRequest,
    request: Request,
) -> dict[str, Any]:
    _enforce_protection(request, "reports_visibility")
    if REPORT_WRITE_TOKEN and (payload.access_token or "") != REPORT_WRITE_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid report access token")
    ok = store.update_report_visibility(report_id=report_id, owner_id=payload.owner_id, is_public=payload.is_public)
    if not ok:
        raise HTTPException(status_code=404, detail="Report not found for this owner")
    return {"ok": True, "report_id": report_id, "is_public": payload.is_public}


@app.delete("/reports/{report_id}")
async def delete_report(report_id: str, payload: ReportDeleteRequest, request: Request) -> dict[str, Any]:
    _enforce_protection(request, "reports_delete")
    if REPORT_WRITE_TOKEN and (payload.access_token or "") != REPORT_WRITE_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid report access token")
    ok = store.delete_report(report_id=report_id, owner_id=payload.owner_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Report not found for this owner")
    return {"ok": True, "report_id": report_id}


@app.get("/analytics/summary")
async def analytics_summary(request: Request, days: int = 7) -> dict[str, Any]:
    _enforce_protection(request, "analytics")
    return store.analytics_summary(days=days)


@app.get("/r/{report_id}", response_class=HTMLResponse)
async def public_report(report_id: str) -> HTMLResponse:
    report = store.get_report(report_id)
    if not report:
        return HTMLResponse(status_code=404, content="<h1>Report not found</h1>")
    if not report.get("is_public", True):
        return HTMLResponse(status_code=403, content="<h1>This report is private</h1>")

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
    <p><strong>Report ID:</strong> {escape(report.get("report_id", ""))}</p>
    <p><strong>Created (UTC):</strong> {escape(report.get("created_at", ""))}</p>
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


@app.get("/dashboard/{owner_id}", response_class=HTMLResponse)
async def owner_dashboard(owner_id: str, request: Request) -> HTMLResponse:
    _enforce_protection(request, "dashboard")
    reports = store.list_reports(owner_id, limit=100)
    rows = ""
    for r in reports:
        vis = "Public" if r.get("is_public") else "Private"
        rid = escape(str(r.get("report_id", "")))
        q = escape(str(r.get("question", "")))
        created = escape(str(r.get("created_at", "")))
        rows += (
            f"<tr><td>{rid}</td><td>{created}</td><td>{vis}</td><td>{q}</td>"
            f"<td><a href='/r/{rid}' target='_blank'>Open</a></td></tr>"
        )
    html = f"""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Owner Dashboard</title>
    <style>
      body {{ font-family: Manrope, Arial, sans-serif; max-width: 1100px; margin: 24px auto; padding: 0 16px; }}
      table {{ width: 100%; border-collapse: collapse; }}
      th, td {{ border: 1px solid #d9e1df; padding: 8px; text-align: left; vertical-align: top; }}
      th {{ background: #f4f8f7; }}
    </style>
  </head>
  <body>
    <h1>Owner Dashboard</h1>
    <p><strong>Owner ID:</strong> {escape(owner_id)}</p>
    <p><a href="/analytics/summary" target="_blank">Open Analytics Summary</a></p>
    <table>
      <thead><tr><th>Report ID</th><th>Created (UTC)</th><th>Visibility</th><th>Question</th><th>Link</th></tr></thead>
      <tbody>{rows or '<tr><td colspan=\"5\">No reports yet.</td></tr>'}</tbody>
    </table>
  </body>
</html>
"""
    return HTMLResponse(content=html)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="live-ai-assistant",
        version=app.version,
        utc=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/version")
async def version() -> dict[str, str]:
    return {"version": app.version}
