'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const authKey = 'inspector-operator-session', pointerKey = 'inspector-demo-chat', pendingKey = 'inspector-demo-pending';
  const read = (store, key) => { try { return store.getItem(key); } catch { return null; } };
  const write = (store, key, value) => { try { if (value == null) store.removeItem(key); else store.setItem(key,value); } catch {} };
  let token = read(sessionStorage, authKey) || '', epoch = 0, overview, caps, device, chat = null, pending = null, create = null;
  let scopedSessions = [], scopedInspections = [];
  let chatChoices=null;
  let workState=null,workspaceBusy=false,showTrash=false,uploadQueue=[],planEditing=null,observationEditing=null,importRequest=null;
  const chosenFiles=new Set();
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
    if(response.ok && options.blob) return response.blob();
    let body; try { body=await response.json(); } catch { body={}; }
    if (!response.ok) { if(response.status===401) lock(); const error=new Error(body.message || `Request failed (${response.status})`); error.status=response.status; error.code=body.code; throw error; }
    return body;
  }
  function lock() {
    epoch++; token=''; write(sessionStorage,authKey,null); chat=null; pending=null; create=null; overview=null; caps=null; device=null; selected=null; scopedSessions=[]; scopedInspections=[];
    chatChoices=null;workState=null;chosenFiles.clear();uploadQueue=[];for(const dialog of document.querySelectorAll('dialog'))dialog.close();
    $('workspace').hidden=true; $('login').hidden=false; $('lock').hidden=true; $('new-chat').disabled=true; $('connection').textContent='Not connected'; $('token').value='';
    $('files').replaceChildren(); $('tasks').replaceChildren(); $('messages').replaceChildren(); $('activity').replaceChildren(); $('selection').hidden=true; historySignature=''; resetDocument();
  }
  function unlock() { $('login').hidden=true; $('workspace').hidden=false; $('lock').hidden=false; $('token').value=''; write(sessionStorage,authKey,token); }
  function controls() {
    const unfinished=chat?.runs?.find(r=>!['completed','failed'].includes(r.status));
    $('chat-picker').disabled=sending||workspaceBusy||!!pending;
    $('document-view').inert=sending||!!pending; $('clear-document-context').disabled=sending||!!pending;
    $('new-chat').disabled= !token || sending || workspaceBusy || !!pending || !!unfinished;
    $('attach-file').disabled=!token||sending||workspaceBusy||!!pending; $('add-plan-item').disabled=!token||workspaceBusy; $('import-plan').disabled=!chat||workspaceBusy;
    $('room').readOnly=!!chat || !!create || sending;
    if(chat?.room_id) $('room').value=chat.room_id;
    $('message').disabled=sending || !!pending;
    $('send').hidden=!!pending && !sending; $('send').disabled=sending||workspaceBusy;
    $('send').textContent=sending ? 'Working…' : 'Send ↑';
    $('resume').hidden=!pending || sending; $('resume').disabled=sending;
    $('run-status').textContent=sending ? 'Inspector is working. Tool results appear in Inspector activity.' : pending ? 'Request pending. Resume uses the same request ID.' : 'Ready when you are.';
    for(const button of $('suggestions').querySelectorAll('button')) button.disabled=sending || !!pending;
  }
  function renderRails() {
    $('project-name').textContent=overview.project_id;
    renderFiles();renderProgress();
    $('tasks').replaceChildren(); $('task-count').textContent=scopedSessions.length;
    for(const session of scopedSessions) {
      const button=node('button','',`◌  ${session.room_id}`); button.append(node('small','',`${session.capture_state} · ${session.processing_state}`));
      button.addEventListener('click',()=>showTask(session)); $('tasks').append(button);
    }
    if(!scopedSessions.length) $('tasks').append(node('p','muted','No capture tasks yet. Ask Inspector to create one.'));
    $('device-state').textContent=device.online?'Online':'Offline'; $('device-dot').classList.toggle('online',device.online);
    $('device-name').textContent=device.device_id; $('device-seen').textContent=`Last seen · ${timestamp(device.last_seen_at)}`;
    if(selected) { const session=scopedSessions.find(s=>s.session_id===selected); if(session) showTask(session); }
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
    $('suggestions').hidden=runs.length>0;
    if(historySignature!==signature) {
      const follow = $('messages').scrollHeight - $('messages').scrollTop - $('messages').clientHeight < 100;
      historySignature=signature;
      if(runs.length) {
        $('messages').replaceChildren();
        for(const run of runs) {
          const user=node('article','message user'); user.append(node('div','message-author','YOU'),messageText(run.text)); if(run.document_selection)user.append(node('small','selected-chip',run.document_selection.label)); $('messages').append(user);
          const assistant=node('article','message assistant'); assistant.append(node('div','message-author','✳ INSPECTOR'));
          assistant.append(messageText(run.display_reply??run.reply??run.error?.message??(run.status==='running'?'Working on your request…':'Request saved. Awaiting a response.')));
          const grid=node('div','artifact-grid'); for(const item of run.artifacts||[]) { const artifact=renderArtifact(item); if(artifact) grid.append(artifact); } if(grid.childNodes.length) assistant.append(grid); $('messages').append(assistant);
        }
      } else {
        const welcome=node('div','welcome'); welcome.append(node('div','hero-icon','✳'),node('p','','Attach files or send a message.')); $('messages').replaceChildren(welcome);
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
      if(!['completed','failed'].includes(last.status)) { pending={schema_version:'0.1',request_id:last.request_id,text:last.text,file_ids:last.file_ids||[]}; remember(); }
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
      if(chatChoices===null)chatChoices=(await api('/v1/chats')).chats;
      if(current!==epoch)return;
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
      const workspaceData=scopeChat?await api(`/v1/chats/${scopeChat}/workspace`):null;
      if(current!==epoch || scopeChat!==chat?.chat_id) return;
      if(chat?.chat_id&&!chatChoices.some(c=>c.chat_id===chat.chat_id))chatChoices.unshift(chat);
      renderChatPicker();
      workState=workspaceData; scopedSessions = linkedSessions; scopedInspections = linkedInspections;
      if(current!==epoch) return; $('connection').textContent='● Connected'; renderRails(); renderConversation(); unlock();
    } catch(error) { if(current===epoch) { $('connection').textContent='Connection issue'; notify(`Could not refresh: ${error.message}. Displayed data may be stale.`); } }
    finally { refreshing=false; }
  }
  async function send() {
    if(sending||workspaceBusy||!token||!overview) return;
    if(!pending && (!$('chat-form').reportValidity()||!$('message').value.trim())) return;
    const current=epoch; sending=true; notify();
    if(!pending) pending={schema_version:'0.1',request_id:uid(),text:$('message').value.trim(),file_ids:[...chosenFiles],...(documentContext?{document_selection:documentContext}:{})};
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
  const workLabels = {confirmed:'Confirmed',needs_review:'Needs review',not_done:'Observed not done',no_data:'No observations'};
  const post = (path,body) => api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const workspacePath = () => `/v1/chats/${chat.chat_id}`;
  async function ensureChat() {
    if(chat?.chat_id) return;
    if(!$('room').reportValidity()) throw new Error('Enter a valid room context first.');
    const current=epoch;
    if(!create) create={schema_version:'0.1',request_id:uid(),room_id:$('room').value.trim()};
    remember();
    const result=await post('/v1/chats',create);
    if(current!==epoch) throw new Error('Workspace was locked.');
    chat=result; create=null; remember(); controls();
  }
  async function loadWorkspace() {
    if(!chat?.chat_id) return;
    const current=epoch,id=chat.chat_id;
    const result=await api(`/v1/chats/${id}/workspace`);
    if(current!==epoch||chat?.chat_id!==id) return;
    workState=result; renderFiles();renderProgress();
  }
  function renderFiles() {
    const files=workState?.files||[];
    for(const id of chosenFiles) if(!files.some(f=>f.file_id===id&&!f.deleted)) chosenFiles.delete(id);
    if(documentContext&&!files.some(f=>f.file_id===documentContext.file_id&&!f.deleted)){documentContext=null;$('document-context').hidden=true;}
    $('files').replaceChildren(); $('selected-files').replaceChildren();
    $('trash-toggle').textContent=showTrash?'Back to files':'Trash';
    for(const file of files.filter(f=>!!f.deleted===showTrash).sort((a,b)=>b.created_at.localeCompare(a.created_at))) {
      const row=node('div','file-row');
      if(!showTrash) {
        const check=node('input');check.type='checkbox';check.checked=chosenFiles.has(file.file_id);check.disabled=sending||!!pending;
        check.setAttribute('aria-label',`Use ${file.name} for AI`);
        check.addEventListener('change',()=>{
          if(check.checked&&chosenFiles.size>=8){check.checked=false;notify('Select up to 8 files per message.');return;}
          if(check.checked)chosenFiles.add(file.file_id);else {chosenFiles.delete(file.file_id);if(documentContext?.file_id===file.file_id){documentContext=null;$('document-context').hidden=true;}}renderFiles();
        });row.append(check);
      }
      const name=node('button','file-name',file.name);name.type='button';name.title='Open document';
      name.append(node('small','',`${file.origin} · ${file.content_available?(file.text_truncated?'AI: text excerpt':'AI: text'):'AI: metadata only'}`));
      name.addEventListener('click',()=>openDocument(file));row.append(name);
      const remove=node('button','file-delete',showTrash?'↶':'×');remove.type='button';remove.setAttribute('aria-label',`${showTrash?'Restore':'Delete'} ${file.name}`);
      remove.disabled=workspaceBusy;
      remove.addEventListener('click',async()=>{
        if(workspaceBusy)return;
        if(!showTrash&&!confirm(`Move ${file.name} to Trash? Saved observations and prior messages keep their evidence history. You can restore the file.`))return;
        workspaceBusy=true;controls();
        try{await post(`${workspacePath()}/files/${encodeURIComponent(file.file_id)}/${file.deleted?'restore':'delete'}`,{});await loadWorkspace();if(documentOpenId===file.file_id)resetDocument();}
        catch(error){notify(error.message);}finally{workspaceBusy=false;controls();}
      });row.append(remove);$('files').append(row);
    }
    if(!$('files').childNodes.length)$('files').append(node('p','muted',showTrash?'Trash is empty.':'No files yet. Attach a file beside the message input.'));
    for(const id of chosenFiles){const file=files.find(f=>f.file_id===id);if(file)$('selected-files').append(node('span','selected-chip',`${file.name} · ${documentContext?.file_id===file.file_id?'selected preview + reference':file.content_available?'text':'metadata only'}`));}
  }
  async function downloadFile(file) {
    const blob=await api(`${workspacePath()}/files/${encodeURIComponent(file.file_id)}/download`,{blob:true});
    downloadBlob(blob,file.name);
  }
  function downloadBlob(blob,name){const url=URL.createObjectURL(blob);const link=node('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  async function uploadFiles() {
    if(workspaceBusy||sending||pending)return;
    workspaceBusy=true;controls();notify();
    try{
      await ensureChat();
      while(uploadQueue.length){
        const entry=uploadQueue[0],form=new FormData();form.append('file_id',entry.id);form.append('file',entry.file);
        await api(`${workspacePath()}/files`,{method:'POST',body:form});
        uploadQueue.shift();await loadWorkspace(); if(chosenFiles.size<8)chosenFiles.add(entry.id);renderFiles();
        const uploaded=workState?.files?.find(f=>f.file_id===entry.id);if(uploaded&&/\.(ifc|pdf|png|jpe?g|webp)$/i.test(uploaded.name))await openDocument(uploaded);
      }
      $('file-input').value='';notify('Files uploaded. Checked files will be included with your next message.');
    }catch(error){if(error.status>=400&&error.status<500&&![408,429].includes(error.status)){uploadQueue=[];$('file-input').value='';notify(`Upload rejected: ${error.message}. Choose a file again.`);}else notify(`Upload was not confirmed: ${error.message}. Use Retry upload to reuse the same upload ID.`);}
    finally{workspaceBusy=false;controls();$('attach-file').textContent=uploadQueue.length?'Retry upload':'＋';}
  }
  function renderProgress() {
    const progress=workState?.progress,items=(workState?.items||[]).filter(i=>!i.deleted);
    $('plan-summary').replaceChildren();$('plan-items').replaceChildren();
    if(!progress?.total)$('plan-summary').append(node('p','muted','Add planned work manually or import a CSV / JSON plan.'));
    else{
      $('plan-summary').append(node('div','progress-total',`${progress.counts.confirmed} / ${progress.total}`),node('p','progress-note',`${progress.confirmed_percent}% of planned items confirmed by operator observations. Each item has equal weight.`));
      for(const [status,count]of Object.entries(progress.counts))$('plan-summary').append(bar(workLabels[status],count,progress.total,''));
      $('plan-summary').append(node('p','progress-note','No observations does not mean work is unfinished.'));
    }
    for(const item of items){
      const card=node('div','plan-item');card.append(node('strong','',item.title),node('small','',`${item.location}${item.location?' · ':''}${workLabels[item.status]}`));
      const actions=node('div','plan-item-actions');
      for(const [label,fn]of [['Observe',()=>openObservation(item)],['History',()=>openHistory(item)],['Edit',()=>openPlan(item)]]){const button=node('button','quiet',label);button.type='button';button.disabled=workspaceBusy;button.addEventListener('click',fn);actions.append(button);}
      card.append(actions);$('plan-items').append(card);
    }
    const archived=(workState?.items||[]).filter(i=>i.deleted);
    if(archived.length){const details=node('details');details.append(node('summary','muted',`Archived (${archived.length})`));for(const item of archived){const button=node('button','quiet',item.title);button.addEventListener('click',()=>openPlan(item));details.append(button);}$('plan-items').append(details);}
  }
  async function openPlan(item=null){
    if(workspaceBusy)return;
    workspaceBusy=true;controls();
    try{await ensureChat();planEditing=item||{item_id:uid(),revision:0};$('plan-form').reset();$('plan-name').value=item?.title||'';$('plan-location').value=item?.location||'';$('plan-reference').value=item?.reference||'';$('plan-archived').checked=!!item?.deleted;$('plan-error').textContent='';$('plan-dialog').showModal();}
    catch(error){notify(error.message);}finally{workspaceBusy=false;controls();}
  }
  function openObservation(item){
    observationEditing={item_id:item.item_id,id:uid(),body:null};$('observation-form').reset();$('observation-item').textContent=item.title;
    const time=new Date();$('observation-time').value=new Date(time-time.getTimezoneOffset()*60000).toISOString().slice(0,16);
    $('observation-error').textContent='';$('observation-files').replaceChildren(node('small','','Evidence (optional for a direct human observation)'));
    for(const file of(workState?.files||[]).filter(f=>!f.deleted)){
      const label=node('label','checkbox-label');const check=node('input');check.type='checkbox';check.value=file.file_id;check.checked=chosenFiles.has(file.file_id);label.append(check,document.createTextNode(file.name));$('observation-files').append(label);
    }
    $('observation-dialog').showModal();
  }
  function openHistory(item){
    $('observation-history').replaceChildren(node('strong','',item.title));
    const observations=(workState?.observations||[]).filter(o=>o.item_id===item.item_id).sort((a,b)=>b.observed_at.localeCompare(a.observed_at));
    if(!observations.length)$('observation-history').append(node('p','muted','No observations recorded.'));
    for(const observation of observations){
      const record=node('div','observation-record',`${workLabels[observation.status]}${observation.withdrawn?' · Withdrawn':''}\n${timestamp(observation.observed_at)} · ${observation.author}\n${observation.note}`);
      for(const evidence of observation.evidence||[])record.append(node('small','',`\n${evidence.name} · ${evidence.origin}`));
      if(!observation.withdrawn){const withdraw=node('button','quiet','Withdraw observation');withdraw.addEventListener('click',async()=>{
        if(!confirm('Withdraw this observation? It remains in history; the item will use the latest remaining observation.'))return;
        withdraw.disabled=true;try{await post(`${workspacePath()}/observations/${observation.observation_id}/withdraw`,{});await loadWorkspace();openHistory(item);}catch(error){notify(error.message);withdraw.disabled=false;}
      });record.append(document.createElement('br'),withdraw);}$('observation-history').append(record);
    }
    if(!$('history-dialog').open)$('history-dialog').showModal();
  }
  $('attach-file').addEventListener('click',()=>{if(uploadQueue.length)uploadFiles();else $('file-input').click();});
  $('file-input').addEventListener('change',()=>{
    const files=[...$('file-input').files];if(!files.length)return;
    if(files.some(file=>file.size>(overview?.max_bim_bytes||8*1024*1024))){notify('Each file must be no larger than 8 MiB.');$('file-input').value='';return;}
    uploadQueue=files.map(file=>({file,id:uid()}));uploadFiles();
  });
  $('trash-toggle').addEventListener('click',()=>{showTrash=!showTrash;renderFiles();});
  $('add-plan-item').addEventListener('click',()=>openPlan());
  for(const button of document.querySelectorAll('[data-close]'))button.addEventListener('click',()=>$(button.dataset.close).close());
  $('plan-form').addEventListener('submit',async event=>{
    event.preventDefault();if(workspaceBusy)return;workspaceBusy=true;event.submitter.disabled=true;
    try{await post(`${workspacePath()}/plan/${planEditing.item_id}`,{revision:planEditing.revision,title:$('plan-name').value,location:$('plan-location').value,reference:$('plan-reference').value,deleted:$('plan-archived').checked});$('plan-dialog').close();await loadWorkspace();}
    catch(error){$('plan-error').textContent=error.message;}finally{workspaceBusy=false;event.submitter.disabled=false;controls();}
  });
  $('observation-form').addEventListener('submit',async event=>{
    event.preventDefault();if(workspaceBusy)return;
    const files=[...$('observation-files').querySelectorAll('input:checked')].map(input=>input.value);
    if(files.length>8){$('observation-error').textContent='Select up to 8 evidence files.';return;}
    workspaceBusy=true;event.submitter.disabled=true;
    if(!observationEditing.body)observationEditing.body={item_id:observationEditing.item_id,status:$('observation-status').value,note:$('observation-note').value,author:$('observation-author').value,observed_at:new Date($('observation-time').value).toISOString(),file_ids:files};
    try{await post(`${workspacePath()}/observations/${observationEditing.id}`,observationEditing.body);$('observation-dialog').close();await loadWorkspace();}
    catch(error){$('observation-error').textContent=error.message;if(error.status===422)observationEditing.body=null;}
    finally{workspaceBusy=false;event.submitter.disabled=false;controls();}
  });

  let documentContext=null,documentOpenId=null,documentGeneration=0;
  const viewer=new ProjectDocuments.Viewer($('document-view'),context=>{
    if(context&&!chosenFiles.has(context.file_id)&&chosenFiles.size>=8){notify('Select up to 8 files per message. Uncheck a file first.');return;}
    documentContext=context;
    $('document-context').hidden=!context;
    if(context){chosenFiles.add(context.file_id);$('document-context').querySelector('span').textContent=context.label;renderFiles();}
  },text=>{$('document-status').textContent=text;});
  function resetDocument(){documentGeneration++;viewer.clear();documentOpenId=null;documentContext=null;$('document-context').hidden=true;$('document-name').textContent='Project documents';$('document-download').disabled=true;$('document-status').textContent='';$('document-view').replaceChildren(node('p','document-empty','Open a BIM model or drawing from Files.'));}
  async function openDocument(file){if(file.deleted||sending||pending)return;const generation=++documentGeneration;documentOpenId=file.file_id;$('document-name').textContent=file.name;$('document-download').disabled=false;$('document-status').textContent='Loading…';try{const blob=await api(`${workspacePath()}/files/${encodeURIComponent(file.file_id)}/download`,{blob:true});if(generation!==documentGeneration)return;await viewer.open(file,blob);}catch(e){if(generation===documentGeneration)notify(e.message);}}
  $('clear-document-context').onclick=()=>{viewer.clearSelection();renderFiles();};
  $('document-download').onclick=()=>{const file=workState?.files?.find(f=>f.file_id===documentOpenId);if(file)downloadFile(file).catch(e=>notify(e.message));};
  $('zoom-in').onclick=()=>viewer.zoom(1.2);$('zoom-out').onclick=()=>viewer.zoom(1/1.2);$('zoom-fit').onclick=()=>viewer.zoom(1);
  $('page-prev').onclick=()=>viewer.next(-1).catch(e=>notify(e.message));$('page-next').onclick=()=>viewer.next(1).catch(e=>notify(e.message));
  const root=document.documentElement;
  const layoutKey='inspector-document-layout';let layout={files:210,dialogue:360,scale:1};
  try{Object.assign(layout,JSON.parse(read(localStorage,layoutKey)||'{}'));}catch{}
  function applyLayout(){layout.files=Math.max(150,Math.min(340,Number(layout.files)||210));layout.dialogue=Math.max(280,Math.min(innerWidth*.5,Number(layout.dialogue)||360));layout.scale=Math.max(.85,Math.min(1.3,Number(layout.scale)||1));root.style.setProperty('--files-width',layout.files+'px');root.style.setProperty('--dialogue-width',layout.dialogue+'px');root.style.setProperty('--ui-scale',layout.scale);$('ui-scale').value=String(layout.scale);write(localStorage,layoutKey,JSON.stringify(layout));}
  for(const [id,key,direction] of [['resize-files','files',1],['resize-dialogue','dialogue',-1]]){const handle=$(id);let drag=null;handle.onpointerdown=e=>{if(e.button!==0)return;drag={x:e.clientX,width:layout[key]};handle.setPointerCapture(e.pointerId);};handle.onpointermove=e=>{if(!drag)return;layout[key]=drag.width+(e.clientX-drag.x)*direction;applyLayout();};handle.onpointerup=handle.onpointercancel=()=>{drag=null;};handle.onkeydown=e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;e.preventDefault();layout[key]+=(e.key==='ArrowRight'?20:-20)*direction;applyLayout();};}
  $('ui-scale').onchange=()=>{layout.scale=Number($('ui-scale').value);applyLayout();};$('reset-layout').onclick=()=>{layout={files:210,dialogue:360,scale:1};applyLayout();viewer.zoom(1);};applyLayout();
  $('plan-template').addEventListener('click',()=>downloadBlob(new Blob(['title,location,reference\n'],{type:'text/csv'}),'plan-template.csv'));
  $('import-plan').addEventListener('click',async()=>{
    if(workspaceBusy)return;
    if(chosenFiles.size!==1){notify('Select one uploaded CSV or JSON file in Files. Required column: title. Optional: location, reference.');return;}
    const file_id=[...chosenFiles][0];
    if(!importRequest||importRequest.file_id!==file_id){if(!confirm('Add the rows from the selected plan to this conversation? Existing items are kept.'))return;importRequest={request_id:uid(),file_id};}
    workspaceBusy=true;controls();
    try{const result=await post(`${workspacePath()}/plan-import`,importRequest);await loadWorkspace();notify(`${result.imported} plan items imported. No work is marked complete until an observation is recorded.`);importRequest=null;}
    catch(error){notify(error.message);if(error.status===422)importRequest=null;}
    finally{workspaceBusy=false;controls();}
  });

  function renderChatPicker(){
    $('chat-picker').replaceChildren(node('option','','New conversation'));
    $('chat-picker').firstChild.value='';
    for(const item of chatChoices||[]){const option=node('option','',`${item.room_id} · ${timestamp(item.created_at)}`);option.value=item.chat_id;$('chat-picker').append(option);}
    $('chat-picker').value=chat?.chat_id||'';
  }
  $('chat-picker').addEventListener('change',async()=>{
    if(sending||workspaceBusy||pending)return;
    const id=$('chat-picker').value;
    if($('message').value.trim()&&!confirm('Discard this unsent message and switch conversation?')){renderChatPicker();return;}
    chat=id?{chat_id:id}:null;create=null;workState=null;chosenFiles.clear();selected=null;scopedSessions=[];scopedInspections=[];importRequest=null;
    $('selection').hidden=true;$('message').value='';historySignature='';resetDocument();remember();renderRails();renderConversation();await refresh();
  });
  $('login-form').addEventListener('submit',async event=>{event.preventDefault(); token=$('token').value.trim(); epoch++; notify(); event.submitter.disabled=true; await refresh(); event.submitter.disabled=false;});
  $('lock').addEventListener('click',lock);
  $('chat-form').addEventListener('submit',event=>{event.preventDefault();send();});
  $('resume').addEventListener('click',send);
  $('new-chat').addEventListener('click',()=>{if(sending||workspaceBusy||pending) return; workState=null;chosenFiles.clear();uploadQueue=[];importRequest=null;showTrash=false; chat=null;create=null; selected=null; scopedSessions=[]; scopedInspections=[]; $('selection').hidden=true; historySignature='';resetDocument();remember();$('message').value='';renderChatPicker();renderRails();renderConversation();notify();});
  for(const button of $('suggestions').querySelectorAll('button')) button.addEventListener('click',()=>{$('message').value=button.dataset.prompt;$('message').focus();});
  $('message').addEventListener('keydown',event=>{if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)){event.preventDefault();send();}});
  setInterval(()=>{if(token&&!document.hidden)refresh();},5000);
  if(token) refresh();
})();
