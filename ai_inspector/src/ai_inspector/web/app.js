'use strict';
const $ = id => document.getElementById(id);
const AUTH_STORAGE_KEY = 'inspector-operator-session';
function savedToken() { try { return sessionStorage.getItem(AUTH_STORAGE_KEY) || ''; } catch { return ''; } }
function saveToken(value) {
  try { if (value) sessionStorage.setItem(AUTH_STORAGE_KEY, value); else sessionStorage.removeItem(AUTH_STORAGE_KEY); } catch { /* Storage may be disabled. */ }
}
function unlock() {
  $('token').value = ''; $('login').hidden = true; $('workspace').hidden = false;
  $('logout').hidden = false; $('refresh').disabled = false; fields();
}
let token = '', generation = 0, overview, capabilities, selected = null, sessions = [], nextBefore = null;
let pending = null, busy = false, polling = false;
const aborting = new Set();
let abortCandidate = null;
let agentChat = null, agentPending = null, agentCreate = null, agentSending = false;
const pretty = value => JSON.stringify(value, null, 2);
const date = value => value ? new Date(value).toLocaleString() : 'Never';
function notice(message = '') { $('notice').textContent = message; $('notice').hidden = !message; }
function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, c => (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16));
}
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {...options.headers, Authorization: `Bearer ${token}`}, signal: AbortSignal.timeout(60000)});
  if (!response.ok) {
    let body; try { body = await response.json(); } catch { body = {}; }
    if (response.status === 401) lock();
    const details = (body.details?.errors || []).map(item => `${item.location?.filter(x => x !== 'body').join('.')}: ${item.message}`).join('; ');
    const error = new Error(`${response.status} · ${body.message || response.statusText}${body.code ? ` (${body.code})` : ''}${details ? ` · ${details}` : ''}`);
    error.status = response.status; error.code = body.code;
    throw error;
  }
  return options.blob ? response.blob() : response.json();
}
function lock() {
  if (typeof resetBacklog === 'function') resetBacklog();
  saveToken(''); token = ''; generation++; selected = null; sessions = []; overview = null; capabilities = null;
  $('token').value = ''; $('login').hidden = false; $('workspace').hidden = true;
  $('logout').hidden = true; $('refresh').disabled = true; $('connection').textContent = 'Locked';
  $('session-json').textContent = ''; $('geometry-result').textContent = ''; $('inspection-json').textContent = '';
  $('detail').hidden = true; abortCandidate = null; $('abort-dialog').close();
  agentChat = null; $('agent-runs').replaceChildren(); $('agent-room').value = ''; $('agent-text').value = '';
  // Retain the pending request ID in this tab to allow safe retry after reauthentication.
}
function fields() {
  const task = $('task').value;
  $('inspection-field').hidden = task !== 'existing'; $('file-field').hidden = task !== 'upload';
  $('prompt-field').hidden = task === 'existing'; $('prompt').required = task !== 'existing';
  $('bim').required = task === 'upload'; $('inspection').required = task === 'existing';
  $('input-mode').disabled = task === 'upload'; $('parent').disabled = task !== 'existing';
  $('parent-hint').hidden = task === 'existing';
  $('submit').textContent = pending ? 'Retry pending request →' : task === 'upload' ? 'Upload reference BIM →' : 'Create assignment →';
  $('pending-hint').hidden = !pending; $('abandon').hidden = !pending;
}
function renderSessions() {
  const body = $('sessions'); body.replaceChildren(); $('empty').hidden = sessions.length > 0;
  for (const session of sessions) {
    const row = document.createElement('tr'); row.classList.toggle('selected', session.session_id === selected);
    const identity = document.createElement('td'); const button = document.createElement('button');
    button.textContent = session.room_id; button.addEventListener('click', () => showSession(session.session_id).catch(e => notice(e.message)));
    const id = document.createElement('small'); id.textContent = session.session_id.slice(0, 8);
    identity.append(button, id); row.append(identity);
    for (const value of [session.capture_state, session.processing_state, session.input_mode, date(session.created_at)]) {
      const cell = document.createElement('td'); cell.textContent = value.replaceAll('_', ' '); row.append(cell);
    }
    const actions = document.createElement('td');
    const abort = document.createElement('button'); abort.className = 'secondary danger';
    abort.textContent = aborting.has(session.session_id) ? 'Aborting…' : 'Abort';
    abort.disabled = aborting.has(session.session_id) || ['completed', 'failed', 'cancelled'].includes(session.processing_state);
    abort.setAttribute('aria-label', `Abort ${session.room_id} · ${session.session_id.slice(0, 8)}`);
    abort.addEventListener('click', () => { abortCandidate = session; $('abort-target').textContent = `${session.room_id} · ${session.session_id.slice(0, 8)}`; $('abort-dialog').showModal(); }); actions.append(abort); row.append(actions);
    body.append(row);
  }
  $('more').hidden = !nextBefore;
}
async function abortSession(session) {
  const id = session.session_id;
  if (aborting.has(id)) return;
  const epoch = generation; aborting.add(id); renderSessions(); notice();
  try {
    const updated = await api(`/v1/sessions/${encodeURIComponent(id)}/abort`, {method:'POST'});
    if (epoch !== generation) return;
    sessions = sessions.map(item => item.session_id === id ? updated : item);
    notice('Session aborted. Uploaded files and history are preserved.');
    if (selected === id) await showSession(id, false);
  } catch (error) {
    if (epoch === generation) notice(`Abort was not confirmed: ${error.message}. Refresh or retry safely.`);
  } finally { aborting.delete(id); if (epoch === generation) renderSessions(); }
}
function renderOverview(data, caps, device) {
  overview = data; capabilities = caps;
  $('server-state').textContent = 'Operational'; $('project').textContent = data.project_id;
  $('device-state').textContent = device.online ? 'Online' : 'Offline';
  $('device-info').textContent = `${device.device_id} · Last seen: ${date(device.last_seen_at)}`;
  $('geometry-state').textContent = caps.geometry_mode === 'mock' ? 'Mock / synthetic' : caps.geometry_mode === 'external' ? 'External configured' : 'Not configured';
  $('geometry-info').textContent = caps.geometry_mode === 'mock' ? 'Test output · no measured reconstruction' : caps.geometry_configured ? 'Reachability is reported by individual jobs' : 'Verified work will remain queued';
  $('dispatch-hint').textContent = data.supabase_configured ? 'Supabase signed uploads configured.' : 'Supabase is not configured for phone ZIP uploads.';
  $('device').value = data.device_id; $('bim').dataset.maxBytes = data.max_bim_bytes;
  const current = $('inspection').value; $('inspection').replaceChildren();
  for (const item of data.inspections) {
    const option = document.createElement('option'); option.value = item.inspection_id;
    option.textContent = `${item.room_id} · ${item.inspection_kind} · ${item.inspection_id.slice(0, 8)}`;
    $('inspection').append(option);
  }
  if (data.inspections.some(i => i.inspection_id === current)) $('inspection').value = current;
  $('connection').textContent = 'Connected'; $('updated').textContent = `Updated ${new Date().toLocaleTimeString()} · every 5s`;
}
async function refresh() {
  if (!token || polling) return;
  polling = true; const epoch = generation;
  try {
    const [data, caps] = await Promise.all([api('/v1/operator/overview'), api('/v1/capabilities')]);
    const device = await api(`/v1/devices/${encodeURIComponent(data.device_id)}/status`);
    if (epoch !== generation) return;
    renderOverview(data, caps, device);
    // Keep loaded older pages visible while refreshing the newest page.
    const latest = new Map(data.sessions.map(s => [s.session_id, s]));
    const older = sessions.filter(s => !latest.has(s.session_id));
    sessions = [...data.sessions, ...older];
    if (!sessions.length || older.length === 0) nextBefore = data.next_before;
    renderSessions();
    if (location.hash === '#backlog' && typeof refreshBacklog === 'function') await refreshBacklog();
    await refreshAgent();
    if (selected) await showSession(selected, false);
  } catch (error) {
    if (epoch === generation) { $('connection').textContent = 'Connection issue'; notice(`Refresh failed: ${error.message}. Displayed data may be stale.`); }
    throw error;
  } finally { polling = false; }
}
async function showSession(id, scroll = true) {
  const epoch = generation; selected = id;
  const session = await api(`/v1/sessions/${encodeURIComponent(id)}`);
  const inspection = await api(`/v1/inspections/${session.inspection_id}`);
  if (epoch !== generation || selected !== id) return;
  const index = sessions.findIndex(s => s.session_id === id); if (index >= 0) sessions[index] = session;
  $('detail').hidden = false; $('detail-title').textContent = `${session.room_id} · ${id.slice(0, 8)}`;
  $('session-json').textContent = pretty(session); $('geometry-result').textContent = session.geometry_result ? pretty(session.geometry_result) : 'No geometry result yet.';
  $('inspection-json').textContent = pretty(inspection); $('inspection-info').textContent = inspection.bim_model ? `${inspection.bim_model.filename} · stored, unvalidated` : 'Capture only · no reference BIM';
  $('download-bim').hidden = !inspection.bim_model;
  $('download-bim').onclick = () => downloadBim(inspection).catch(e => notice(e.message));
  $('session-error').hidden = !session.error; $('session-error').textContent = session.error ? pretty(session.error) : '';
  const resultMode = session.geometry_result?.analysis_origin || session.geometry_job?.analysis_origin || capabilities?.geometry_mode;
  $('result-note').textContent = `${session.processing_scope} · ${resultMode === 'mock' ? 'MOCK OUTPUT: synthetic geometry, no measured reconstruction.' : 'Geometry output only; no inspection verdict or BIM comparison.'}`;
  $('timeline').replaceChildren();
  const steps = [['Assigned', true], ['Acknowledged', !!session.device_acknowledged_at], ['Capture', ['capturing','submitted','sealed'].includes(session.capture_state)], ['Source verified', !!session.source], ['Geometry', !!session.geometry_result]];
  for (const [label, done] of steps) { const item = document.createElement('span'); item.textContent = `${done ? '✓' : '○'} ${label}`; item.classList.toggle('done', done); $('timeline').append(item); }
  $('export').onclick = () => download(new Blob([pretty(session)], {type:'application/json'}), `session-${id}.json`);
  renderSessions(); if (scroll) $('detail').scrollIntoView({behavior:'smooth', block:'start'});
}
function download(blob, name) { const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
async function downloadBim(inspection) { download(await api(`/v1/inspections/${inspection.inspection_id}/bim-model`, {blob:true}), inspection.bim_model.filename); }
$('abort-confirm').addEventListener('click', () => {
  const session = abortCandidate; abortCandidate = null; $('abort-dialog').close();
  if (session) abortSession(session);
});
$('abort-dismiss').addEventListener('click', () => { abortCandidate = null; $('abort-dialog').close(); });
$('abort-dialog').addEventListener('cancel', () => { abortCandidate = null; });
$('login-form').addEventListener('submit', async event => {
  event.preventDefault(); if (polling) return; token = $('token').value.trim(); generation++; notice();
  const button = event.submitter; button.disabled = true;
  try { await refresh(); if (token) { saveToken(token); unlock(); } }
  catch (e) { token = ''; saveToken(''); notice(`Could not connect: ${e.message}`); }
  finally { button.disabled = false; }
});
$('logout').addEventListener('click', lock);
$('refresh').addEventListener('click', () => { notice(); refresh().catch(() => {}); });
$('task').addEventListener('change', fields);
$('inspection').addEventListener('change', () => { const item = overview.inspections.find(i => i.inspection_id === $('inspection').value); if (item) $('room').value = item.room_id; });
$('more').addEventListener('click', async () => {
  const epoch = generation; $('more').disabled = true;
  try { const page = await api(`/v1/operator/overview?before=${nextBefore}`); if (epoch !== generation) return; const ids = new Set(sessions.map(s => s.session_id)); sessions.push(...page.sessions.filter(s => !ids.has(s.session_id))); nextBefore = page.next_before; renderSessions(); }
  catch (e) { notice(e.message); } finally { $('more').disabled = false; }
});
function pendingControls(disabled) {
  for (const element of $('capture-form').elements) if (!['submit', 'abandon'].includes(element.id)) element.disabled = disabled;
  if (!disabled) { $('device').readOnly = true; fields(); }
}
$('abandon').addEventListener('click', () => {
  if (!confirm('The server may have accepted this request. Check sessions or existing inspections before creating another. Discard this retry?')) return;
  pending = null; pendingControls(false); fields();
});
$('capture-form').addEventListener('submit', async event => {
  event.preventDefault(); if (busy) return; notice();
  if (!pending) {
    const task = $('task').value; const requestId = uuid();
    const body = {schema_version:'0.1', request_id:requestId, device_id:overview.device_id, room_id:$('room').value, capture_origin:$('input-mode').value === 'synthetic_fixture' ? 'synthetic' : 'device', input_mode:$('input-mode').value, parent_session_id:task === 'existing' ? $('parent').value.trim() || null : null};
    if (body.parent_session_id && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(body.parent_session_id)) { notice('Parent session must be a UUID.'); return; }
    if (task === 'upload') {
      const file = $('bim').files[0]; if (!file || !file.size || file.size > overview.max_bim_bytes) { notice(`Select a nonempty BIM file up to ${Math.floor(overview.max_bim_bytes / 1048576)} MB.`); return; }
      const form = new FormData(); for (const [key,value] of Object.entries({schema_version:'0.1',request_id:requestId,project_id:overview.project_id,room_id:body.room_id,prompt:$('prompt').value,model_is_synthetic:String($('synthetic-bim').checked)})) form.append(key,value);
      form.append('bim_model', file); pending = {task, path:'/v1/inspections', options:{method:'POST',body:form}};
    } else {
      if (task === 'existing' && !$('inspection').value) { notice('Upload a reference BIM or create a capture-only inspection first.'); return; }
      if (task === 'capture') body.prompt = $('prompt').value;
      pending = {task,path:task === 'existing' ? `/v1/inspections/${$('inspection').value}/capture-sessions` : '/v1/capture-sessions',options:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}};
    }
  }
  busy = true; $('submit').disabled = true; $('abandon').disabled = true; pendingControls(true); fields();
  try {
    const result = await api(pending.path, pending.options); const task = pending.task;
    pending = null; pendingControls(false); fields(); await refresh();
    if (task === 'upload') { $('task').value = 'existing'; $('inspection').value = result.inspection_id; fields(); notice('Reference BIM stored. Create an assignment for this inspection when ready.'); }
    else { notice('Assignment created. Waiting for the device.'); await showSession(result.session_id); }
  } catch (e) { notice(`${e.message}${pending ? ' · Retry the pending request to reuse its original operation ID.' : ''}`); }
  finally { busy = false; $('submit').disabled = false; $('abandon').disabled = false; fields(); }
});
function agentKey() { return `inspector-chat:${overview.project_id}`; }
function rememberChat(value) { try { localStorage.setItem(agentKey(), JSON.stringify(value)); } catch { /* Server history remains authoritative. */ } }
function renderAgent() {
  const history = $('agent-runs'); history.replaceChildren();
  const append = (label, text, style) => {
    const item = document.createElement('div'); item.className = `chat-message ${style}`;
    const author = document.createElement('strong'); author.textContent = label;
    const content = document.createElement('span'); content.textContent = text; item.append(author, content); history.append(item);
  };
  for (const run of agentChat?.runs || []) {
    append('You', run.text, 'user');
    if (run.reply) append('Inspector', run.reply, 'assistant');
    else append('Inspector', run.error?.message || 'Request saved. Waiting for the agent response.', 'pending');
  }
  const last = agentChat?.runs?.at(-1);
  const unfinished = last && !['completed', 'failed'].includes(last.status);
  if (unfinished) agentPending = {schema_version:'0.1', request_id:last.request_id, text:last.text};
  else if (last && agentPending?.request_id === last.request_id) agentPending = null;
  $('agent-state').textContent = capabilities?.agent_model_configured ? 'Muse Spark 1.3 · configured' : 'Model access needed';
  $('agent-hint').textContent = capabilities?.agent_model_configured ? 'Ask a question or request a capture. An assignment waits for the phone to connect and acknowledge.' : 'An administrator needs to configure Meta Model API access on the server. Your request can be saved and resumed.';
  $('agent-room').readOnly = !!agentChat || !!agentCreate;
  if (agentChat) $('agent-room').value = agentChat.room_id;
  $('agent-text').disabled = agentSending || !!agentPending;
  $('agent-send').disabled = agentSending || !!agentPending;
  $('agent-new').disabled = agentSending || !!agentPending;
  $('agent-retry').hidden = !agentPending;
  $('agent-retry').disabled = agentSending;
  $('agent-progress').textContent = agentSending ? 'Request saved as it is received. You can resume after a connection issue.' : unfinished ? 'This message is pending. Resume it to keep the same request and session.' : 'Messages and sessions are saved on the server.';
}
async function refreshAgent() {
  if (agentSending || !token || !overview) return;
  if (!agentChat && !agentCreate) {
    try {
      const saved = JSON.parse(localStorage.getItem(agentKey()) || 'null');
      if (saved?.chat_id) agentChat = {chat_id:saved.chat_id};
      else if (saved?.create_request) { agentCreate = saved.create_request; $('agent-room').value = agentCreate.room_id; }
    } catch { /* A malformed local pointer does not change server state. */ }
  }
  if (agentChat) {
    const epoch = generation, id = agentChat.chat_id;
    const result = await api(`/v1/chats/${encodeURIComponent(id)}`);
    if (epoch !== generation || agentChat?.chat_id !== id) return;
    agentChat = result;
  }
  renderAgent();
}
async function sendAgent() {
  if (agentSending || !token) return;
  if (!agentPending && !agentCreate && !$('agent-form').reportValidity()) return;
  if (!agentPending && !$('agent-text').value.trim()) { notice('Enter a message before sending.'); return; }
  const epoch = generation; agentSending = true; notice();
  if (!agentPending) agentPending = {schema_version:'0.1', request_id:uuid(), text:$('agent-text').value};
  const message = agentPending;
  renderAgent();
  try {
    if (!agentChat) {
      if (!agentCreate) agentCreate = {schema_version:'0.1', request_id:uuid(), room_id:$('agent-room').value};
      rememberChat({create_request:agentCreate});
      const created = await api('/v1/chats', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(agentCreate)});
      if (epoch !== generation) return;
      agentChat = created; agentCreate = null; rememberChat({chat_id:created.chat_id});
    }
    const result = await api(`/v1/chats/${agentChat.chat_id}/messages`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(message)});
    if (epoch !== generation) return;
    if (['completed', 'failed'].includes(result.status)) { agentPending = null; $('agent-text').value = ''; }
    if (result.error) notice(result.error.message);
  } catch (error) {
    if (epoch === generation) {
      if (error.status === 422 && error.code === 'validation_error') {
        // FastAPI rejected the body before executing the handler: editing is safe.
        $('agent-text').value = message.text;
        agentPending = null;
        if (!agentChat) { agentCreate = null; rememberChat(null); }
        notice(`Request was not accepted. Correct the fields and send again: ${error.message}`);
      } else {
        notice(`Request confirmation is pending: ${error.message}. Resume the saved request.`);
      }
    }
  } finally {
    agentSending = false;
    if (epoch === generation) { renderAgent(); await refresh().catch(() => {}); }
  }
}
$('agent-form').addEventListener('submit', event => { event.preventDefault(); sendAgent(); });
$('agent-retry').addEventListener('click', sendAgent);
$('agent-new').addEventListener('click', () => {
  if (agentSending || agentPending) return;
  agentChat = null; agentCreate = null; rememberChat(null); $('agent-room').value = ''; $('agent-text').value = ''; renderAgent();
});
window.addEventListener('beforeunload', event => { if (pending || agentPending) { event.preventDefault(); event.returnValue = ''; } });
setInterval(() => { if (token && !document.hidden && !busy) refresh().catch(() => {}); }, 5000);
fields();

// Keep authentication through reloads in this tab; logout/401 clears it.
(async function restoreLogin() {
  const saved = savedToken();
  if (!saved) return;
  token = saved; generation++; unlock(); $('connection').textContent = 'Reconnecting…';
  try { await refresh(); } catch { /* Transient errors keep the session for the next refresh. 401 calls lock(). */ }
})();
