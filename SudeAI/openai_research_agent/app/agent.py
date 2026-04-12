import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any
from urllib.parse import quote_plus, urlparse
import xml.etree.ElementTree as ET
from collections import defaultdict

import httpx
from bs4 import BeautifulSoup
try:
    from ddgs import DDGS
except Exception:  # pragma: no cover
    from duckduckgo_search import DDGS

try:
    from google import genai
except Exception:  # pragma: no cover
    genai = None


@dataclass
class AgentConfig:
    base_dir: Path
    gemini_api_key: str | None
    gemini_model: str
    google_cse_api_key: str | None
    google_cse_cx: str | None
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
        self.cache_ttl_seconds = 900
        self._cache: dict[str, tuple[float, Any]] = {}
        self.gemini_client = None
        if genai is not None and config.gemini_api_key:
            try:
                self.gemini_client = genai.Client(api_key=config.gemini_api_key)
            except Exception:
                self.gemini_client = None

    def _cache_get(self, key: str) -> Any | None:
        item = self._cache.get(key)
        if not item:
            return None
        ts, value = item
        if (time.time() - ts) > self.cache_ttl_seconds:
            self._cache.pop(key, None)
            return None
        return value

    def _cache_set(self, key: str, value: Any) -> None:
        self._cache[key] = (time.time(), value)

    def _domain(self, url: str) -> str:
        try:
            return urlparse(url).netloc.lower()
        except Exception:
            return ""

    def _clean_title(self, title: str) -> str:
        t = (title or "").strip()
        t = re.sub(r"\s+", " ", t)
        if "Top Stories" in t:
            t = t.split("Top Stories", 1)[0].strip(" -|:")
        if len(t) > 140:
            t = t[:137].rstrip() + "..."
        return t or "Source"

    def _publisher_search_url(self, title: str) -> str:
        q = quote_plus((title or "").strip())
        return f"https://www.google.com/search?q={q}"

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

    def _expand_queries(self, interpreted_question: str) -> list[str]:
        base = interpreted_question.strip()
        out = [base]
        low = base.lower()
        if low.startswith("who is "):
            subject = base[7:].strip(" ?!.")
            if subject:
                out.extend([f"{subject} biography", f"{subject} profile", f"{subject} wikipedia"])
                if " " in subject:
                    out.append(f"{subject} official")
        if any(term in low for term in ["news", "trending", "latest", "today"]):
            out.extend([f"{base} reuters", f"{base} ap news", f"{base} bbc"])
        seen: set[str] = set()
        deduped: list[str] = []
        for q in out:
            if q and q not in seen:
                seen.add(q)
                deduped.append(q)
        return deduped

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
        # news.google.com is an aggregator, not the original publisher.
        if domain == "news.google.com":
            return 1
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
            out.append({"title": self._clean_title(s.get("title", "Source")), "url": u, "snippet": s.get("snippet", "")})
        out.sort(key=self._source_rank)
        return out

    def _apply_source_diversity(
        self,
        sources: list[dict[str, str]],
        max_total: int = 12,
        max_per_domain: int = 2,
        max_aggregator: int = 2,
    ) -> list[dict[str, str]]:
        if not sources:
            return []
        domain_counts: dict[str, int] = defaultdict(int)
        selected: list[dict[str, str]] = []
        aggregator_domain = "news.google.com"
        aggregator_count = 0

        for src in sources:
            domain = self._domain(src.get("url", ""))
            if not domain:
                continue
            if domain == aggregator_domain and aggregator_count >= max_aggregator:
                continue
            if domain_counts[domain] >= max_per_domain:
                continue
            selected.append(src)
            domain_counts[domain] += 1
            if domain == aggregator_domain:
                aggregator_count += 1
            if len(selected) >= max_total:
                break

        return selected

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
        cache_key = f"web:{query}:{max_results}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
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
            self._cache_set(cache_key, sources)
            return sources
        except Exception:
            return []

    def tool_google_cse_search(self, query: str, max_results: int = 8) -> list[dict[str, str]]:
        cache_key = f"cse:{query}:{max_results}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        api_key = (self.config.google_cse_api_key or "").strip()
        cx = (self.config.google_cse_cx or "").strip()
        if not api_key or not cx:
            return []
        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                res = client.get(
                    "https://www.googleapis.com/customsearch/v1",
                    params={"key": api_key, "cx": cx, "q": query, "num": min(max_results, 10)},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
            if res.status_code >= 400:
                return []
            payload = res.json()
            items = payload.get("items") or []
            out: list[dict[str, str]] = []
            for item in items:
                url = item.get("link", "")
                if not url:
                    continue
                out.append(
                    {
                        "title": item.get("title", "Source"),
                        "url": url,
                        "snippet": item.get("snippet", ""),
                    }
                )
            out = self._dedupe(out)
            self._cache_set(cache_key, out)
            return out
        except Exception:
            return []

    def tool_google_news_rss(self, query: str, max_results: int = 8) -> list[dict[str, str]]:
        cache_key = f"gnrss:{query}:{max_results}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            rss_url = (
                "https://news.google.com/rss/search"
                f"?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
            )
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                res = client.get(rss_url, headers={"User-Agent": "Mozilla/5.0"})
            if res.status_code >= 400:
                return []
            root = ET.fromstring(res.text)
            out: list[dict[str, str]] = []
            for item in root.findall(".//item")[:max_results]:
                title = (item.findtext("title") or "Source").strip()
                url = (item.findtext("link") or "").strip()
                if not url:
                    continue
                snippet = (item.findtext("description") or "").strip()
                out.append({"title": title, "url": url, "snippet": snippet})
            out = self._dedupe(out)
            self._cache_set(cache_key, out)
            return out
        except Exception:
            return []

    def tool_fetch_page(self, url: str, max_chars: int = 2500) -> tuple[str, str]:
        cache_key = f"fetch:{url}:{max_chars}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            with httpx.Client(timeout=8.0, follow_redirects=True) as client:
                res = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if res.status_code >= 400:
                return "", url
            soup = BeautifulSoup(res.text, "html.parser")
            for t in soup(["script", "style", "noscript"]):
                t.decompose()
            text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
            final_url = str(getattr(res, "url", url))
            payload = (text[:max_chars], final_url)
            self._cache_set(cache_key, payload)
            return payload
        except Exception:
            return "", url

    def _gemini_models(self) -> list[str]:
        base = self.config.gemini_model.strip()
        ordered = [base, base.replace("models/", "", 1), "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
        seen: set[str] = set()
        unique: list[str] = []
        for m in ordered:
            if m and m not in seen:
                seen.add(m)
                unique.append(m)
        return unique

    def _generate(self, prompt: str) -> tuple[str, str]:
        if self.config.demo_mode:
            return (
                "Demo mode: live assistant response generated without external model call.",
                "demo-simulated-research-agent",
            )
        if self.gemini_client is None or not self.config.gemini_api_key:
            raise RuntimeError("Gemini is not configured.")
        last_error: Exception | None = None
        for model_name in self._gemini_models():
            try:
                response = self.gemini_client.models.generate_content(model=model_name, contents=prompt)
                text = (getattr(response, "text", "") or "").strip()
                if text:
                    return text, model_name
            except Exception as exc:
                last_error = exc
                continue
        raise RuntimeError(str(last_error) if last_error else "No Gemini model available.")

    def _extractive_fallback(
        self,
        question: str,
        now: str,
        sources: list[dict[str, str]],
        fetch_blocks: list[str],
    ) -> str:
        top_sources = sources[:3]
        if not top_sources:
            return f"Insufficient sources.\n\nData Freshness: {now}"
        bullets = []
        for src in top_sources:
            title = src.get("title", "Source")
            url = src.get("url", "")
            snippet = (src.get("snippet", "") or "").strip()
            if snippet:
                snippet = snippet[:220]
            bullets.append(f"- {title}: {snippet} [{title}]({url})")
        return (
            f"Fallback answer (model unavailable): based on retrieved evidence for '{question}'.\n\n"
            + "\n".join(bullets)
            + f"\n\nData Freshness: {now}"
        )

    def run(self, question: str, session_id: str, strict_sources: bool = False) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        memory = self.memory.recent(session_id, limit=8)
        memory_blob = "\n".join([f"{m['role']}: {m['content'][:280]}" for m in memory]) or "No prior chat history."
        interpreted_question = self._normalize_question(question)

        normalized_q = re.sub(
            r"\bas of\s+[A-Za-z]+\s+\d{1,2},\s*\d{4}\b", "", interpreted_question, flags=re.IGNORECASE
        ).strip()
        search_queries = [interpreted_question, normalized_q, f"{normalized_q or interpreted_question} official source"]
        search_queries.extend(self._expand_queries(interpreted_question))
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
            tool_trace.append(f"google_news_rss('{q[:100]}')")
            sources.extend(self.tool_google_news_rss(q, max_results=6))
            if self.config.google_cse_api_key and self.config.google_cse_cx:
                tool_trace.append(f"google_cse_search('{q[:100]}')")
                sources.extend(self.tool_google_cse_search(q, max_results=8))
            sources = [s for s in sources if self._is_relevant(s, interpreted_question)]
            sources = self._dedupe(sources)
            sources = self._apply_source_diversity(sources)
            if len(sources) >= 5:
                break

        if len(sources) < 3:
            sources = self._dedupe(sources + self._fallback_reference_sources(interpreted_question))
            sources = self._apply_source_diversity(sources)

        fetch_blocks: list[str] = []
        source_snapshots: list[dict[str, str]] = []
        for src in sources[:3]:
            original_url = src["url"]
            tool_trace.append(f"fetch_page('{original_url}')")
            text, resolved_url = self.tool_fetch_page(original_url)
            if resolved_url and resolved_url != original_url:
                src["url"] = resolved_url
                tool_trace.append(f"resolved_url('{original_url}' -> '{resolved_url}')")
            effective_text = text.strip()
            if not effective_text and src.get("snippet"):
                effective_text = src["snippet"]
            if effective_text:
                snapshot_excerpt = effective_text[:420]
                source_snapshots.append(
                    {
                        "title": src.get("title", "Source"),
                        "url": src.get("url", original_url),
                        "domain": self._domain(src.get("url", original_url)),
                        "excerpt": snapshot_excerpt,
                        "publisher_search_url": self._publisher_search_url(src.get("title", "Source")),
                    }
                )
            if text:
                fetch_blocks.append(f"Source: {src['title']} ({src['url']})\n{text}")
            elif src.get("snippet"):
                # Fallback to search snippet when full page fetch is blocked.
                fetch_blocks.append(f"Source: {src['title']} ({src['url']})\n{src['snippet']}")

        sources = self._apply_source_diversity(self._dedupe(sources))

        if len(sources) < 3:
            tool_trace.append("source_policy: insufficient_sources")

        source_lines = "\n".join([f"- {s['title']}: {s['url']}" for s in sources[:8]]) or "- No sources found."
        fetched_text = "\n\n".join(fetch_blocks) or "No page extracts available."

        if strict_sources and (len(sources) < 3 or len(fetch_blocks) < 2):
            tool_trace.append("strict_source_policy: blocked_low_evidence")
            answer = (
                "Insufficient high-quality sources for a reliable answer.\n\n"
                f"Source count: {len(sources)} | Extracted pages/snippets: {len(fetch_blocks)}\n"
                f"Data Freshness: {now}"
            )
            verification_notes = (
                "Confidence: High\n"
                "Verification Notes:\n"
                "- Strict source mode is enabled.\n"
                "- Fewer than 3 relevant sources or fewer than 2 extracted evidence blocks were available.\n"
                "- Returning a blocked answer is safer than generating unsupported claims."
            )
            confidence = "High"
            used_model = "source-policy"
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
                        "publisher_search_url": self._publisher_search_url(s["title"]),
                    }
                )

            retriever_output = (
                f"Queries executed: {len([t for t in tool_trace if t.startswith('web_search')])}\n"
                f"Sources collected: {len(cleaned_sources)}\n"
                f"Top domains: {', '.join(sorted({s['domain'] for s in cleaned_sources[:5] if s.get('domain')})) or 'none'}"
            )
            agent_panels = {
                "retriever": retriever_output,
                "analyst": answer,
                "verifier": verification_notes,
                "summarizer": "Strict mode blocked output due to weak evidence.",
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
                "source_snapshots": source_snapshots,
            }

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
        try:
            answer, used_model = self._generate(answer_prompt)
            # Guardrail: prevent stale memory timestamps leaking into current output.
            answer = re.sub(r"Data Freshness:\s*.+", f"Data Freshness: {now}", answer)
        except Exception:
            used_model = "extractive-fallback"
            tool_trace.append("model_routing: gemini_failed -> extractive_fallback")
            answer = self._extractive_fallback(question=question, now=now, sources=sources, fetch_blocks=fetch_blocks)

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
        try:
            verification_notes, _ = self._generate(verification_prompt)
            confidence = "Medium"
            for line in verification_notes.splitlines():
                if line.lower().startswith("confidence:"):
                    confidence = line.split(":", 1)[1].strip() or "Medium"
                    break
        except Exception:
            tool_trace.append("model_routing: verifier_failed -> heuristic_verifier")
            confidence = "Medium" if len(fetch_blocks) >= 2 else "Low"
            verification_notes = (
                f"Confidence: {confidence}\n"
                "Verification Notes:\n"
                "- Verification model was unavailable, used heuristic fallback.\n"
                f"- Relevant sources found: {len(sources)}.\n"
                f"- Evidence blocks extracted: {len(fetch_blocks)}."
            )

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
                    "publisher_search_url": self._publisher_search_url(s["title"]),
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
            "source_snapshots": source_snapshots,
        }
