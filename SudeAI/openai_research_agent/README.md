# OpenAI Research Agent

A live AI assistant that can:
- access the internet
- answer questions in near real time
- verify answers using fresh web data

## Features
- Web-enabled research using OpenAI Responses API + `web_search_preview`
- Gemini provider mode using free-tier API + DuckDuckGo web retrieval
- Two-pass flow:
  - primary answer generation
  - verification/fact-check pass
- Source link extraction (when returned by the model)
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
- `answer`
- `verification_notes`
- `confidence`
- `sources`
- `verified_at_utc`

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
