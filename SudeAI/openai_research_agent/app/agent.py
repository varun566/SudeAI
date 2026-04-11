import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

try:
    import google.generativeai as genai
except Exception:  # pragma: no cover
    genai = None


@dataclass
class AgentConfig:
    base_dir: Path
    gemini_api_key: str | None
    gemini_model: str
    demo_mode: bool


class MemoryStore:
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
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def save(self, session_id: str, role: str, content: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO chat_messages(session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

    def recent(self, session_id: str, limit: int = 8) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content, created_at
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        items = [{"role": r[0], "content": r[1], "created_at": r[2]} for r in rows]
        items.reverse()
        return items


class LiveResearchAgent:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.memory = MemoryStore(config.base_dir / "data" / "memory.db")
        if genai is not None and config.gemini_api_key:
            genai.configure(api_key=config.gemini_api_key)

    def _domain(self, url: str) -> str:
        try:
            return urlparse(url).netloc.lower()
        except Exception:
            return ""

    def _is_low_quality(self, source: dict[str, str]) -> bool:
        text = " ".join(
            [source.get("title", "").lower(), source.get("url", "").lower(), source.get("snippet", "").lower()]
        )
        blocked_terms = ["forum", "community", "support", "thread", "reddit", "quora", "at&t", "att.com"]
        return any(term in text for term in blocked_terms)

    def _normalize_question(self, question: str) -> str:
        q = question.strip()
        low = q.lower().strip(" ?!.")
        acronym_map = {
            "ml": "machine learning in artificial intelligence",
            "ai": "artificial intelligence in computer science",
            "nlp": "natural language processing in artificial intelligence",
            "cv": "computer vision in artificial intelligence",
            "llm": "large language model in artificial intelligence",
        }
        if low in acronym_map:
            return f"What is {acronym_map[low]}?"
        return q

    def _query_keywords(self, interpreted_question: str) -> set[str]:
        normalized = interpreted_question.lower()
        normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
        tokens = {
            t
            for t in normalized.split()
            if len(t) >= 3 and t not in {"what", "when", "where", "which", "with", "from", "that", "this", "about"}
        }
        # Strong hint words for common education prompts.
        if "machine learning" in normalized:
            tokens.update({"machine", "learning", "artificial", "intelligence"})
        if "artificial intelligence" in normalized:
            tokens.update({"artificial", "intelligence", "ai"})
        return tokens

    def _is_relevant(self, source: dict[str, str], interpreted_question: str) -> bool:
        q_tokens = self._query_keywords(interpreted_question)
        if not q_tokens:
            return True
        haystack = " ".join(
            [source.get("title", "").lower(), source.get("snippet", "").lower(), source.get("url", "").lower()]
        )
        matches = sum(1 for tok in q_tokens if tok in haystack)
        # Small queries need at least one token, longer ones need better overlap.
        required = 1 if len(q_tokens) <= 4 else 2
        return matches >= required

    def _source_rank(self, source: dict[str, str]) -> int:
        domain = self._domain(source.get("url", ""))
        trusted_exact = {
            "reuters.com",
            "apnews.com",
            "bbc.com",
            "nytimes.com",
            "wsj.com",
            "ft.com",
            "bloomberg.com",
            "openai.com",
            "google.com",
            "microsoft.com",
            "meta.com",
            "who.int",
            "cdc.gov",
            "nasa.gov",
            "sec.gov",
            "europa.eu",
            "wikipedia.org",
        }
        if any(domain == d or domain.endswith(f".{d}") for d in trusted_exact):
            return 0
        if domain.endswith(".gov") or domain.endswith(".edu") or domain.endswith(".org"):
            return 1
        return 2

    def _trust_tier(self, source: dict[str, str]) -> str:
        rank = self._source_rank(source)
        if rank == 0:
            return "high"
        if rank == 1:
            return "medium"
        return "low"

    def _dedupe(self, sources: list[dict[str, str]]) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for s in sources:
            u = s.get("url", "").strip()
            if not u or u in seen:
                continue
            seen.add(u)
            out.append({"title": s.get("title", "Source"), "url": u, "snippet": s.get("snippet", "")})
        out.sort(key=self._source_rank)
        return out

    def _fallback_reference_sources(self, interpreted_question: str) -> list[dict[str, str]]:
        q = interpreted_question.lower()
        if "machine learning" in q:
            return [
                {
                    "title": "Machine learning - Wikipedia",
                    "url": "https://en.wikipedia.org/wiki/Machine_learning",
                    "snippet": "Machine learning is a field of study in artificial intelligence.",
                },
                {
                    "title": "What is machine learning? - IBM",
                    "url": "https://www.ibm.com/think/topics/machine-learning",
                    "snippet": "Machine learning enables systems to learn from data and improve.",
                },
                {
                    "title": "Machine learning - Britannica",
                    "url": "https://www.britannica.com/technology/machine-learning",
                    "snippet": "Machine learning is a branch of AI and computer science.",
                },
            ]
        if "artificial intelligence" in q:
            return [
                {
                    "title": "Artificial intelligence - Wikipedia",
                    "url": "https://en.wikipedia.org/wiki/Artificial_intelligence",
                    "snippet": "Artificial intelligence is intelligence demonstrated by machines.",
                },
                {
                    "title": "What is Artificial Intelligence (AI)? - IBM",
                    "url": "https://www.ibm.com/think/topics/artificial-intelligence",
                    "snippet": "AI is technology that enables computers to simulate human intelligence.",
                },
                {
                    "title": "Artificial intelligence - Britannica",
                    "url": "https://www.britannica.com/technology/artificial-intelligence",
                    "snippet": "AI methods and applications overview.",
                },
            ]
        return []

    def tool_web_search(self, query: str, max_results: int = 8) -> list[dict[str, str]]:
        try:
            with DDGS() as ddgs:
                results: list[dict[str, Any]] = []
                q = query.strip().lower()
                is_news_query = any(term in q for term in ["news", "headline", "trending", "latest"])

                # Prefer news index for news-like prompts.
                if is_news_query:
                    try:
                        news_results = ddgs.news(query, max_results=max_results)
                        results.extend(news_results or [])
                    except Exception:
                        pass

                if not results:
                    text_results = ddgs.text(query, max_results=max_results)
                    results.extend(text_results or [])

            sources = []
            for item in results:
                url = item.get("href") or item.get("url")
                if not url:
                    continue
                snippet = item.get("body") or item.get("snippet") or item.get("description") or ""
                sources.append({"title": item.get("title", "Source"), "url": url, "snippet": snippet})
            sources = [s for s in self._dedupe(sources) if not self._is_low_quality(s)]
            return sources
        except Exception:
            return []

    def tool_fetch_page(self, url: str, max_chars: int = 2500) -> str:
        try:
            with httpx.Client(timeout=8.0, follow_redirects=True) as client:
                res = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if res.status_code >= 400:
                return ""
            soup = BeautifulSoup(res.text, "html.parser")
            for t in soup(["script", "style", "noscript"]):
                t.decompose()
            text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
            return text[:max_chars]
        except Exception:
            return ""

    def _gemini_models(self) -> list[str]:
        base = self.config.gemini_model.strip()
        ordered = [base, f"models/{base}", "gemini-2.5-flash", "models/gemini-2.5-flash", "gemini-2.0-flash"]
        seen: set[str] = set()
        unique: list[str] = []
        for m in ordered:
            if m and m not in seen:
                seen.add(m)
                unique.append(m)
        if genai is not None:
            try:
                for model in genai.list_models():
                    name = getattr(model, "name", "")
                    methods = getattr(model, "supported_generation_methods", []) or []
                    if "generateContent" in methods and name and name not in seen:
                        seen.add(name)
                        unique.append(name)
            except Exception:
                pass
        return unique

    def _generate(self, prompt: str) -> tuple[str, str]:
        if self.config.demo_mode:
            return (
                "Demo mode: live assistant response generated without external model call.",
                "demo-simulated-research-agent",
            )
        if genai is None or not self.config.gemini_api_key:
            raise RuntimeError("Gemini is not configured.")
        last_error: Exception | None = None
        for model_name in self._gemini_models():
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(prompt)
                text = (getattr(response, "text", "") or "").strip()
                if text:
                    return text, model_name.replace("models/", "", 1)
            except Exception as exc:
                last_error = exc
                continue
        raise RuntimeError(str(last_error) if last_error else "No Gemini model available.")

    def run(self, question: str, session_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        memory = self.memory.recent(session_id, limit=8)
        memory_blob = "\n".join([f"{m['role']}: {m['content'][:280]}" for m in memory]) or "No prior chat history."
        interpreted_question = self._normalize_question(question)

        normalized_q = re.sub(
            r"\bas of\s+[A-Za-z]+\s+\d{1,2},\s*\d{4}\b", "", interpreted_question, flags=re.IGNORECASE
        ).strip()
        search_queries = [interpreted_question, normalized_q, f"{normalized_q or interpreted_question} official source"]
        if any(term in question.lower() for term in ["news", "headline", "trending", "latest"]):
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            search_queries.extend(
                [
                    f"top world news {today}",
                    f"latest technology headlines {today}",
                    f"reuters ap bbc top stories {today}",
                ]
            )
        # Preserve order, drop empty/duplicates.
        seen_q: set[str] = set()
        deduped_queries: list[str] = []
        for q in search_queries:
            q = q.strip()
            if not q or q in seen_q:
                continue
            seen_q.add(q)
            deduped_queries.append(q)

        tool_trace: list[str] = []
        sources: list[dict[str, str]] = []
        for q in deduped_queries:
            if not q:
                continue
            tool_trace.append(f"web_search('{q[:100]}')")
            sources.extend(self.tool_web_search(q, max_results=8))
            sources = [s for s in sources if self._is_relevant(s, interpreted_question)]
            sources = self._dedupe(sources)
            if len(sources) >= 5:
                break

        if len(sources) < 3:
            sources = self._dedupe(sources + self._fallback_reference_sources(interpreted_question))

        fetch_blocks: list[str] = []
        for src in sources[:3]:
            tool_trace.append(f"fetch_page('{src['url']}')")
            text = self.tool_fetch_page(src["url"])
            if text:
                fetch_blocks.append(f"Source: {src['title']} ({src['url']})\n{text}")
            elif src.get("snippet"):
                # Fallback to search snippet when full page fetch is blocked.
                fetch_blocks.append(f"Source: {src['title']} ({src['url']})\n{src['snippet']}")

        if len(sources) < 3:
            tool_trace.append("source_policy: insufficient_sources")

        source_lines = "\n".join([f"- {s['title']}: {s['url']}" for s in sources[:8]]) or "- No sources found."
        fetched_text = "\n\n".join(fetch_blocks) or "No page extracts available."

        answer_prompt = (
            f"Current UTC time: {now}\n"
            "You are a Live AI Assistant.\n"
            "Goals:\n"
            "1) Use latest web evidence\n"
            "2) Answer in real time with concise, factual output\n"
            "3) Cite sources inline as [Title](URL)\n"
            "4) If evidence is weak, say 'insufficient sources'\n\n"
            "5) Always set Data Freshness to the current UTC time provided above, never reuse past timestamps from memory.\n\n"
            f"Conversation memory:\n{memory_blob}\n\n"
            f"Original user question:\n{question}\n\n"
            f"Interpreted question for research:\n{interpreted_question}\n\n"
            f"Web sources:\n{source_lines}\n\n"
            f"Fetched excerpts:\n{fetched_text}\n\n"
            "Return: short answer, key facts, and a final 'Data Freshness' line."
        )
        answer, used_model = self._generate(answer_prompt)
        # Guardrail: prevent stale memory timestamps leaking into current output.
        answer = re.sub(r"Data Freshness:\s*.+", f"Data Freshness: {now}", answer)

        verification_prompt = (
            f"Current UTC time: {now}\n"
            "You are a verifier. Check whether the answer is supported by the supplied sources.\n"
            "Return exactly:\n"
            "Confidence: <High|Medium|Low>\n"
            "Verification Notes:\n"
            "- <2-5 bullets>\n\n"
            f"Question:\n{question}\n\n"
            f"Answer Draft:\n{answer}\n\n"
            f"Sources:\n{source_lines}\n\n"
            f"Fetched excerpts for verification:\n{fetched_text}\n\n"
            "Important: Do not say you cannot browse if excerpts are provided. Verify against provided evidence."
        )
        verification_notes, _ = self._generate(verification_prompt)
        confidence = "Medium"
        for line in verification_notes.splitlines():
            if line.lower().startswith("confidence:"):
                confidence = line.split(":", 1)[1].strip() or "Medium"
                break

        self.memory.save(session_id, "user", question)
        self.memory.save(session_id, "assistant", answer)

        cleaned_sources = []
        for s in self._dedupe(sources):
            cleaned_sources.append(
                {
                    "title": s["title"],
                    "url": s["url"],
                    "domain": self._domain(s["url"]),
                    "trust_tier": self._trust_tier(s),
                }
            )

        summary_lines = [ln.strip() for ln in answer.splitlines() if ln.strip()][:3]
        summarizer_output = "\n".join(summary_lines) if summary_lines else "No summary available."
        retriever_output = (
            f"Queries executed: {len([t for t in tool_trace if t.startswith('web_search')])}\n"
            f"Sources collected: {len(cleaned_sources)}\n"
            f"Top domains: {', '.join(sorted({s['domain'] for s in cleaned_sources[:5] if s.get('domain')})) or 'none'}"
        )
        agent_panels = {
            "retriever": retriever_output,
            "analyst": answer,
            "verifier": verification_notes,
            "summarizer": summarizer_output,
        }

        return {
            "answer": answer,
            "verification_notes": verification_notes,
            "confidence": confidence,
            "sources": cleaned_sources,
            "model": used_model,
            "verified_at_utc": now,
            "tool_trace": tool_trace,
            "memory_used": len(memory),
            "agent_panels": agent_panels,
        }
