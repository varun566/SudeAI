# SudeAI - Live AI Assistant

A production-style, web-connected AI assistant that can:
- access the internet
- answer questions in real time
- verify responses using fresh source evidence

## Live Demo
- https://sudeai-research-agent.onrender.com

## Highlights
- Multi-agent pipeline: Retriever -> Analyst -> Verifier -> Summarizer
- Live retrieval: DDGS, Google News RSS, optional Google Custom Search
- Evidence and trust display: sources, trust tiers, snapshots, tool trace
- Session memory with timeline
- Streaming answers (SSE)
- Voice input/output in browser
- Shareable reports + owner dashboard
- Reliability and production guardrails:
  - stream retry / cold-start resilience
  - source diversity controls
  - optional API key protection
  - per-scope rate limiting
  - health/version endpoints

## Tech Stack
- Backend: FastAPI, Python
- AI: Gemini (with configurable model), tool-calling pipeline
- Data: SQLite (memory, reports, analytics)
- Frontend: HTML, CSS, vanilla JS
- Deployment: Render (Blueprint via `render.yaml`)

## Project Layout
- Main app: `openai_research_agent/`
- Render blueprint: `render.yaml`

## Quick Start
```bash
cd openai_research_agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --reload-dir app --reload-dir static --reload-dir templates --port 8000
```

Open: http://127.0.0.1:8000

## Environment (minimum)
```env
DEMO_MODE=false
GEMINI_API_KEY=your_key_here
```

Optional:
```env
GOOGLE_CSE_API_KEY=...
GOOGLE_CSE_CX=...
APP_API_KEY=...
RATE_LIMIT_PER_MINUTE=60
REPORT_WRITE_TOKEN=...
```

## Full Documentation
- Detailed README: `openai_research_agent/README.md`
