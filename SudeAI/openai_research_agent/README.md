# OpenAI Research Agent

A live AI assistant that can:
- access the internet
- answer questions in near real time
- verify answers using fresh web data

## Features
- Agent-style orchestration with explicit tool calls
- Live web search + page fetching for latest data
- Acronym disambiguation for tech context (e.g., `ml` -> Machine Learning)
- Trusted-source ranking (official/news/authority domains prioritized)
- Streaming response UX via SSE (`/ask_stream`)
- Voice mode (browser speech-to-text + text-to-speech)
- Multi-agent tabbed panel (Retriever, Analyst, Verifier, Summarizer)
- Source trust cards (domain + trust tier)
- Two-pass flow:
  - primary answer generation
  - verification/fact-check pass
- Session memory (SQLite) across turns
- Source links + tool trace + conversation timeline visible in UI
- Simple FastAPI backend + browser UI

## Project Structure

```text
openai_research_agent/
├── app/
│   └── main.py
├── static/
│   ├── app.js
│   └── styles.css
├── templates/
│   └── index.html
├── .env.example
└── requirements.txt
```

## Setup

1. Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure environment:

```bash
cp .env.example .env
# then edit .env and add your OPENAI_API_KEY
```

For classroom/demo-only runs without API credits:

```env
DEMO_MODE=true
```

When `DEMO_MODE=true`, `/ask` returns a simulated research + verification response
without calling OpenAI.

To use Gemini free tier for live results:

```env
DEMO_MODE=false
PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.0-flash
```

4. Run server:

```bash
uvicorn app.main:app --reload --port 8000
```

5. Open:

- http://127.0.0.1:8000

## API

### `POST /ask`

Request:

```json
{
  "question": "What are the latest major updates from OpenAI this week?"
}
```

Response includes:
- `session_id`
- `answer`
- `verification_notes`
- `confidence`
- `sources`
- `verified_at_utc`
- `tool_trace`
- `memory_used`

### `GET /history/{session_id}`

Returns recent chat memory for a session.

### `GET /ask_stream?question=...&session_id=...`

Streams assistant output using Server-Sent Events:
- `status`
- `chunk`
- `final`

## Notes
- This app requires internet access at runtime for web search.
- Some runs may return fewer/no explicit source annotations depending on model output.
- If your account has no API quota, keep `DEMO_MODE=true` for presentation/testing.

## Deploy (Render - Public URL)

This repository includes a Render blueprint at `render.yaml` (repo root).

### Quick Steps
1. Push latest code to GitHub (`varun566/SudeAI`).
2. In Render, click **New +** -> **Blueprint**.
3. Select your GitHub repo: `varun566/SudeAI`.
4. Render auto-detects `render.yaml`.
5. Set secret env var:
   - `GEMINI_API_KEY` = your Gemini API key
6. Click **Apply** to deploy.

### Result
- Render builds from `openai_research_agent/`
- Starts with:
  - `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- You get a public URL like:
  - `https://sudeai-research-agent.onrender.com`
