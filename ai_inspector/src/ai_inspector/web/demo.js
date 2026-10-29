'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const authKey = 'inspector-operator-session', pointerKey = 'inspector-demo-chat', pendingKey = 'inspector-demo-pending';
  const read = (store, key) => { try { return store.getItem(key); } catch { return null; } };
  const write = (store, key, value) => { try { if (value == null) store.removeItem(key); else store.setItem(key,value); } catch {} };
  let token = read(sessionStorage, authKey) || '', epoch = 0, overview, caps, device, chat = null, pending = null, create = null;
  let scopedSessions = [], scopedInspections = [];
  let sending = false, refreshing = false, selected = null, historySignature = '';
  const uid = () => crypto.randomUUID();
  const timestamp = value => value ? new Date(value).toLocaleString() : 'Not reported';
  const node = (tag, className, text) => { const el = document.createElement(tag); if (className) el.className=className; if (text != null) el.textContent=String(text); return el; };
  function messageText(text) {
    const el=node('div','message-text');
    for(const part of String(text).replace(/\n{3,}/g,'\n\n').split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g)) {
      if(part.startsWith('`') && part.endsWith('`')) el.append(node('code','',part.slice(1,-1)));
      else if(part.startsWith('**') && part.endsWith('**')) el.append(node('strong','',part.slice(2,-2)));
      else el.append(document.createTextNode(part));
    }
    return el;
  }
  function notify(text='') { $('notice').textContent=text; $('notice').hidden=!text; }
  function remember() {
    write(localStorage,pointerKey,chat?.chat_id ? JSON.stringify({chat_id:chat.chat_id, project_id:overview?.project_id}) : null);
    write(sessionStorage,pendingKey,pending || create ? JSON.stringify({pending,create,chat_id:chat?.chat_id,project_id:overview?.project_id}) : null);
  }
  async function api(path, options={}) {
    const response=await fetch(path,{...options,headers:{...options.headers,Authorization:`Bearer ${token}`},signal:AbortSignal.timeout(60000)});
    let body; try { body=await response.json(); } catch { body={}; }
    if (!response.ok) { if(response.status===401) lock(); const error=new Error(body.message || `Request failed (${response.status})`); error.status=response.status; error.code=body.code; throw error; }
    return body;
  }
  function lock() {
    epoch++; token=''; write(sessionStorage,authKey,null); chat=null; pending=null; create=null; overview=null; caps=null; device=null; selected=null; scopedSessions=[]; scopedInspections=[];
    $('workspace').hidden=true; $('login').hidden=false; $('lock').hidden=true; $('new-chat').disabled=true; $('connection').textContent='Not connected'; $('token').value='';
    $('files').replaceChildren(); $('tasks').replaceChildren(); $('messages').replaceChildren(); $('activity').replaceChildren(); $('selection').hidden=true; historySignature='';
  }
  function unlock() { $('login').hidden=true; $('workspace').hidden=false; $('lock').hidden=false; $('token').value=''; write(sessionStorage,authKey,token); }
  function controls() {
    const unfinished=chat?.runs?.find(r=>!['completed','failed'].includes(r.status));
    $('new-chat').disabled= !token || sending || !!pending || !!unfinished;
    $('room').readOnly=!!chat || !!create || sending;
    if(chat?.room_id) $('room').value=chat.room_id;
    $('message').disabled=sending || !!pending;
    $('send').hidden=!!pending && !sending; $('send').disabled=sending;
    $('send').textContent=sending ? 'Working…' : 'Send ↑';
    $('resume').hidden=!pending || sending; $('resume').disabled=sending;
    $('run-status').textContent=sending ? 'Inspector is working. Saved tool results appear on the right.' : pending ? 'Request pending. Resume uses the same request ID.' : 'Ready when you are.';
    for(const button of $('suggestions').querySelectorAll('button')) button.disabled=sending || !!pending;
  }
  function renderRails() {
    $('project-name').textContent=overview.project_id;
    $('files').replaceChildren();
    const files=scopedInspections.filter(i=>i.bim_model);
    for(const item of files) {
      const button=node('button','',`▤  ${item.bim_model.filename}`); button.append(node('small','',`${item.room_id} · Reference BIM`));
      button.addEventListener('click',()=>showFile(item)); $('files').append(button);
    }
    const sources=scopedSessions.filter(s=>s.source);
    for(const session of sources) {
      const button=node('button','',`▤  Capture · ${session.room_id}`); button.append(node('small','',session.session_id.slice(0,8)));
      button.addEventListener('click',()=>showTask(session)); $('files').append(button);
    }
    if(!files.length && !sources.length) $('files').append(node('p','muted','No evidence linked to this conversation yet. Files appear after a capture is received; chat attachments are not supported yet.'));
    $('tasks').replaceChildren(); $('task-count').textContent=scopedSessions.length;
    for(const session of scopedSessions) {
      const button=node('button','',`◌  ${session.room_id}`); button.append(node('small','',`${session.capture_state} · ${session.processing_state}`));
      button.addEventListener('click',()=>showTask(session)); $('tasks').append(button);
    }
    if(!scopedSessions.length) $('tasks').append(node('p','muted','No capture tasks yet. Ask Inspector to create one.'));
    $('device-state').textContent=device.online?'Online':'Offline'; $('device-dot').classList.toggle('online',device.online);
    $('device-name').textContent=device.device_id; $('device-seen').textContent=`Last seen · ${timestamp(device.last_seen_at)}`;
    $('model-state').textContent=caps.agent_model_configured?'Configured':'Not configured';
    $('geometry-state').textContent=caps.geometry_mode==='mock'?'Mock / synthetic':caps.geometry_configured?'Configured':'Not connected';
    const counts={}; for(const session of scopedSessions) counts[session.processing_state]=(counts[session.processing_state]||0)+1;
    $('snapshot').replaceChildren(node('div','snapshot-number',scopedSessions.length),node('small','','Captures linked to this conversation'));
    for(const [label,value] of Object.entries(counts)) $('snapshot').append(bar(label.replaceAll('_',' '),value,scopedSessions.length,''));
    if(!scopedSessions.length) $('snapshot').append(node('p','muted','A live summary will appear as captures are created.'));
    if(selected) { const session=scopedSessions.find(s=>s.session_id===selected); if(session) showTask(session); }
  }
  function showFile(item) {
    selected=null; const box=$('selection'); box.hidden=false; box.className='selection';
    box.replaceChildren(node('h3','',item.bim_model.filename),node('p','',`Reference BIM · ${item.room_id}`),node('p','',`${item.bim_model.byte_length.toLocaleString()} bytes · Stored, not validated`));
    box.append(node('p','','Linked reference for this capture. Uploading a new BIM from chat is not available yet.'));
  }
  function showTask(session) {
    selected=session.session_id; const box=$('selection'); box.hidden=false; box.className='selection';
    box.replaceChildren(node('h3','',`Task · ${session.room_id}`),node('p','',session.capture_task?.text || 'Capture assignment'),node('p','',`Capture: ${session.capture_state}\nProcessing: ${session.processing_state}`),node('p','',`Created ${timestamp(session.created_at)}`));
    if(session.error) box.append(node('p','',session.error.message || 'Processing error reported'));
    if(session.geometry_result) { const detail=node('details'); detail.append(node('summary','','Geometry result'),node('pre','',JSON.stringify(session.geometry_result,null,2))); box.append(detail); }
    box.append(node('p','',`Session ${session.session_id}`));
  }
  function bar(label,value,max,unit) {
    const box=node('div'); const heading=node('div','bar-label'); heading.append(node('span','',label),node('span','',`${value}${unit?' '+unit:''}`));
    const track=node('div','bar-track'); const fill=node('div','bar-fill'); fill.style.width=`${Math.max(0,Math.min(100,max>0?value/max*100:0))}%`; track.append(fill); box.append(heading,track); return box;
  }
  function renderArtifact(item) {
    if(!['metric','bar_chart','table'].includes(item.type)) return null;
    const box=node('section',`artifact${item.type==='table'?' artifact-wide':''}`); box.append(node('h4','',item.title));
    if(item.type==='metric') box.append(node('div','metric-value',`${item.value}${item.unit?' '+item.unit:''}`));
    if(item.type==='bar_chart') {
      const values=(item.values||[]).slice(0,20); const max=Math.max(0,...values);
      values.forEach((value,i)=>box.append(bar(item.labels?.[i]||'',value,max,item.unit)));
    }
    if(item.type==='table') {
      const table=node('table'), head=node('thead'), row=node('tr'); for(const column of item.columns||[]) row.append(node('th','',column)); head.append(row); table.append(head);
      const body=node('tbody'); for(const values of item.rows||[]) { const tr=node('tr'); for(const value of values) tr.append(node('td','',value??'—')); body.append(tr); } table.append(body); box.append(table);
    }
    box.append(node('div','source',`AI summary · Source: ${item.source}. Verify against original evidence.`)); return box;
  }
  function renderConversation() {
    const runs=chat?.runs||[], signature=JSON.stringify(runs);
    if(historySignature!==signature) {
      const follow = $('messages').scrollHeight - $('messages').scrollTop - $('messages').clientHeight < 100;
      historySignature=signature;
      if(runs.length) {
        $('messages').replaceChildren();
        for(const run of runs) {
          const user=node('article','message user'); user.append(node('div','message-author','YOU'),messageText(run.text)); $('messages').append(user);
          const assistant=node('article','message assistant'); assistant.append(node('div','message-author','✳ INSPECTOR'));
          assistant.append(messageText(run.display_reply??run.reply??run.error?.message??(run.status==='running'?'Working on your request…':'Request saved. Awaiting a response.')));
          const grid=node('div','artifact-grid'); for(const item of run.artifacts||[]) { const artifact=renderArtifact(item); if(artifact) grid.append(artifact); } if(grid.childNodes.length) assistant.append(grid); $('messages').append(assistant);
        }
      } else {
        const welcome=node('div','welcome'); welcome.append(node('div','hero-icon','✳'),node('h1','','From a question\nto a clearer picture.'),node('p','','Ask about your device, plan a capture, or turn available evidence into a dashboard.')); $('messages').replaceChildren(welcome);
      }
      if(follow) $('messages').scrollTop=$('messages').scrollHeight;
    }
    $('activity').replaceChildren();
    const last=runs.at(-1);
    if(last) {
      const state=node('div','activity-item',`Agent · ${last.status}`); state.append(node('small','',timestamp(last.updated_at))); $('activity').append(state);
      for(const tool of last.tool_results||[]) {
        const event=node('div','activity-item',`${tool.result?.error?'!':'✓'} ${tool.name}`); event.append(node('small','',tool.result?.error?'Tool reported an error':'Tool result saved'));
        const detail=node('details'); detail.append(node('summary','','View result'),node('pre','',JSON.stringify(tool.result,null,2))); event.append(detail); $('activity').append(event);
      }
      if(!(last.tool_results||[]).length) $('activity').append(node('p','muted',last.status==='completed'?'Answered without a tool call.':'No completed tool calls reported yet.'));
      if(!['completed','failed'].includes(last.status)) { pending={schema_version:'0.1',request_id:last.request_id,text:last.text}; remember(); }
      else if(pending?.request_id===last.request_id) { pending=null; remember(); if(!sending) $('message').value=''; }
    } else $('activity').append(node('p','muted','No agent request in this conversation yet.'));
    controls();
  }
  async function refresh() {
    if(!token||refreshing) return; const current=epoch; refreshing=true;
    try {
      const [data,capabilities]=await Promise.all([api('/v1/operator/overview'),api('/v1/capabilities')]);
      const status=await api(`/v1/devices/${encodeURIComponent(data.device_id)}/status`);
      if(current!==epoch) return;
      overview=data; caps=capabilities; device=status;
      if(!chat && !create) {
        try {
          const saved=JSON.parse(read(localStorage,pointerKey)||'null'), draft=JSON.parse(read(sessionStorage,pendingKey)||'null');
          if(saved?.project_id===overview.project_id && saved.chat_id) chat={chat_id:saved.chat_id};
          if(draft?.project_id===overview.project_id && (!draft.chat_id || draft.chat_id===chat?.chat_id)) { pending=draft.pending; create=draft.create; if(create) $('room').value=create.room_id; }
        } catch {}
      }
      if(chat?.chat_id) { const id=chat.chat_id; const result=await api(`/v1/chats/${encodeURIComponent(id)}`); if(current!==epoch) return; if(chat?.chat_id===id) chat=result; }
      const scopeChat = chat?.chat_id;
      const ids = [...new Set([chat?.session_id, ...(chat?.runs||[]).map(run=>run.session_id)].filter(Boolean))];
      const linkedSessions = await Promise.all(ids.map(id=>data.sessions.find(session=>session.session_id===id) || api(`/v1/sessions/${encodeURIComponent(id)}`)));
      const inspectionIds = [...new Set(linkedSessions.map(session=>session.inspection_id))];
      const linkedInspections = await Promise.all(inspectionIds.map(id=>data.inspections.find(inspection=>inspection.inspection_id===id) || api(`/v1/inspections/${encodeURIComponent(id)}`)));
      if(current!==epoch || scopeChat!==chat?.chat_id) return;
      scopedSessions = linkedSessions; scopedInspections = linkedInspections;
      if(current!==epoch) return; $('connection').textContent='● Connected'; renderRails(); renderConversation(); unlock();
    } catch(error) { if(current===epoch) { $('connection').textContent='Connection issue'; notify(`Could not refresh: ${error.message}. Displayed data may be stale.`); } }
    finally { refreshing=false; }
  }
  async function send() {
    if(sending||!token||!overview) return;
    if(!pending && (!$('chat-form').reportValidity()||!$('message').value.trim())) return;
    const current=epoch; sending=true; notify();
    if(!pending) pending={schema_version:'0.1',request_id:uid(),text:$('message').value.trim()};
    if(!chat && !create) create={schema_version:'0.1',request_id:uid(),room_id:$('room').value.trim()};
    remember(); controls();
    try {
      if(!chat) { const created=await api('/v1/chats',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(create)}); if(current!==epoch) return; chat=created; create=null; remember(); }
      const result=await api(`/v1/chats/${chat.chat_id}/messages`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending)});
      if(current!==epoch) return;
      if(['completed','failed'].includes(result.status)) { pending=null; $('message').value=''; remember(); }
      if(result.error) notify(result.error.message);
      const runs=(chat.runs||[]).filter(r=>r.run_id!==result.run_id); chat={...chat,runs:[...runs,result]}; renderConversation();
    } catch(error) {
      if(current!==epoch) return;
      if(error.status===422) { $('message').value=pending?.text||''; pending=null; if(!chat) create=null; remember(); notify(`Please correct the request: ${error.message}`); }
      else notify(`Response not confirmed: ${error.message}. Resume the saved request safely.`);
    } finally { sending=false; if(current===epoch) { controls(); await refresh(); } }
  }
  $('login-form').addEventListener('submit',async event=>{event.preventDefault(); token=$('token').value.trim(); epoch++; notify(); event.submitter.disabled=true; await refresh(); event.submitter.disabled=false;});
  $('lock').addEventListener('click',lock);
  $('chat-form').addEventListener('submit',event=>{event.preventDefault();send();});
  $('resume').addEventListener('click',send);
  $('new-chat').addEventListener('click',()=>{if(sending||pending) return; chat=null;create=null; selected=null; scopedSessions=[]; scopedInspections=[]; $('selection').hidden=true; historySignature='';remember();$('message').value='';renderRails();renderConversation();notify();});
  for(const button of $('suggestions').querySelectorAll('button')) button.addEventListener('click',()=>{$('message').value=button.dataset.prompt;$('message').focus();});
  $('message').addEventListener('keydown',event=>{if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)){event.preventDefault();send();}});
  setInterval(()=>{if(token&&!document.hidden)refresh();},5000);
  if(token) refresh();
})();
