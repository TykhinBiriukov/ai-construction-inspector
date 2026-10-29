'use strict';
const backlogStatuses = {planned:'Planned', in_progress:'In progress', done:'Done', not_needed:'Not needed'};
let backlogItems = [], backlogEditing = null, backlogDirty = false, backlogSaving = false, backlogLoading = false;
const backlogReady = value => ['done', 'not_needed'].includes(value);
function backlogRoute() {
  const active = location.hash === '#backlog';
  $('control-view').hidden = active; $('backlog-view').hidden = !active;
  $('nav-control').classList.toggle('active', !active); $('nav-backlog').classList.toggle('active', active);
  $('nav-control').setAttribute('aria-current', active ? 'false' : 'page');
  $('nav-backlog').setAttribute('aria-current', active ? 'page' : 'false');
  $('page-title').textContent = active ? 'Backlog' : 'Control room';
  $('page-subtitle').textContent = active ? 'Web and backend. Shared progress, explicit handoffs.' : 'Assign a capture. Follow the evidence.';
  if (active && token) refreshBacklog();
}
async function refreshBacklog() {
  if (!token || backlogLoading) return;
  const epoch = generation; backlogLoading = true;
  try {
    const result = await api('/v1/operator/backlog');
    if (epoch !== generation) return;
    backlogItems = result.items; renderBacklog();
    $('backlog-state').textContent = `Shared with the team · Updated ${new Date().toLocaleTimeString()}`;
  } catch (error) { if (epoch === generation) $('backlog-state').textContent = `Could not refresh: ${error.message}`; }
  finally { backlogLoading = false; }
}
function resetBacklog() {
  backlogItems = []; backlogEditing = null; backlogDirty = false;
  $('backlog-list').replaceChildren(); $('backlog-form').reset(); $('backlog-editor').hidden = true;
}
function renderBacklog() {
  const filter = $('backlog-filter').value;
  const rows = backlogItems.filter(item => {
    if (filter === 'archived') return item.archived;
    if (item.archived) return false;
    if (filter === 'web') return !backlogReady(item.web_status);
    if (filter === 'backend') return !backlogReady(item.backend_status);
    if (filter === 'done') return backlogReady(item.web_status) && backlogReady(item.backend_status);
    return true;
  });
  $('backlog-list').replaceChildren();
  for (const item of rows) {
    const card = document.createElement('article'); card.className = 'backlog-card';
    const title = document.createElement('h3'); title.textContent = item.title; card.append(title);
    const statuses = document.createElement('p');
    statuses.textContent = `Web: ${backlogStatuses[item.web_status]} · Backend: ${backlogStatuses[item.backend_status]}`; card.append(statuses);
    const needs = [];
    if (!backlogReady(item.web_status)) needs.push('Web');
    if (!backlogReady(item.backend_status)) needs.push('Backend');
    const badge = document.createElement('span'); badge.className = 'badge';
    badge.textContent = needs.length ? `Needs work: ${needs.join(' + ')}` : 'Both sides ready'; card.append(badge);
    if (item.description) { const detail = document.createElement('p'); detail.className = 'backlog-text'; detail.textContent = item.description; card.append(detail); }
    const by = document.createElement('small'); by.textContent = `Updated by ${item.updated_by} · ${date(item.updated_at)}`; card.append(by);
    const edit = document.createElement('button'); edit.type = 'button'; edit.className = 'secondary'; edit.textContent = 'Details / edit';
    edit.addEventListener('click', () => openBacklog(item)); card.append(edit); $('backlog-list').append(card);
  }
  if (!rows.length) $('backlog-list').textContent = 'No features in this view.';
}
function openBacklog(item = null, replace = false) {
  if (backlogSaving) return;
  if (backlogDirty && !replace && !confirm('Discard the unsaved draft?')) return;
  backlogEditing = item ? {...item} : {item_id:uuid(), revision:0};
  $('backlog-form').reset();
  for (const field of ['title','description','contract','web_notes','backend_notes']) $('bl-' + field.replaceAll('_','-')).value = item?.[field] || '';
  for (const side of ['web','backend']) $('bl-' + side + '-status').value = item?.[side + '_status'] || 'planned';
  $('bl-author').value = item?.updated_by || ''; $('bl-archived').checked = !!item?.archived;
  $('backlog-editor-title').textContent = item ? 'Edit feature' : 'New feature';
  $('backlog-save-state').textContent = ''; $('backlog-reload').hidden = true; $('backlog-editor').hidden = false;
  backlogDirty = false; $('backlog-editor').scrollIntoView({behavior:'smooth', block:'start'});
}
for (const side of ['web','backend']) {
  for (const [value,label] of Object.entries(backlogStatuses)) {
    const option = document.createElement('option'); option.value = value; option.textContent = label;
    $('bl-' + side + '-status').append(option);
  }
}
$('backlog-form').addEventListener('input', () => { backlogDirty = true; });
$('backlog-form').addEventListener('submit', async event => {
  event.preventDefault(); if (backlogSaving || !backlogEditing) return;
  const epoch = generation, id = backlogEditing.item_id;
  const body = {revision:backlogEditing.revision, archived:$('bl-archived').checked, updated_by:$('bl-author').value.trim()};
  for (const field of ['title','description','contract','web_status','backend_status','web_notes','backend_notes']) body[field] = $('bl-' + field.replaceAll('_','-')).value.trim();
  backlogSaving = true; for (const control of $('backlog-form').elements) control.disabled = true;
  try {
    const saved = await api(`/v1/operator/backlog/${encodeURIComponent(id)}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    if (epoch !== generation) return;
    backlogEditing = saved; backlogDirty = false; $('backlog-save-state').textContent = 'Saved for the team.'; $('backlog-reload').hidden = true;
    await refreshBacklog();
  } catch (error) {
    if (epoch !== generation) return;
    $('backlog-save-state').textContent = error.message; $('backlog-reload').hidden = error.status !== 409;
  } finally { backlogSaving = false; for (const control of $('backlog-form').elements) control.disabled = false; }
});
$('backlog-reload').addEventListener('click', async () => {
  if (!confirm('Replace your draft with the latest saved version?')) return;
  await refreshBacklog();
  const item = backlogItems.find(i => i.item_id === backlogEditing?.item_id);
  if (item) openBacklog(item, true);
});
$('backlog-new').addEventListener('click', () => openBacklog());
$('backlog-close').addEventListener('click', () => {
  if (backlogSaving || (backlogDirty && !confirm('Discard the unsaved draft?'))) return;
  backlogDirty = false; $('backlog-editor').hidden = true;
});
$('backlog-filter').addEventListener('change', renderBacklog);
window.addEventListener('hashchange', backlogRoute);
window.addEventListener('beforeunload', event => { if (backlogDirty) { event.preventDefault(); event.returnValue = ''; } });
backlogRoute();
