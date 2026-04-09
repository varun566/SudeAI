import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request

from openai import OpenAI, APIError, RateLimitError
from duckduckgo_search import DDGS

try:
    import google.generativeai as genai
except Exception:  # pragma: no cover - optional provider dependency
    genai = None

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent.parent

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
DEMO_MODE = os.getenv("DEMO_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
PROVIDER = os.getenv("PROVIDER", "openai").strip().lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

if not OPENAI_API_KEY:
    # Keep app bootable for UI preview, but fail when /ask is called.
    client = None
else:
    client = OpenAI(api_key=OPENAI_API_KEY)

if genai is not None and GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=4000)


class AskResponse(BaseModel):
    question: str
    answer: str
    verification_notes: str
    confidence: str
    sources: list[dict[str, str]]
    verified_at_utc: str
    model: str


def _demo_answer(question: str) -> tuple[str, str, str, list[dict[str, str]]]:
    answer = (
        f"Demo mode response for: {question}\n\n"
        "This educational demo simulates a live research workflow:\n"
        "- gathers multiple web-style sources\n"
        "- drafts a concise answer\n"
        "- runs a second verification pass\n"
        "- returns confidence and citations\n\n"
        "To switch from demo to real-time internet-backed answers, set DEMO_MODE=false and "
        "configure a funded OPENAI_API_KEY."
    )
    verification_notes = (
        "Confidence: Medium\n"
        "Verification Notes:\n"
        "- This is a simulated result generated in DEMO_MODE.\n"
        "- Citation format and response structure match production behavior.\n"
        "- No live API/web call was executed for this answer.\n"
        "- Use this mode for UI demos and end-to-end workflow testing."
    )
    confidence = "Medium"
    sources = [
        {"title": "OpenAI API Error Codes", "url": "https://platform.openai.com/docs/guides/error-codes/api-errors"},
        {"title": "FastAPI Documentation", "url": "https://fastapi.tiangolo.com/"},
        {"title": "OpenAI Responses API", "url": "https://platform.openai.com/docs/api-reference/responses"},
    ]
    return answer, verification_notes, confidence, sources


def _extract_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return text.strip()

    # Fallback for SDK variants
    try:
        dumped = response.model_dump()
    except Exception:
        dumped = {}

    pieces: list[str] = []
    for item in dumped.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                pieces.append(content["text"])
    return "\n".join(pieces).strip()


def _extract_sources(response: Any) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()

    try:
        dumped = response.model_dump()
    except Exception:
        dumped = {}

    for item in dumped.get("output", []):
        for content in item.get("content", []):
            for ann in content.get("annotations", []):
                url = ann.get("url") or ann.get("source")
                title = ann.get("title") or ann.get("source_title") or "Source"
                if url and url not in seen:
                    seen.add(url)
                    sources.append({"title": str(title), "url": str(url)})

    return sources


def _search_web(query: str, max_results: int = 5) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    try:
        with DDGS() as ddgs:
            results = ddgs.text(query, max_results=max_results)
            for item in results:
                url = item.get("href")
                if not url:
                    continue
                sources.append(
                    {
                        "title": item.get("title", "Source"),
                        "url": url,
                        "snippet": item.get("body", ""),
                    }
                )
    except Exception:
        # Graceful fallback handled by caller.
        return []
    return sources


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _is_low_quality_source(source: dict[str, str]) -> bool:
    text = " ".join(
        [
            source.get("title", "").lower(),
            source.get("url", "").lower(),
            source.get("snippet", "").lower(),
        ]
    )
    blocked_terms = [
        "forum",
        "community",
        "support",
        "thread",
        "reddit",
        "quora",
        "stackexchange",
        "at&t",
        "att.com",
    ]
    return any(term in text for term in blocked_terms)


def _is_ceo_query(question: str) -> bool:
    q = question.lower()
    return "ceo" in q and any(
        company in q for company in ["openai", "google", "microsoft", "meta"]
    )


def _extract_companies(question: str) -> list[str]:
    q = question.lower()
    companies: list[str] = []
    if "openai" in q:
        companies.append("OpenAI")
    if "google" in q:
        companies.append("Google")
    if "microsoft" in q:
        companies.append("Microsoft")
    if "meta" in q:
        companies.append("Meta")
    return companies


def _search_ceo_sources(question: str) -> list[dict[str, str]]:
    companies = _extract_companies(question)
    if not companies:
        return []

    company_queries = {
        "OpenAI": "OpenAI leadership CEO official",
        "Google": "Google leadership CEO official",
        "Microsoft": "Microsoft leadership CEO official",
        "Meta": "Meta leadership CEO official",
    }
    gathered: list[dict[str, str]] = []
    for company in companies:
        results = _search_web(company_queries[company], max_results=8)
        cleaned = [r for r in results if not _is_low_quality_source(r)]
        # Prefer corporate/about pages first.
        cleaned.sort(
            key=lambda r: (
                0
                if any(
                    token in r.get("url", "").lower()
                    for token in ["about", "leadership", "investor", "company"]
                )
                else 1
            )
        )
        if cleaned:
            gathered.append(cleaned[0])
    return gathered


