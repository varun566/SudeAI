const form = document.getElementById('ask-form');
const questionEl = document.getElementById('question');
const submitBtn = document.getElementById('submit-btn');
const statusEl = document.getElementById('status');
const resultEl = document.getElementById('result');
const answerEl = document.getElementById('answer');
const verificationEl = document.getElementById('verification');
const confidenceEl = document.getElementById('confidence');
const verifiedAtEl = document.getElementById('verified-at');
const modelEl = document.getElementById('model');
const sourcesEl = document.getElementById('sources');

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
      body: JSON.stringify({ question }),
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

    resultEl.classList.remove('hidden');
    statusEl.textContent = 'Done.';
  } catch (error) {
    statusEl.textContent = `Error: ${error.message}`;
  } finally {
    submitBtn.disabled = false;
  }
});
