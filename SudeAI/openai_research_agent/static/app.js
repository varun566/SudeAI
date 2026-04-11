const form = document.getElementById('ask-form');
const questionEl = document.getElementById('question');
const submitBtn = document.getElementById('submit-btn');
const regenerateBtn = document.getElementById('regenerate-btn');
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
const exportSessionBtn = document.getElementById('export-session-btn');
const shareReportBtn = document.getElementById('share-report-btn');
const sourceSnapshotsEl = document.getElementById('source-snapshots');

const SESSION_KEY = 'live_ai_session_id';
const THEME_KEY = 'live_ai_theme';
const LIQUID_INTENSITY_KEY = 'live_ai_liquid_intensity';
let sessionId = localStorage.getItem(SESSION_KEY) || null;
let latestAnswer = '';
let latestAgentPanels = {};
let lastQuestion = '';
let latestPayload = null;
let activeRecognition = null;
let voiceRetryCount = 0;

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
    if (source.domain === 'news.google.com' && source.publisher_search_url) {
      const helper = document.createElement('a');
      helper.href = source.publisher_search_url;
      helper.target = '_blank';
      helper.rel = 'noopener noreferrer';
      helper.className = 'source-helper-link';
      helper.textContent = 'Open likely publisher';
      card.appendChild(helper);
    }
    sourcesEl.appendChild(card);
  });
}