def _dedupe_sources(sources: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for source in sources:
        url = source.get("url", "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(
            {
                "title": source.get("title", "Source"),
                "url": url,
                "snippet": source.get("snippet", ""),
            }
        )
    return deduped


def _enforce_min_sources(question: str, sources: list[dict[str, str]], minimum: int = 3) -> tuple[list[dict[str, str]], bool]:
    merged = _dedupe_sources(sources)
    if len(merged) >= minimum:
        return merged, False

    needed = minimum - len(merged)
    # Supplemental search to improve citation coverage.
    supplemental = _search_web(question, max_results=max(minimum + 2, needed + 2))
    merged = _dedupe_sources(merged + supplemental)
    # Filter low-quality sources after supplementation.
    merged = [s for s in merged if not _is_low_quality_source(s)]
    return merged[: max(len(merged), minimum)], len(merged) < minimum


def _strip_as_of_date(query: str) -> str:
    # Remove phrases like "as of April 9, 2026" to improve search recall.
    cleaned = re.sub(
        r"\bas of\s+[A-Za-z]+\s+\d{1,2},\s*\d{4}\b",
        "",
        query,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or query


def _search_with_fallbacks(question: str, max_results: int = 6) -> list[dict[str, str]]:
    if _is_ceo_query(question):
        specific = _search_ceo_sources(question)
        if specific:
            return _dedupe_sources(specific)

    attempts = [
        question,
        _strip_as_of_date(question),
        f"{_strip_as_of_date(question)} official source",
    ]
    combined: list[dict[str, str]] = []
    for q in attempts:
        combined.extend(_search_web(q, max_results=max_results))
        deduped = _dedupe_sources(combined)
        deduped = [s for s in deduped if not _is_low_quality_source(s)]
        if len(deduped) >= 3:
            return deduped
    return [s for s in _dedupe_sources(combined) if not _is_low_quality_source(s)]


def _gemini_candidate_models() -> list[str]:
    base = GEMINI_MODEL.strip()
    candidates = [base]
    if base.startswith("models/"):
        candidates.append(base.replace("models/", "", 1))
    else:
        candidates.append(f"models/{base}")
    candidates.extend(
        [
            "gemini-2.0-flash",
            "models/gemini-2.0-flash",
            "gemini-1.5-flash-latest",
            "models/gemini-1.5-flash-latest",
            "gemini-1.5-flash",
            "models/gemini-1.5-flash",
        ]
    )
    # Preserve order, remove duplicates.
    seen: set[str] = set()
    ordered: list[str] = []
    for model_name in candidates:
        if model_name and model_name not in seen:
            seen.add(model_name)
            ordered.append(model_name)
    # Add runtime-discovered models from the current Gemini account/project.
    try:
        for model in genai.list_models():
            name = getattr(model, "name", "")
            methods = getattr(model, "supported_generation_methods", []) or []
            if "generateContent" in methods and name and name not in seen:
                seen.add(name)
                ordered.append(name)
    except Exception:
        # Ignore discovery errors and rely on static fallback list.
        pass
    return ordered


def _gemini_generate_content(prompt: str) -> tuple[str, str]:
    last_error: Exception | None = None
    for model_name in _gemini_candidate_models():
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            text = (getattr(response, "text", "") or "").strip()
            if text:
                return text, model_name.replace("models/", "", 1)
        except Exception as exc:  # pragma: no cover - provider/runtime dependent
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError("No Gemini model could generate content.")


def _run_primary_research(question: str) -> tuple[str, list[dict[str, str]]]:
    if client is None:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured.")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    response = client.responses.create(
        model=DEFAULT_MODEL,
        tools=[{"type": "web_search_preview"}],
        input=[
            {
                "role": "system",
                "content": (
                    "You are a research assistant. Use web search for up-to-date information. "
                    "Provide a concise answer first, then key facts. "
                    "Never invent facts. If uncertain, say so."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Date: {today} (UTC). Research and answer:\n{question}\n\n"
                    "Requirements:\n"
                    "1) Verify using recent web sources\n"
                    "2) Prefer primary/official sources\n"
                    "3) Mention if a fact may be outdated"
                ),
            },
        ],
    )

    return _extract_text(response), _extract_sources(response)


def _run_verification(question: str, draft_answer: str) -> tuple[str, str]:
    if client is None:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured.")

    response = client.responses.create(
        model=DEFAULT_MODEL,
        tools=[{"type": "web_search_preview"}],
        input=[
            {
                "role": "system",
                "content": (
                    "You are a fact-checker. Re-verify the draft answer with current web data. "
                    "Return exactly two sections:\n"
                    "Confidence: <High|Medium|Low>\n"
                    "Verification Notes: <2-5 bullet points>"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Question:\n{question}\n\nDraft answer:\n{draft_answer}\n\n"
                    "Check factual correctness and freshness of claims."
                ),
            },
        ],
    )

    text = _extract_text(response)
    confidence = "Medium"
    if "Confidence:" in text:
        for line in text.splitlines():
            if line.strip().lower().startswith("confidence:"):
                confidence = line.split(":", 1)[1].strip() or "Medium"
                break
    return text, confidence


def _run_primary_research_gemini(question: str) -> tuple[str, list[dict[str, str]], str]:
    if genai is None:
        raise HTTPException(
            status_code=500,
            detail="Gemini SDK not installed. Run: pip install google-generativeai",
        )
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured.")

    sources = _search_with_fallbacks(question, max_results=6)
    context_lines = [
        f"- {s['title']} ({s['url']}): {s.get('snippet', '')}" for s in sources
    ]
    context_blob = "\n".join(context_lines) if context_lines else "- No web results found."
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    prompt = (
        f"Date: {today} UTC\n"
        "You are a research assistant. Use the provided web results to answer accurately.\n"
        "Important: If a question says 'as of <today's date>', treat it as present-day, not future.\n"
        "Do not use forum/community/support pages as authoritative sources for company leadership facts.\n"
        "If question asks multiple companies, answer each one explicitly.\n"
        "If uncertain, say so.\n\n"
        f"Question:\n{question}\n\n"
        f"Web Results:\n{context_blob}\n\n"
        "Return:\n"
        "1) concise answer\n"
        "2) key facts in bullets\n"
        "3) mention if any detail may be outdated"
    )
    answer, used_model = _gemini_generate_content(prompt)
    return answer, [{"title": s["title"], "url": s["url"]} for s in sources], used_model


def _run_verification_gemini(question: str, draft_answer: str, sources: list[dict[str, str]]) -> tuple[str, str]:
    if genai is None:
        raise HTTPException(
            status_code=500,
            detail="Gemini SDK not installed. Run: pip install google-generativeai",
        )
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured.")

    source_lines = [f"- {s['title']}: {s['url']}" for s in sources]
    source_blob = "\n".join(source_lines) if source_lines else "- No sources available."

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt = (
        f"Date: {today} UTC.\n"
        "You are a fact-checker.\n"
        "Important: If the question references today's date, do not classify it as a future date.\n"
        "Return exactly:\n"
        "Confidence: <High|Medium|Low>\n"
        "Verification Notes: <2-5 bullet points>\n\n"
        f"Question:\n{question}\n\n"
        f"Draft answer:\n{draft_answer}\n\n"
        f"Sources:\n{source_blob}\n"
    )
    text, _ = _gemini_generate_content(prompt)
    confidence = "Medium"
    for line in text.splitlines():
        if line.strip().lower().startswith("confidence:"):
            confidence = line.split(":", 1)[1].strip() or "Medium"
            break
    return text or "Confidence: Medium\nVerification Notes:\n- Verification unavailable.", confidence


app = FastAPI(title="OpenAI Research Agent", version="1.0.0")

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
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


@app.post("/ask", response_model=AskResponse)
async def ask(payload: AskRequest) -> AskResponse:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is required.")

    if DEMO_MODE:
        primary_answer, verification_notes, confidence, sources = _demo_answer(question)
        model_name = "demo-simulated-research-agent"
    elif PROVIDER == "gemini":
        try:
            primary_answer, sources, used_model = _run_primary_research_gemini(question)
            verification_notes, confidence = _run_verification_gemini(
                question, primary_answer, sources
            )
            model_name = used_model
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Gemini error: {str(exc)}")
    else:
        try:
            primary_answer, sources = _run_primary_research(question)
            verification_notes, confidence = _run_verification(question, primary_answer)
            model_name = DEFAULT_MODEL
        except RateLimitError:
            raise HTTPException(
                status_code=429,
                detail=(
                    "OpenAI API quota exceeded or no active credits. "
                    "Please check billing/credits and try again."
                ),
            )
        except APIError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"OpenAI API error: {str(exc)}",
            )

    sources, insufficient_sources = _enforce_min_sources(question, sources, minimum=3)
    if insufficient_sources:
        verification_notes = (
            verification_notes
            + "\n- Source Quality: Insufficient sources. Fewer than 3 verifiable URLs were found for this query."
        )

    return AskResponse(
        question=question,
        answer=primary_answer,
        verification_notes=verification_notes,
        confidence=confidence,
        sources=sources,
        verified_at_utc=datetime.now(timezone.utc).isoformat(),
        model=model_name,
    )
