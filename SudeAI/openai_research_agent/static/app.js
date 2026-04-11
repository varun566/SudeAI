const form = document.getElementById('ask-form');
const questionEl = document.getElementById('question');
const submitBtn = document.getElementById('submit-btn');
const micBtn = document.getElementById('mic-btn');
const speakBtn = document.getElementById('speak-btn');
const newSessionBtn = document.getElementById('new-session-btn');
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
const agentPanelEl = document.getElementById('agent-panel');
const agentTabsEl = document.getElementById('agent-tabs');
const themeToggleBtn = document.getElementById('theme-toggle-btn');
const intensitySelect = document.getElementById('liquid-intensity');
const copyAnswerBtn = document.getElementById('copy-answer-btn');
const downloadAnswerBtn = document.getElementById('download-answer-btn');

const SESSION_KEY = 'live_ai_session_id';
const THEME_KEY = 'live_ai_theme';
const LIQUID_INTENSITY_KEY = 'live_ai_liquid_intensity';
let sessionId = localStorage.getItem(SESSION_KEY) || null;
let latestAnswer = '';
let latestAgentPanels = {};

function setTheme(mode) {
  const dark = mode === 'dark';
  document.body.classList.toggle('dark', dark);
  localStorage.setItem(THEME_KEY, dark ? 'dark' : 'light');
  if (themeToggleBtn) {
    themeToggleBtn.textContent = dark ? 'Light Mode' : 'Dark Mode';
  }
}

function initTheme() {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved === 'dark' || saved === 'light') {
    setTheme(saved);
    return;
  }
  const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  setTheme(prefersDark ? 'dark' : 'light');
}

function setLiquidIntensity(level) {
  const safe = ['low', 'medium', 'high'].includes(level) ? level : 'medium';
  document.body.dataset.liquid = safe;
  localStorage.setItem(LIQUID_INTENSITY_KEY, safe);
  if (intensitySelect) intensitySelect.value = safe;
}

function initLiquidIntensity() {
  const saved = localStorage.getItem(LIQUID_INTENSITY_KEY) || 'medium';
  setLiquidIntensity(saved);
}

function trustLabel(tier) {
  if (tier === 'high') return 'High Trust';
  if (tier === 'medium') return 'Medium Trust';
  return 'Low Trust';
}

function renderSources(sources) {
  sourcesEl.innerHTML = '';
  if (!sources || sources.length === 0) {
    sourcesEl.innerHTML = '<div class="timeline-empty">No explicit source links were returned by the model for this run.</div>';
    return;
  }

  sources.forEach((source) => {
    const card = document.createElement('div');
    card.className = `source-card ${source.trust_tier || 'low'}`;

    const title = document.createElement('a');
    title.href = source.url;
    title.target = '_blank';
    title.rel = 'noopener noreferrer';
    title.textContent = source.title || source.url;
    title.className = 'source-title';

    const meta = document.createElement('div');
    meta.className = 'source-meta';
    meta.textContent = `${source.domain || ''} • ${trustLabel(source.trust_tier)}`;

    card.appendChild(title);
    card.appendChild(meta);
    sourcesEl.appendChild(card);
  });
}

function renderAgentPanel(tab) {
  const text = latestAgentPanels?.[tab] || 'No data.';
  agentPanelEl.textContent = text;
}

agentTabsEl?.addEventListener('click', (event) => {
  const btn = event.target.closest('.tab-btn');
  if (!btn) return;
  agentTabsEl.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
  btn.classList.add('active');
  renderAgentPanel(btn.dataset.tab);
});

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

function applyFinalData(data) {
  latestAnswer = data.answer || '';
  answerEl.textContent = latestAnswer;

  sessionId = data.session_id;
  localStorage.setItem(SESSION_KEY, sessionId);
  sessionEl.textContent = `Session ID: ${sessionId}\nMemory turns used: ${data.memory_used}`;

  verificationEl.textContent = data.verification_notes;
  confidenceEl.textContent = `Confidence: ${data.confidence}`;
  verifiedAtEl.textContent = `Verified at (UTC): ${data.verified_at_utc}`;
  modelEl.textContent = `Model: ${data.model}`;
  toolTraceEl.textContent = (data.tool_trace || []).join('\n') || 'No tools called.';
  renderSources(data.sources || []);

  latestAgentPanels = data.agent_panels || {};
  const activeTab = agentTabsEl.querySelector('.tab-btn.active')?.dataset.tab || 'retriever';
  renderAgentPanel(activeTab);

  refreshTimeline();
}