function renderSourceSnapshots(snapshots) {
  sourceSnapshotsEl.innerHTML = '';
  if (!snapshots || snapshots.length === 0) {
    sourceSnapshotsEl.innerHTML = '<div class="timeline-empty">No source snapshots available.</div>';
    return;
  }
  snapshots.forEach((snap) => {
    const card = document.createElement('div');
    card.className = 'snapshot-card';

    const title = document.createElement('a');
    title.href = snap.url;
    title.target = '_blank';
    title.rel = 'noopener noreferrer';
    title.className = 'source-title';
    title.textContent = snap.title || snap.url;

    const meta = document.createElement('div');
    meta.className = 'source-meta';
    meta.textContent = snap.domain || '';

    const excerpt = document.createElement('div');
    excerpt.className = 'snapshot-excerpt';
    excerpt.textContent = snap.excerpt || '';

    card.appendChild(title);
    card.appendChild(meta);
    card.appendChild(excerpt);
    sourceSnapshotsEl.appendChild(card);
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
  latestPayload = data;
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
  renderSourceSnapshots(data.source_snapshots || []);

  latestAgentPanels = data.agent_panels || {};
  const activeTab = agentTabsEl.querySelector('.tab-btn.active')?.dataset.tab || 'retriever';
  renderAgentPanel(activeTab);

  refreshTimeline();
}

function runStream(question, options = {}) {
  return new Promise((resolve, reject) => {
    const params = new URLSearchParams({ question });
    if (sessionId) params.set('session_id', sessionId);
    if (options.strictSources) params.set('strict_sources', 'true');
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
  lastQuestion = question;

  submitBtn.disabled = true;
  if (regenerateBtn) regenerateBtn.disabled = true;
  resultEl.classList.add('hidden');

  try {
    await runStream(question, { strictSources: false });
  } catch (error) {
    statusEl.textContent = `Error: ${error.message}`;
  } finally {
    submitBtn.disabled = false;
    if (regenerateBtn) regenerateBtn.disabled = false;
  }
});

regenerateBtn?.addEventListener('click', async () => {
  const question = (questionEl.value || lastQuestion || '').trim();
  if (!question) {
    statusEl.textContent = 'Enter a question first.';
    return;
  }
  lastQuestion = question;
  submitBtn.disabled = true;
  regenerateBtn.disabled = true;
  resultEl.classList.add('hidden');
  statusEl.textContent = 'Running stricter retrieval mode...';
  try {
    await runStream(question, { strictSources: true });
  } catch (error) {
    statusEl.textContent = `Error: ${error.message}`;
  } finally {
    submitBtn.disabled = false;
    regenerateBtn.disabled = false;
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

exportSessionBtn?.addEventListener('click', async () => {
  if (!sessionId || !latestPayload) {
    statusEl.textContent = 'Run at least one question before exporting.';
    return;
  }
  try {
    const response = await fetch(`/history/${encodeURIComponent(sessionId)}`);
    const history = response.ok ? await response.json() : { messages: [] };
    const lines = [];
    lines.push('# Live AI Assistant Session Report');
    lines.push('');
    lines.push(`- Session ID: ${sessionId}`);
    lines.push(`- Exported (UTC): ${new Date().toISOString()}`);
    lines.push('');
    lines.push('## Latest Question');
    lines.push('');
    lines.push(questionEl.value.trim() || lastQuestion || '');
    lines.push('');
    lines.push('## Latest Answer');
    lines.push('');
    lines.push(latestPayload.answer || '');
    lines.push('');
    lines.push('## Verification');
    lines.push('');
    lines.push(`- Confidence: ${latestPayload.confidence || ''}`);
    lines.push(`- Verified at (UTC): ${latestPayload.verified_at_utc || ''}`);
    lines.push('');
    lines.push(latestPayload.verification_notes || '');
    lines.push('');
    lines.push('## Sources');
    lines.push('');
    (latestPayload.sources || []).forEach((s) => {
      lines.push(`- [${s.title || s.url}](${s.url})`);
    });
    lines.push('');
    lines.push('## Conversation Timeline');
    lines.push('');
    (history.messages || []).forEach((m) => {
      lines.push(`### ${String(m.role || 'assistant').toUpperCase()}`);
      lines.push('');
      lines.push(m.content || '');
      lines.push('');
    });

    const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `session-${sessionId}.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    statusEl.textContent = 'Session markdown exported.';
  } catch {
    statusEl.textContent = 'Export failed.';
  }
});

async function createPublicReport() {
  if (!sessionId || !latestPayload) {
    statusEl.textContent = 'Run at least one question before sharing.';
    return;
  }
  try {
    const historyRes = await fetch(`/history/${encodeURIComponent(sessionId)}`);
    const historyPayload = historyRes.ok ? await historyRes.json() : { messages: [] };
    const body = {
      session_id: sessionId,
      question: questionEl.value.trim() || lastQuestion || '',
      answer: latestPayload.answer || '',
      verification_notes: latestPayload.verification_notes || '',
      confidence: latestPayload.confidence || '',
      sources: latestPayload.sources || [],
      history: historyPayload.messages || [],
    };
    const response = await fetch('/reports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error('Share request failed');
    const data = await response.json();
    const fullUrl = `${window.location.origin}${data.public_url}`;
    try {
      await navigator.clipboard.writeText(fullUrl);
      statusEl.textContent = `Public report created and copied: ${fullUrl}`;
    } catch {
      statusEl.textContent = `Public report created: ${fullUrl}`;
    }
  } catch {
    statusEl.textContent = 'Public report creation failed.';
  }
}

shareReportBtn?.addEventListener('click', createPublicReport);

function stopVoiceInput() {
  if (activeRecognition) {
    try {
      activeRecognition.stop();
    } catch {}
  }
}

function startVoiceInput() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    statusEl.textContent = 'Voice input is not supported in this browser.';
    return;
  }
  const rec = new Recognition();
  activeRecognition = rec;
  rec.lang = 'en-US';
  rec.interimResults = false;
  rec.maxAlternatives = 1;
  statusEl.textContent = 'Listening...';
  rec.onresult = (event) => {
    const text = event.results?.[0]?.[0]?.transcript || '';
    questionEl.value = text;
    statusEl.textContent = 'Voice captured.';
    voiceRetryCount = 0;
  };
  rec.onerror = (event) => {
    const err = event?.error || 'unknown';
    if ((err === 'no-speech' || err === 'aborted') && voiceRetryCount < 1) {
      voiceRetryCount += 1;
      statusEl.textContent = 'Retrying voice input...';
      setTimeout(() => startVoiceInput(), 280);
      return;
    }
    statusEl.textContent = 'Voice input error.';
  };
  rec.onend = () => {
    activeRecognition = null;
  };
  rec.start();
}

micBtn?.addEventListener('mousedown', (e) => {
  e.preventDefault();
  startVoiceInput();
});
micBtn?.addEventListener('mouseup', stopVoiceInput);
micBtn?.addEventListener('mouseleave', stopVoiceInput);
micBtn?.addEventListener('touchstart', (e) => {
  e.preventDefault();
  startVoiceInput();
});
micBtn?.addEventListener('touchend', stopVoiceInput);

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
