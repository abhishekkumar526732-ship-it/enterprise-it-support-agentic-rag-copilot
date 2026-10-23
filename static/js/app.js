// ═══════════════════════════════════════════════════════════════
// Enterprise IT Support Copilot — Frontend (JWT/RBAC + Guardrails)
// ═══════════════════════════════════════════════════════════════

// ───────────────────── Auth state ───────────────────────────────
let accessToken = localStorage.getItem('access_token') || '';
let refreshToken = localStorage.getItem('refresh_token') || '';

const loginScreen  = document.getElementById('loginScreen');
const appShell     = document.getElementById('appShell');
const loginForm    = document.getElementById('loginForm');
const loginError   = document.getElementById('loginError');
const currentUser  = document.getElementById('currentUser');
const currentRole  = document.getElementById('currentRole');
const logoutBtn    = document.getElementById('logoutBtn');

function authHeaders() {
  return { 'Authorization': `Bearer ${accessToken}`, 'Content-Type': 'application/json' };
}

function showApp() {
  loginScreen.classList.add('hidden');
  appShell.classList.remove('hidden');
}

function showLogin() {
  loginScreen.classList.remove('hidden');
  appShell.classList.add('hidden');
  accessToken = '';
  refreshToken = '';
  localStorage.removeItem('access_token');
  localStorage.removeItem('refresh_token');
}

async function loadUserInfo() {
  try {
    const res = await fetch('/api/auth/me', { headers: authHeaders() });
    if (!res.ok) throw new Error('Session expired');
    const data = await res.json();
    currentUser.textContent = data.username;
    currentRole.textContent = data.role.toUpperCase();
    currentRole.className = `role-badge role-${data.role}`;
    showApp();
  } catch {
    showLogin();
  }
}

loginForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  loginError.textContent = '';
  const username = document.getElementById('loginUser').value.trim();
  const password = document.getElementById('loginPass').value;
  try {
    const res = await fetch('/api/auth/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Login failed');
    accessToken = data.access_token;
    refreshToken = data.refresh_token;
    localStorage.setItem('access_token', accessToken);
    localStorage.setItem('refresh_token', refreshToken);
    await loadUserInfo();
  } catch (err) {
    loginError.textContent = err.message;
  }
});

logoutBtn.addEventListener('click', showLogin);

// Auto-login if token exists
if (accessToken) {
  loadUserInfo();
} else {
  showLogin();
}

// ───────────────────── Chat logic ───────────────────────────────
const chat       = document.getElementById('chat');
const form       = document.getElementById('chatForm');
const question   = document.getElementById('question');
const trace      = document.getElementById('trace');
const sourceUsed = document.getElementById('sourceUsed');
const guardrailStatus = document.getElementById('guardrailStatus');

function escapeHtml(s = '') {
  return s.replace(/[&<>'"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
}
function formatText(s = '') { return escapeHtml(s).replace(/\n/g, '<br>'); }

function addMessage(role, text, source = '', citations = [], guardrailBlocked = false) {
  const wrap = document.createElement('div');
  wrap.className = `message ${role}`;
  const citeHtml = citations.length
    ? `<div class="citations"><strong>Sources</strong><br>${citations.map(c => c.url ? `<a href="${escapeHtml(c.url)}" target="_blank" rel="noopener">${escapeHtml(c.title)}</a>` : escapeHtml(c.title)).join('<br>')}</div>`
    : '';
  const guardBadge = guardrailBlocked
    ? '<div class="guardrail-badge blocked">⛔ Blocked by Guardrails</div>'
    : '';
  wrap.innerHTML = `<div class="avatar">AI</div><div class="bubble">${guardBadge}${formatText(text)}${source ? `<div class="answer-source">Source: ${escapeHtml(source)}</div>` : ''}${citeHtml}</div>`;
  chat.appendChild(wrap);
  chat.scrollTop = chat.scrollHeight;
}

function renderTrace(items = []) {
  trace.innerHTML = items.length
    ? items.map(x => {
        let cls = 'trace-item';
        if (x.includes('BLOCKED')) cls += ' trace-blocked';
        else if (x.includes('PASSED')) cls += ' trace-passed';
        return `<div class="${cls}">${escapeHtml(x)}</div>`;
      }).join('')
    : '<div class="empty">No trace.</div>';
}

async function askAgent(q) {
  addMessage('user', q);
  question.value = '';
  renderTrace(['Running LangGraph workflow...']);
  sourceUsed.textContent = 'Running';
  guardrailStatus.textContent = 'Checking…';
  guardrailStatus.className = 'guardrail-pending';
  const btn = form.querySelector('button');
  btn.disabled = true;
  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ question: q }),
    });
    if (res.status === 401) { showLogin(); return; }
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Request failed');

    const blocked = data.guardrail_blocked || false;
    addMessage('assistant', data.answer, data.source_used, data.citations || [], blocked);
    renderTrace(data.trace || []);
    sourceUsed.textContent = data.source_used;
    guardrailStatus.textContent = blocked ? '⛔ BLOCKED' : '✅ PASSED';
    guardrailStatus.className = blocked ? 'guardrail-blocked' : 'guardrail-ok';
  } catch (e) {
    addMessage('assistant', `Error: ${e.message}`);
    renderTrace(['Request failed']);
    sourceUsed.textContent = 'Error';
    guardrailStatus.textContent = '—';
  } finally {
    btn.disabled = false;
  }
}

form.addEventListener('submit', e => { e.preventDefault(); const q = question.value.trim(); if (q) askAgent(q); });
document.querySelectorAll('.example').forEach(b => b.addEventListener('click', () => askAgent(b.textContent.trim())));

// ───────────────────── Upload logic ─────────────────────────────
const modal = document.getElementById('uploadModal');
document.getElementById('openUpload').onclick = () => modal.classList.remove('hidden');
document.getElementById('closeUpload').onclick = () => modal.classList.add('hidden');
document.getElementById('uploadBtn').onclick = async () => {
  const file = document.getElementById('fileInput').files[0];
  const status = document.getElementById('uploadStatus');
  if (!file) { status.textContent = 'Choose a file first.'; return; }
  status.textContent = 'Indexing document...';
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch('/api/ingest', {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${accessToken}` },
      body: fd,
    });
    if (r.status === 401) { showLogin(); return; }
    if (r.status === 403) { status.textContent = 'Admin role required for document ingestion.'; return; }
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Upload failed');
    status.textContent = `Indexed ${d.file}: ${d.chunks} chunks.`;
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  }
};
