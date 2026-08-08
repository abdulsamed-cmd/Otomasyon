'use strict';

const api = {
  async get(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error((await res.json()).error || res.statusText);
    return res.json();
  },
  async send(url, method, body) {
    const res = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 204) return null;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  },
};

const el = (id) => document.getElementById(id);

async function refreshHealth() {
  try {
    const health = await api.get('/api/health');
    const node = el('health');
    node.textContent = `● ${health.status}`;
    node.classList.add('ok');
  } catch {
    el('health').textContent = '● offline';
  }
}

function ruleCard(rule) {
  const card = document.createElement('div');
  card.className = 'rule-card' + (rule.enabled ? '' : ' disabled');
  card.dataset.id = rule.id;

  card.innerHTML = `
    <div class="rule-meta">
      <h3>${escapeHtml(rule.name)}</h3>
      <p>
        <span class="badge">${escapeHtml(rule.trigger)}</span>
        ${escapeHtml(rule.action)} · ${rule.runCount} run(s)
      </p>
    </div>
    <div class="rule-actions">
      <button class="btn-run" data-act="run" ${rule.enabled ? '' : 'disabled'}>Run</button>
      <button class="btn-toggle" data-act="toggle">${rule.enabled ? 'Disable' : 'Enable'}</button>
      <button class="btn-del" data-act="delete">Delete</button>
    </div>
  `;
  return card;
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[c]));
}

async function refreshRules() {
  const rules = await api.get('/api/rules');
  const container = el('rules');
  container.innerHTML = '';
  if (rules.length === 0) {
    container.innerHTML = '<p class="empty">No rules yet. Add one to get started.</p>';
    return;
  }
  rules.forEach((rule) => container.appendChild(ruleCard(rule)));
}

async function refreshRuns() {
  const runs = await api.get('/api/runs');
  const list = el('runs');
  list.innerHTML = '';
  if (runs.length === 0) {
    list.innerHTML = '<li class="empty">No runs recorded yet.</li>';
    return;
  }
  runs.forEach((run) => {
    const li = document.createElement('li');
    li.innerHTML = `<span>${escapeHtml(run.ruleName)} — ${escapeHtml(run.action)}</span>
      <span class="ok">${escapeHtml(run.status)} · ${new Date(run.ranAt).toLocaleTimeString()}</span>`;
    list.appendChild(li);
  });
}

async function refreshAll() {
  await Promise.all([refreshRules(), refreshRuns()]);
}

el('rule-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  el('form-error').textContent = '';
  const form = e.target;
  const payload = {
    name: form.name.value,
    trigger: form.trigger.value,
    action: form.action.value,
  };
  try {
    await api.send('/api/rules', 'POST', payload);
    form.reset();
    await refreshRules();
  } catch (err) {
    el('form-error').textContent = err.message;
  }
});

el('rules').addEventListener('click', async (e) => {
  const btn = e.target.closest('button');
  if (!btn) return;
  const card = e.target.closest('.rule-card');
  const id = Number(card.dataset.id);
  const act = btn.dataset.act;
  try {
    if (act === 'run') {
      await api.send(`/api/rules/${id}/run`, 'POST');
    } else if (act === 'toggle') {
      const disabling = !card.classList.contains('disabled');
      await api.send(`/api/rules/${id}`, 'PATCH', { enabled: !disabling });
    } else if (act === 'delete') {
      await api.send(`/api/rules/${id}`, 'DELETE');
    }
    await refreshAll();
  } catch (err) {
    el('form-error').textContent = err.message;
  }
});

refreshHealth();
refreshAll();
setInterval(refreshHealth, 10000);