function runStream(question) {
  return new Promise((resolve, reject) => {
    const params = new URLSearchParams({ question });
    if (sessionId) params.set('session_id', sessionId);
    const es = new EventSource(`/ask_stream?${params.toString()}`);

    let streamed = '';

    es.addEventListener('status', (e) => {
      statusEl.textContent = e.data === 'researching' ? 'Researching and verifying with live web data...' : e.data;
      answerEl.textContent = '';
    });

    es.addEventListener('chunk', (e) => {
      try {
        const payload = JSON.parse(e.data || '{}');
        streamed += payload.text || '';
        answerEl.textContent = streamed;
        resultEl.classList.remove('hidden');
      } catch {
        // ignore malformed chunk
      }
    });

    es.addEventListener('final', (e) => {
      try {
        const data = JSON.parse(e.data || '{}');
        applyFinalData(data);
        statusEl.textContent = 'Done.';
        es.close();
        resolve();
      } catch (err) {
        es.close();
        reject(err);
      }
    });

    es.onerror = () => {
      es.close();
      reject(new Error('Streaming failed.'));
    };
  });
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const question = questionEl.value.trim();
  if (!question) return;

  submitBtn.disabled = true;
  resultEl.classList.add('hidden');

  try {
    await runStream(question);
  } catch (error) {
    statusEl.textContent = `Error: ${error.message}`;
  } finally {
    submitBtn.disabled = false;
  }
});

newSessionBtn.addEventListener('click', () => {
  sessionId = null;
  localStorage.removeItem(SESSION_KEY);
  statusEl.textContent = 'Started a new session.';
  timelineEl.innerHTML = '<div class="timeline-empty">No memory yet.</div>';
  resultEl.classList.add('hidden');
});

themeToggleBtn?.addEventListener('click', () => {
  const isDark = document.body.classList.contains('dark');
  setTheme(isDark ? 'light' : 'dark');
});

intensitySelect?.addEventListener('change', () => {
  setLiquidIntensity(intensitySelect.value);
});

copyAnswerBtn?.addEventListener('click', async () => {
  if (!latestAnswer) {
    statusEl.textContent = 'No answer to copy yet.';
    return;
  }
  try {
    await navigator.clipboard.writeText(latestAnswer);
    statusEl.textContent = 'Answer copied.';
  } catch {
    statusEl.textContent = 'Copy failed on this browser.';
  }
});

downloadAnswerBtn?.addEventListener('click', () => {
  if (!latestAnswer) {
    statusEl.textContent = 'No answer to download yet.';
    return;
  }
  const blob = new Blob([latestAnswer], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'assistant-answer.txt';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  statusEl.textContent = 'Answer downloaded.';
});

micBtn.addEventListener('click', () => {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    statusEl.textContent = 'Voice input is not supported in this browser.';
    return;
  }
  const rec = new Recognition();
  rec.lang = 'en-US';
  rec.interimResults = false;
  rec.maxAlternatives = 1;
  statusEl.textContent = 'Listening...';
  rec.onresult = (event) => {
    const text = event.results?.[0]?.[0]?.transcript || '';
    questionEl.value = text;
    statusEl.textContent = 'Voice captured.';
  };
  rec.onerror = () => {
    statusEl.textContent = 'Voice input error.';
  };
  rec.start();
});

speakBtn.addEventListener('click', () => {
  if (!latestAnswer) {
    statusEl.textContent = 'No answer to speak yet.';
    return;
  }
  if (!window.speechSynthesis) {
    statusEl.textContent = 'Speech output is not supported in this browser.';
    return;
  }
  const utterance = new SpeechSynthesisUtterance(latestAnswer);
  utterance.rate = 1;
  utterance.pitch = 1;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
});

refreshTimeline();
initTheme();
initLiquidIntensity();
