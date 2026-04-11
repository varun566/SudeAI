const form = document.getElementById('ask-form');
const questionEl = document.getElementById('question');
const submitBtn = document.getElementById('submit-btn');
const statusEl = document.getElementById('status');
const resultEl = document.getElementById('result');
const answerEl = document.getElementById('answer');
const sessionEl = document.getElementById('session');
const verificationEl = document.getElementById('verification');
const confidenceEl = document.getElementById('confidence');
const verifiedAtEl = document.getElementById('verified-at');
const modelEl = document.getElementById('model');
const sourcesEl = document.getElementById('sources');
const toolTraceEl = document.getElementById('tool-trace');
const timelineEl = document.getElementById('timeline');

const SESSION_KEY = 'live_ai_session_id';
let sessionId = localStorage.getItem(SESSION_KEY) || null;

async function refreshTimeline() {
  if (!sessionId) {
    timelineEl.innerHTML = '<div class="timeline-empty">No memory yet.</div>';
    return;
  }
  try {
    const response = await fetch(`/history/${encodeURIComponent(sessionId)}`);
    if (!response.ok) {
      timelineEl.innerHTML = '<div class="timeline-empty">Unable to load memory.</div>';
      return;
    }
    const data = await response.json();
    const items = data.messages || [];
    if (items.length === 0) {
      timelineEl.innerHTML = '<div class="timeline-empty">No memory yet.</div>';
      return;
    }
    timelineEl.innerHTML = '';
    items.forEach((msg) => {
      const row = document.createElement('div');
      row.className = `timeline-item ${msg.role || 'assistant'}`;
      const role = document.createElement('div');
      role.className = 'timeline-role';
      role.textContent = (msg.role || 'assistant').toUpperCase();
      const text = document.createElement('div');
      text.className = 'timeline-text';
      text.textContent = msg.content || '';
      row.appendChild(role);
      row.appendChild(text);
      timelineEl.appendChild(row);
    });
  } catch {
    timelineEl.innerHTML = '<div class="timeline-empty">Unable to load memory.</div>';
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const question = questionEl.value.trim();
  if (!question) return;

  submitBtn.disabled = true;
  statusEl.textContent = 'Researching and verifying with live web data...';
  resultEl.classList.add('hidden');

  try {
    const response = await fetch('/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, session_id: sessionId }),
    });

    let data;
    const raw = await response.text();
    try {
      data = raw ? JSON.parse(raw) : {};
    } catch {
      throw new Error(raw || `Request failed with status ${response.status}`);
    }

    if (!response.ok) {
      throw new Error(data.detail || 'Failed to process request');
    }

    answerEl.textContent = data.answer;
    sessionId = data.session_id;
    localStorage.setItem(SESSION_KEY, sessionId);
    sessionEl.textContent = `Session ID: ${sessionId}\nMemory turns used: ${data.memory_used}`;
    await refreshTimeline();
    verificationEl.textContent = data.verification_notes;
    confidenceEl.textContent = `Confidence: ${data.confidence}`;
    verifiedAtEl.textContent = `Verified at (UTC): ${data.verified_at_utc}`;
    modelEl.textContent = `Model: ${data.model}`;

    sourcesEl.innerHTML = '';
    if (data.sources.length === 0) {
      const li = document.createElement('li');
      li.textContent = 'No explicit source links were returned by the model for this run.';
      sourcesEl.appendChild(li);
    } else {
      data.sources.forEach((source) => {
        const li = document.createElement('li');
        const a = document.createElement('a');
        a.href = source.url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        a.textContent = source.title || source.url;
        li.appendChild(a);
        sourcesEl.appendChild(li);
      });
    }

    toolTraceEl.textContent = (data.tool_trace || []).join('\n') || 'No tools called.';

    resultEl.classList.remove('hidden');
    statusEl.textContent = 'Done.';
  } catch (error) {
    statusEl.textContent = `Error: ${error.message}`;
  } finally {
    submitBtn.disabled = false;
  }
});

refreshTimeline();
