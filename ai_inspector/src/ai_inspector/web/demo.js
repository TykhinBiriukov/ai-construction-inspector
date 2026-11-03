'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const authKey = 'inspector-operator-session', pointerKey = 'inspector-demo-chat', pendingKey = 'inspector-demo-pending';
  const read = (store, key) => { try { return store.getItem(key); } catch { return null; } };
  const write = (store, key, value) => { try { if (value == null) store.removeItem(key); else store.setItem(key,value); } catch {} };
  let token = read(sessionStorage, authKey) || '', epoch = 0, overview, caps, device, chat = null, pending = null, create = null;
  let scopedSessions = [], scopedInspections = [];
  let geometryViewer=null;
  let demoModes=null,selectedDemoMode='',modeBusy=false;
  let chatChoices=null;
  let workState=null,workspaceBusy=false,showTrash=false,uploadQueue=[],planEditing=null,observationEditing=null,importRequest=null;
  const chosenFiles=new Set();
  let selectionScope=null;
  const fileSelectionKey=()=>overview?.project_id&&chat?.chat_id?`inspector-files:${overview.project_id}:${chat.chat_id}`:null;
  function restoreFileSelection(){
    const key=fileSelectionKey();
    if(!key||selectionScope===key)return;
    chosenFiles.clear();selectionScope=key;
    try{const ids=JSON.parse(read(sessionStorage,key)||'[]');if(Array.isArray(ids))for(const id of ids.slice(0,8))if(typeof id==='string')chosenFiles.add(id);}catch{}
  }
  function saveFileSelection(){
    const key=fileSelectionKey();
    if(key&&key===selectionScope)write(sessionStorage,key,JSON.stringify([...chosenFiles]));
  }
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
  const chatPendingKey = id => `inspector-pending:${overview?.project_id}:${id}`;
  function remember() {
    if(chat?.chat_id)write(sessionStorage,chatPendingKey(chat.chat_id),pending?JSON.stringify(pending):null);
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
    closeProgress4d();geometryViewer?.clear();
    epoch++; token=''; write(sessionStorage,authKey,null); chat=null; pending=null; create=null; overview=null; caps=null; device=null; selected=null; scopedSessions=[]; scopedInspections=[];
    demoModes=null;selectedDemoMode='';syncModePickers();chatChoices=null;workState=null;chosenFiles.clear();selectionScope=null;uploadQueue=[];for(const dialog of document.querySelectorAll('dialog'))dialog.close();
    $('workspace').hidden=true; $('login').hidden=false; $('lock').hidden=true; $('new-chat').disabled=true; $('connection').textContent='Not connected'; $('token').value='';
    $('files').replaceChildren(); $('tasks').replaceChildren(); $('messages').replaceChildren(); $('activity').replaceChildren(); activitySignature='';activitySync=0;activityExpanded.clear(); $('selection').hidden=true; historySignature=''; resetDocument();
  }
  function unlock() { $('login').hidden=true; $('workspace').hidden=false; $('lock').hidden=false; $('token').value=''; write(sessionStorage,authKey,token); }
  function controls() {
    $('progress4d-open').disabled=!chat?.chat_id||modeBusy;
    for(const picker of document.querySelectorAll('[data-demo-mode],#demo-mode'))picker.disabled=!token||sending||workspaceBusy||modeBusy;
    $('chat-picker').disabled=sending||workspaceBusy;
    $('document-view').inert=sending||!!pending; $('clear-document-context').disabled=sending||!!pending;
    $('new-chat').disabled= !token || sending || workspaceBusy;
    $('attach-file').disabled=!token||sending||workspaceBusy||!!pending; $('add-plan-item').disabled=!token||workspaceBusy; $('import-plan').disabled=!chat||workspaceBusy;
    $('room').readOnly=!!chat || !!create || sending;
    if(chat?.room_id) $('room').value=chat.room_id;
    $('message').disabled=sending || !!pending;
    $('send').hidden=!!pending && !sending; $('send').disabled=sending||workspaceBusy;
    $('send').textContent=sending ? 'Working…' : 'Send ↑';
    $('resume').hidden=!pending || sending || !!chat?.runs?.at(-1)?.auto_resume; $('resume').disabled=sending;
    $('run-status').textContent=sending ? 'AI Inspector is working. Progress appears in Work process.' : chat?.runs?.at(-1)?.auto_resume ? 'Analysis is continuing automatically. You can return later.' : pending ? 'Request pending. Resume uses the same request ID.' : 'Ready when you are.';
    const review=chat?.geometry_reviews?.find(r=>!['completed','cancelled'].includes(r.status));
    if(review&&!sending&&!pending){const labels={waiting_geometry:'Processing captured geometry. Review will continue automatically.',preparing_evidence:'Preparing captured evidence.',reviewing:'Reviewing the captured evidence.',summarizing:'Preparing the inspection response.',waiting_configuration:'Inspection is waiting for service configuration.',retrying:'Inspection will retry automatically.',failed:'Inspection needs attention.'};const saved=scopedSessions.find(s=>s.session_id===chat.session_id)?.geometry_job?.processing_mode==='saved_result';$('run-status').textContent=review.status==='waiting_geometry'&&saved?'Loading saved inspection results. No reconstruction or segmentation.':labels[review.status]||'Inspection is continuing automatically.';if(review.status==='reviewing'&&review.batches)$('run-status').textContent+=` ${review.reviewed}/${review.batches} batches reviewed.`;}
    updateActivityStatus();renderGeometryProgress();
    for(const button of $('suggestions').querySelectorAll('button')) button.disabled=sending || !!pending;
  }
  function renderGeometryProgress() {
    let panel=$('geometry-progress');
    if(!panel){panel=node('div','action-card');panel.id='geometry-progress';$('activity-open').after(panel);}
    const run=chat?.runs?.at(-1),review=chat?.geometry_reviews?.filter(r=>r.status!=='cancelled').at(-1);
    const importing=run?.active_tool==='session_import'&&!['completed','failed'].includes(run.status);
    panel.hidden=!review&&!importing;panel.replaceChildren();if(panel.hidden)return;
    const stale=importing&&run.status==='running'&&Date.now()-Date.parse(run.updated_at)>90000;
    const paused=stale||run?.status==='retryable'||review?.status==='failed';
    const labels={waiting_geometry:'Reading saved inspection result',preparing_evidence:'Preparing evidence',reviewing:'Checking findings',summarizing:'Preparing the report',completed:'Inspection completed',waiting_configuration:'Waiting for service configuration',retrying:'Retrying the saved inspection',failed:'Inspection paused'};
    const label=paused?(review?.status==='failed'?'Inspection paused — see error details':'Processing interrupted — resume the saved request'):importing?'Connecting saved inspection result':labels[review?.status]||'Preparing evidence';
    panel.append(node('p','',label));
    if(review&&!importing){$('activity-current').textContent=label;$('process-state').textContent=label;}
    if(!paused&&review?.status!=='completed'){
      const progress=node('progress');progress.setAttribute('aria-label',label);
      if(review?.status==='reviewing'&&review.batches>0){progress.max=review.batches;progress.value=review.reviewed;panel.append(node('p','',`${review.reviewed} of ${review.batches} groups checked`));}
      panel.append(progress);
    }
    const saved=importing||scopedSessions.find(s=>s.session_id===chat?.session_id)?.geometry_job?.processing_mode==='saved_result';
    panel.append(node('small','',paused?'The last state is preserved.':saved?'Using existing results. Reconstruction and segmentation are not repeated.':'The inspection continues automatically.'));
    const updated=review?.updated_at||run?.updated_at;if(updated)panel.append(node('small','',`Last update: ${new Date(updated).toLocaleTimeString()}`));
  }
  function renderRails() {
    $('project-name').textContent=overview.project_id;
    renderFiles();renderProgress();
    $('tasks').replaceChildren(); $('task-count').textContent=scopedSessions.length;
    for(const session of scopedSessions) {
      const button=node('button','',`◌  ${session.room_id}`); button.append(node('small','',`${session.capture_state} · ${session.processing_state}`));
      button.addEventListener('click',()=>showTask(session)); $('tasks').append(button);
    }
    if(!scopedSessions.length) $('tasks').append(node('p','muted','No capture tasks yet. Ask AI Inspector to create one.'));
    $('device-state').textContent=device.online?'Online':'Offline'; $('device-dot').classList.toggle('online',device.online);
    $('device-name').textContent=device.device_id;
    if(selected) { const session=scopedSessions.find(s=>s.session_id===selected); if(session) showTask(session); }
  }
  function showTask(session) {
    selected=session.session_id; const box=$('selection'); box.hidden=false; box.className='selection';
    box.replaceChildren(node('h3','',`Task · ${session.room_id}`),node('p','',session.capture_task?.text || 'Capture assignment'),node('p','',`Capture: ${session.capture_state}\nProcessing: ${session.processing_state}`),node('p','',`Created ${timestamp(session.created_at)}`));
    if(session.error) box.append(node('p','',session.error.message || 'Processing error reported'));
    if(session.geometry_result) box.append(geometryResultCard(session.session_id));
    box.append(node('p','',`Session ${session.session_id}`));
  }
  function bar(label,value,max,unit) {
    const box=node('div'); const heading=node('div','bar-label'); heading.append(node('span','',label),node('span','',`${value}${unit?' '+unit:''}`));
    const track=node('div','bar-track'); const fill=node('div','bar-fill'); fill.style.width=`${Math.max(0,Math.min(100,max>0?value/max*100:0))}%`; track.append(fill); box.append(heading,track); return box;
  }
  let evidenceVersion=0,evidenceUrl=null,evidenceDetail=null,evidenceFull=false;
  function clearEvidence(){
    evidenceVersion++;if(evidenceUrl)URL.revokeObjectURL(evidenceUrl);if(evidenceDetail)URL.revokeObjectURL(evidenceDetail);
    evidenceUrl=null;evidenceDetail=null;$('evidence-image').removeAttribute('src');$('evidence-image').hidden=true;
    $('evidence-toggle').hidden=true;$('evidence-retry').hidden=true;
  }
  function showEvidenceImage(){
    $('evidence-image').src=evidenceFull?evidenceUrl:evidenceDetail;
    $('evidence-image').hidden=false;$('evidence-toggle').hidden=false;
    $('evidence-toggle').textContent=evidenceFull?'Show detail':'Show full image';
  }
  async function openEvidence(chatId,result,findingIndex,kind,refIndex){
    clearEvidence();const version=evidenceVersion,current=epoch;
    const finding=result.report.findings[findingIndex],claim=kind==='photo'?finding.photo_claim:finding.design_claim;
    const region=claim.evidence[refIndex],asset=(result.assets||[]).find(a=>a.asset_id===region.asset_id);
    $('evidence-title').textContent=`${kind==='photo'?'Photo':'Drawing'}${asset?.page?' · page '+asset.page:''} — ${finding.criterion}`;
    $('evidence-claim').textContent=claim.text;$('evidence-status').textContent='Loading evidence…';
    if(!$('evidence-dialog').open)$('evidence-dialog').showModal();
    $('evidence-retry').onclick=()=>openEvidence(chatId,result,findingIndex,kind,refIndex);
    try{
      const blob=await api(`/v1/chats/${encodeURIComponent(chatId)}/comparisons/${encodeURIComponent(result.task_id)}/evidence/${findingIndex}/${kind}/${refIndex}`,{blob:true});
      if(version!==evidenceVersion||current!==epoch)return;
      evidenceUrl=URL.createObjectURL(blob);const image=new Image();image.src=evidenceUrl;await image.decode();
      if(version!==evidenceVersion||current!==epoch)return;
      const [x,y,w,h]=region.bbox,padX=Math.max(.04,w*.2),padY=Math.max(.04,h*.2);
      const left=Math.max(0,x-padX),top=Math.max(0,y-padY),right=Math.min(1,x+w+padX),bottom=Math.min(1,y+h+padY);
      const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.ceil((right-left)*image.naturalWidth));canvas.height=Math.max(1,Math.ceil((bottom-top)*image.naturalHeight));
      canvas.getContext('2d').drawImage(image,left*image.naturalWidth,top*image.naturalHeight,(right-left)*image.naturalWidth,(bottom-top)*image.naturalHeight,0,0,canvas.width,canvas.height);
      const detail=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
      if(version!==evidenceVersion||current!==epoch)return;
      if(!detail)throw new Error('Could not display the evidence region.');
      evidenceDetail=URL.createObjectURL(detail);evidenceFull=false;showEvidenceImage();
      $('evidence-status').textContent='Highlighted area cited by the analysis. Open the full image to see its context.';
    }catch(error){if(version===evidenceVersion&&current===epoch){$('evidence-status').textContent=error.message||'Could not load evidence.';$('evidence-retry').hidden=false;}}
  }
  function comparisonResults(run){
    const results=new Map();
    for(const tool of run.tool_results||[]){const r=tool.result;if(['drawing_photo_compare','drawing_photo_result'].includes(tool.name)&&r?.task_id&&r.report?.findings?.length)results.set(r.task_id,r);}
    return [...results.values()];
  }
  function renderComparison(result,chatId){
    const box=node('section','artifact artifact-wide comparison-evidence');box.append(node('h4','','Drawing and photo comparison'));
    const table=node('table'),head=node('thead'),heading=node('tr');for(const name of ['Detail','Result','Evidence'])heading.append(node('th','',name));head.append(heading);table.append(head);
    const body=node('tbody');
    result.report.findings.forEach((finding,index)=>{
      const requirement=result.checklist?.checklist?.items?.find(item=>item.criterion===finding.criterion);
      const categories={geometry:'Geometry',material:'Material',product:'Product',measurement:'Measurement',installation:'Installation',performance:'Performance'};
      const label=categories[requirement?.category]?`${categories[requirement.category]} · ${finding.criterion}`:finding.criterion;
      const row=node('tr');row.append(node('td','',label),node('td','',({match:'Match',mismatch:'Mismatch',inconclusive:'Unclear'})[finding.verdict]||'Unclear'));
      const links=node('td');
      for(const [kind,key,label] of [['photo','photo_claim','Photo'],['drawing','design_claim','Drawing']]){
        const refs=finding[key]?.evidence||[];
        refs.forEach((region,i)=>{if(!Array.isArray(region.bbox)||region.bbox.length!==4||!region.bbox.every(Number.isFinite))return;
          const link=node('button','quiet evidence-link',label+(refs.length>1?' '+(i+1):''));link.type='button';link.setAttribute('aria-label',`View ${label.toLowerCase()} evidence ${i+1}: ${finding.criterion}`);
          link.onclick=()=>openEvidence(chatId,result,index,kind,i);links.append(link);
        });
      }
      if(!links.childNodes.length)links.append(node('span','','Unavailable'));
      row.append(links);body.append(row);
    });table.append(body);box.append(table);return box;
  }
  function renderArtifact(item) {
    if(!['metric','bar_chart','table'].includes(item.type)) return null;
    if(item.type==='table'&&String(item.source).includes('device_status')){
      const online=(item.rows||[]).find(r=>String(r[0]).toLowerCase()==='online');
      if(online&&['true','false'].includes(String(online[1]).toLowerCase())){
        const card=node('section','artifact');const name=(item.rows||[]).find(r=>String(r[0]).toLowerCase()==='device_id');
        card.append(node('h4','',name?.[1]||'Capture device'),node('div','device-status-result',String(online[1]).toLowerCase()==='true'?'Online':'Offline'));return card;
      }
    }
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
  const comparisonPhaseLabels={drawing_checklist:'Reading the drawing',checklist_reuse:'Using saved checklist',photo_inspection:'Checking the photo',review:'Reviewing the findings'};
  const toolLabels={session_import:'Connecting saved inspection result',device_status:'Checking capture device',session_get:'Reading capture task',session_create:'Creating capture task',drawing_photo_compare:'Comparing drawing and photo',drawing_photo_result:'Reading comparison results',delegate_inspection:'Reviewing inspection evidence'};
  let activitySync=0,activitySignature='',activityExpanded=new Map();
  function isLiveEvent(run,event){return event?.status==='running'&&event.origin==='live'&&run.status==='running'&&Date.now()-activitySync<12000&&Date.now()-Date.parse(run.updated_at)<90000&&!document.hidden;}
  function updateActivityStatus(){
    const last=chat?.runs?.at(-1),event=last?.activity_events?.at(-1),live=isLiveEvent(last||{},event),active=live||!!last?.auto_resume;
    let text=last?'Response ready':'AI Inspector is ready';
    if(last?.status==='retryable')text=last.auto_resume?'Analyzing your files':'Action needed';
    else if(last?.status==='failed')text='Request failed';
    else if(event?.status==='running')text=live?(event.kind==='tool'?toolLabels[event.name]||'Working on your request':'Preparing response'):'Waiting for server update';
    else if(sending)text='Sending request';
    else if(last&&!['completed','failed'].includes(last.status))text='Waiting for server';
    const phase=Object.entries(last?.comparison_progress?.phases||{}).find(([name,status])=>comparisonPhaseLabels[name]&&status==='running');
    if(last?.auto_resume&&phase)text=comparisonPhaseLabels[phase[0]];
    $('activity-current').textContent=text;$('process-state').textContent=text;$('activity-open').classList.toggle('working',active);
    $('activity-graph').classList.toggle('live',active);$('activity-feed').classList.toggle('live',active);
    const contextual=live&&last?.document_selection?.file_id===documentOpenId;
    viewer.setActivity(last?.document_selection,!!contextual);
  }

  function toolSummary(tool){const r=tool.result||{};if(r.error)return r.error.message||'This action could not be completed.';
    if(tool.name==='session_import')return r.new_geometry_computation===false?'Saved result connected. Evidence review continues automatically.':'Saved capture connected.';
    if(tool.name==='device_status')return `${r.device_id||'Capture device'} · ${r.online===true?'Online':r.online===false?'Offline':'Status unavailable'}`;
    const session=r.session===undefined?r:r.session;if(!session)return 'No capture task in this dialogue.';
    if(tool.name==='session_create'||tool.name==='session_get')return session.capture_state?`Capture task · ${String(session.capture_state).replaceAll('_',' ')}`:'Task information received.';
    return 'Completed';
  }
  function actionEvents(run){
    const raw=run.activity_events?.length?run.activity_events:(run.tool_results||[]).map((tool,i)=>({event_id:`legacy-${run.run_id}-${i}`,kind:'tool',name:tool.name,operation_id:tool.operation_id,status:tool.result?.error?'failed':'completed',origin:'unrecorded'}));
    const events=[],positions=new Map();
    for(const event of raw){
      // Planning corrections and network retries are diagnostics, not user-facing steps.
      if(String(event.operation_id||'').startsWith('validation-'))continue;
      if(event.kind==='tool'&&event.operation_id&&positions.has(event.operation_id)){
        events[positions.get(event.operation_id)]={...event};continue;
      }
      if(event.kind==='model'&&events.at(-1)?.kind==='model'){events[events.length-1]={...event};continue;}
      if(event.kind==='tool'&&event.operation_id)positions.set(event.operation_id,events.length);
      events.push({...event});
    }
    return events;
  }
  async function openActionContext(run,fileId){
    const file=workState?.files?.find(f=>f.file_id===fileId&&!f.deleted);
    if(!file){notify('This file is no longer available in the dialogue.');return;}
    $('activity-dialog').close();await openDocument(file,true);
    if(run.document_selection?.file_id===fileId)await viewer.focusSelection(run.document_selection);
  }
  function actionCard(run,event,inline=false){
    const tool=(run.tool_results||[]).find(t=>t.event_id?t.event_id===event.event_id:t.operation_id===event.operation_id);
    const details=node('details',`action-card action-${event.status}`);const key=(inline?'inline:':'history:')+event.event_id;
    details.open=activityExpanded.get(key)??['running','waiting','failed'].includes(event.status);
    details.addEventListener('click',e=>{if(e.target.closest('summary'))activityExpanded.set(key,!details.open);});
    const summary=node('summary'),icon=node('span','action-icon',event.status==='completed'?'✓':event.status==='failed'?'!':event.status==='waiting'?'Ⅱ':'◌');
    const label=node('span','action-label',event.kind==='model'?'Preparing response':toolLabels[event.name]||'Working on your request');
    const badge=node('span','action-state',event.status==='waiting'&&run.auto_resume?'Working':({completed:'Done',running:'Running',waiting:'Waiting',failed:'Failed',interrupted:'Interrupted'})[event.status]||event.status);
    summary.append(icon,label,badge);details.append(summary);
    const body=node('div','action-body');
    const comparison=['drawing_photo_compare','drawing_photo_result'].includes(event.name);
    const result=event.status==='failed'?'This step could not be completed. See the response for what to do next.':
      ['running','waiting'].includes(event.status)?(event.name==='session_import'?'Connecting the saved result; no reconstruction or segmentation.':comparison?'The drawing/photo comparison is in progress.':'This step is in progress.'):
      event.kind==='model'?'Step completed.':comparison?(tool?.result?.error?'The comparison could not be completed. See the response for details.':'Comparison results received.'):'Step completed.';
    body.append(node('p','',result));
    if(comparison&&run.comparison_progress?.operation_id===event.operation_id){
      for(const [phase,status] of Object.entries(run.comparison_progress.phases||{})){
        if(comparisonPhaseLabels[phase])body.append(node('p','',`${status==='completed'?'✓':'◌'} ${comparisonPhaseLabels[phase]}`));
      }
    }
    const synthetic=event.origin==='synthetic'||tool?.result?.analysis_origin==='synthetic'||tool?.result?.geometry_mode==='mock';if(synthetic)body.append(node('strong','action-synthetic','Synthetic result'));
    if(event.status==='waiting'&&run.status==='retryable')body.append(node('p','action-retry',run.auto_resume?'Continuing automatically. You can leave this page open or return later.':'Select Resume to continue.'));
    const links=node('div','action-links');
    if(tool?.name==='device_status'){const link=node('button','quiet','Show device');link.onclick=()=>{$('activity-dialog').close();document.querySelector('.device-card').scrollIntoView({block:'nearest'});};links.append(link);}
    const sessionId=tool?.result?.session?.session_id;if(sessionId){const session=scopedSessions.find(s=>s.session_id===sessionId);if(session){const link=node('button','quiet','Open capture task');link.onclick=()=>{$('activity-dialog').close();showTask(session);$('selection').scrollIntoView({block:'nearest'});};links.append(link);}}
    const selection=run.document_selection;
    if(selection){const link=node('button','quiet',selection.kind==='ifc'?'Open selected BIM elements':'Open selected document area');link.onclick=()=>openActionContext(run,selection.file_id).catch(e=>notify(e.message));links.append(link);}
    else for(const id of (run.file_ids||[]).slice(0,3)){const file=workState?.files?.find(f=>f.file_id===id&&!f.deleted);if(file){const link=node('button','quiet',file.name);link.onclick=()=>openActionContext(run,id).catch(e=>notify(e.message));links.append(link);}}
    if(event.kind==='model'&&event.status==='completed'&&run.reply){const link=node('button','quiet','View response');link.onclick=()=>{$('activity-dialog').close();document.getElementById('reply-'+run.run_id)?.scrollIntoView({block:'nearest'});};links.append(link);}
    body.append(links);details.append(body);return details;
  }
  function renderActivity(runs){
    const signature=JSON.stringify(runs.map(r=>[r.run_id,r.status,r.activity_events,r.tool_results,r.comparison_progress]));if(activitySignature===signature)return;activitySignature=signature;
    $('activity').replaceChildren();$('activity-feed').replaceChildren();$('activity-graph').replaceChildren();
    const last=runs.at(-1),events=last?actionEvents(last):[];
    $('activity-feed').hidden=!events.length;$('activity-graph').hidden=!events.length;
    if(!runs.length){$('activity').append(node('p','process-empty','Actions will appear here when you send a request.'));return;}
    if(events.length){
      const agent=node('span','graph-agent','AI Inspector');$('activity-graph').append(agent);
      const tools=[...new Set(events.filter(e=>e.kind==='tool').map(e=>e.name))];
      const branches=node('div','graph-branches');
      for(const name of tools){const event=events.filter(e=>e.name===name).at(-1);const branch=node('div',`graph-branch${event.status==='running'?' graph-active':''}`);branch.append(node('i','graph-link'),node('span','graph-tool',toolLabels[name]||'Inspection task'));branches.append(branch);}
      if(!tools.length)branches.append(node('span','graph-no-tools','Preparing your response'));$('activity-graph').append(branches);
      for(const event of events.slice(-4))$('activity-feed').append(actionCard(last,event,true));
    }
    for(const run of [...runs].reverse()){
      const group=node('section','process-run');group.append(node('h3','',run.text.slice(0,140)),node('small','process-date',timestamp(run.created_at)));
      const history=actionEvents(run);for(const event of history)group.append(actionCard(run,event));
      if(!history.length)group.append(node('p','process-empty',run.status==='completed'?'Response delivered.':run.status==='failed'?'This request could not be completed. See the response for details.':'Preparing your request.'));
      $('activity').append(group);
    }
  }
  function renderConversation() {
    const runs=chat?.runs||[], signature=JSON.stringify([runs,chat?.geometry_reviews,scopedSessions.map(s=>[s.session_id,s.geometry_result?.job_id,s.processing_state])]);
    $('suggestions').hidden=runs.length>0;
    if(historySignature!==signature) {
      const follow = $('messages').scrollHeight - $('messages').scrollTop - $('messages').clientHeight < 100;
      historySignature=signature;
      if(runs.length) {
        $('messages').replaceChildren();
        for(const run of runs) {
          const user=node('article','message user'); user.append(node('div','message-author',run.continuation?'AUTOMATIC REVIEW':'YOU'),messageText(run.continuation?'Results from the saved capture inspection.':run.text)); if(run.document_selection)user.append(node('small','selected-chip',run.document_selection.label)); $('messages').append(user);
          const assistant=node('article','message assistant'); assistant.id='reply-'+run.run_id; assistant.append(node('div','message-author','✳ AI INSPECTOR'));
          assistant.append(messageText(run.display_reply??run.reply??run.error?.message??(run.status==='running'?(Date.now()-Date.parse(run.updated_at)>90000?'No recent server update. Resume continues this saved request.':toolLabels[run.active_tool]||'Preparing response…'):'Request saved. Awaiting a response.')));
          const grid=node('div','artifact-grid'),comparisons=comparisonResults(run);
          for(const item of run.artifacts||[]) {
            const source=String(item.source||'');
            if(comparisons.length&&item.type==='table'&&(/drawing_photo_(compare|result)/.test(source)||comparisons.some(r=>source.includes(r.task_id))))continue;
            const artifact=renderArtifact(item);if(artifact)grid.append(artifact);
          }
          for(const result of comparisons)grid.append(renderComparison(result,chat.chat_id));
          if(run.continuation&&run.session_id&&scopedSessions.some(s=>s.session_id===run.session_id&&s.geometry_result))grid.append(geometryResultCard(run.session_id));
          if(grid.childNodes.length) assistant.append(grid); $('messages').append(assistant);
        }
      } else {
        const welcome=node('div','welcome'); welcome.append(node('div','hero-icon','✳'),node('p','','Attach files or send a message.')); $('messages').replaceChildren(welcome);
      }
      for(const session of scopedSessions.filter(s=>s.geometry_result&&!runs.some(r=>r.continuation&&r.session_id===s.session_id)))$('messages').append(geometryResultCard(session.session_id));
      if(follow) $('messages').scrollTop=$('messages').scrollHeight;
    }
    renderActivity(runs);
    const last=runs.at(-1);
    if(last&&!['completed','failed'].includes(last.status)) {
      if(!pending||pending.request_id!==last.request_id){
        // Preserve an existing frozen payload, including its image, on refresh.
        pending={schema_version:'0.1',request_id:last.request_id,text:last.text,file_ids:last.file_ids||[]};
        if(last.document_selection&&!last.auto_resume) { notify('This request is still running. Resume from the browser tab that sent its document selection.'); pending=null; }
        remember();
      }
    } else if(last&&pending?.request_id===last.request_id){pending=null;remember();if(!sending)$('message').value='';}
    controls();
  }
  let activeRefresh=null;
  function refresh(){
    if(activeRefresh)return activeRefresh;
    activeRefresh=refreshWorkspace().finally(()=>{activeRefresh=null;});
    return activeRefresh;
  }
  async function refreshWorkspace() {
    if(!token||refreshing) return; const current=epoch; refreshing=true;
    try {
      const [data,capabilities]=await Promise.all([api('/v1/operator/overview'),api('/v1/capabilities')]);
      const status=await api(`/v1/devices/${encodeURIComponent(data.device_id)}/status`);
      if(current!==epoch) return;
      overview=data; caps=capabilities; device=status;
      if(chatChoices===null)chatChoices=(await api('/v1/chats')).chats;
      if(demoModes===null)demoModes=(await api('/v1/demo-modes')).modes;
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
      selectedDemoMode=demoModes.find(m=>m.chat_id===chat?.chat_id)?.id||'';syncModePickers();
      renderChatPicker();
      workState=workspaceData; scopedSessions = linkedSessions; scopedInspections = linkedInspections;
      geometryViewer?.sync(chat?.chat_id,JSON.stringify([chat?.geometry_reviews,linkedSessions.map(s=>[s.session_id,s.processing_state,s.geometry_result?.job_id])]));
      if(current!==epoch) return; $('connection').textContent='● Connected'; activitySync=Date.now(); renderRails(); renderConversation(); unlock();
    } catch(error) { if(current===epoch) { activitySync=0;updateActivityStatus();$('connection').textContent='Connection issue'; notify(`Could not refresh: ${error.message}. Displayed data may be stale.`); } }
    finally { refreshing=false; }
  }
  async function send() {
    if(sending||workspaceBusy||!token||!overview||chat?.runs?.at(-1)?.auto_resume) return;
    if(!pending && (!$('chat-form').reportValidity()||!$('message').value.trim())) return;
    const current=epoch; sending=true; notify();
    if(!pending) pending={schema_version:'0.1',request_id:uid(),text:$('message').value.trim(),file_ids:[...chosenFiles],...(documentContext?{document_selection:documentContext}:{})};
    if(!chat && !create) create={schema_version:'0.1',request_id:uid(),room_id:$('room').value.trim()};
    remember(); $('message').value=''; controls();
    try {
      if(!chat) { const created=await api('/v1/chats',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(create)}); if(current!==epoch) return; chat=created; create=null; remember(); }
      const result=await api(`/v1/chats/${chat.chat_id}/messages`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending)});
      if(current!==epoch) return;
      if(['completed','failed'].includes(result.status)) { pending=null; $('message').value=''; remember(); }
      if(result.error&&!result.auto_resume) notify(result.error.message);
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
    if(workState)restoreFileSelection();
    const files=workState?.files||[];
    for(const id of chosenFiles) if(!files.some(f=>f.file_id===id&&!f.deleted)) chosenFiles.delete(id);
    if(workState)saveFileSelection();
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
      remove.disabled=workspaceBusy||sending||!!pending;
      remove.addEventListener('click',async()=>{
        if(workspaceBusy)return;
        if(!showTrash&&!confirm(`Move ${file.name} to Trash? Saved observations and prior messages keep their evidence history. You can restore the file.`))return;
        workspaceBusy=true;controls();
        try{await post(`${workspacePath()}/files/${encodeURIComponent(file.file_id)}/${file.deleted?'restore':'delete'}`,{});await loadWorkspace();if(documentOpenId===file.file_id)resetDocument();}
        catch(error){notify(error.message);}finally{workspaceBusy=false;controls();}
      });row.append(remove);$('files').append(row);
    }
    if(!$('files').childNodes.length)$('files').append(node('p','muted',showTrash?'Trash is empty.':'No files yet. Attach a file beside the message input.'));
    for(const id of chosenFiles){const file=files.find(f=>f.file_id===id);if(!file||documentContext?.file_id===id)continue;
      const chip=node('span','selected-chip');chip.append(node('span','',file.name));const remove=node('button','','×');remove.type='button';remove.setAttribute('aria-label',`Remove ${file.name} from message`);remove.disabled=sending||!!pending;remove.onclick=()=>{chosenFiles.delete(id);renderFiles();};chip.append(remove);$('selected-files').append(chip);
    }
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
      $('file-input').value='';notify();
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
  $('evidence-dialog').addEventListener('close',clearEvidence);
  $('evidence-toggle').addEventListener('click',()=>{evidenceFull=!evidenceFull;showEvidenceImage();});
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
  async function openDocument(file,inspect=false){if(file.deleted||(!inspect&&(sending||pending)))return;if(file.origin==='geometry'&&file.name==='geometry-review-report.json'){await geometryViewer.open(chat.chat_id,{sessionId:file.session_id});return;}const generation=++documentGeneration;documentOpenId=file.file_id;$('document-name').textContent=file.name;$('document-download').disabled=false;$('document-status').textContent='Loading…';try{const blob=await api(`${workspacePath()}/files/${encodeURIComponent(file.file_id)}/download`,{blob:true});if(generation!==documentGeneration)return;await viewer.open(file,blob);}catch(e){if(generation===documentGeneration)notify(e.message);}}
  let progress4dGeneration=0;
  function closeProgress4d(){progress4dGeneration++;$('progress4d-dialog').close();$('progress4d-frame').removeAttribute('src');}
  $('progress4d-close').onclick=closeProgress4d;
  $('progress4d-dialog').addEventListener('cancel',event=>{event.preventDefault();closeProgress4d();});
  async function openProgress4d(){
    if(!chat?.chat_id)return;
    const generation=++progress4dGeneration;
    $('progress4d-dialog').showModal();$('progress4d-status').textContent='Loading 4D result…';
    try{
      const result=await api(`/v1/chats/${chat.chat_id}/progress4d`);
      if(generation!==progress4dGeneration)return;
      const url=new URL(result.viewer_path,location.origin);
      if(url.origin!==location.origin||url.pathname!=='/ui/progress4d/viewer.html')throw new Error('Invalid 4D viewer address');
      $('progress4d-frame').src=url.href;$('progress4d-status').textContent='';
    }catch(error){if(generation===progress4dGeneration)$('progress4d-status').textContent=error.message;}
  };
  function syncModePickers(){
    for(const picker of document.querySelectorAll('[data-demo-mode],#demo-mode'))picker.value=selectedDemoMode;
  }
  async function openDemo(){
    if(!chat?.chat_id)return;
    if(selectedDemoMode==='monitoring')return openProgress4d();
    if(selectedDemoMode==='geometry')return geometryViewer.open(chat.chat_id,{scope:'model'});
    if(selectedDemoMode==='drawing'){
      const result=document.querySelector('.comparison-evidence');
      if(result)result.scrollIntoView({block:'start',behavior:'smooth'});
      const file=(workState?.files||[]).find(f=>!f.deleted&&(/drawing|plan|чертеж/i.test(f.name)||/\.pdf$/i.test(f.name)));
      if(file)await openDocument(file);
      if(!result)notify('No saved drawing/photo comparison is available in this conversation.');
      return;
    }
    return openProgress4d();
  }
  async function chooseDemoMode(value){
    if(modeBusy||sending||workspaceBusy)return;
    const entry=demoModes?.find(m=>m.id===value);
    if(!entry?.chat_id){syncModePickers();notify('This demo is not connected yet.');return;}
    modeBusy=true;controls();
    try{
      await switchConversation(entry.chat_id);
      if(chat?.chat_id!==entry.chat_id){syncModePickers();return;}
      selectedDemoMode=value;syncModePickers();await openDemo();
    }catch(error){notify(error.message);}finally{modeBusy=false;controls();}
  }
  $('demo-mode').onchange=()=>chooseDemoMode($('demo-mode').value);
  $('progress4d-open').onclick=openDemo;
  geometryViewer=new InspectionViewer($('geometry-dialog'),api);
  for(const header of [$('geometry-dialog').querySelector('.gv-header'),$('progress4d-dialog').querySelector('.progress4d-toolbar')]){
    const label=node('label','mode-control','Mode '),picker=$('demo-mode').cloneNode(true);picker.removeAttribute('id');picker.dataset.demoMode='';picker.onchange=()=>chooseDemoMode(picker.value);label.append(picker);header.insertBefore(label,header.lastElementChild);
  }
  function geometryResultCard(sessionId){
    const card=node('section','geometry-result-card');card.append(node('h4','','Captured geometry and inspection results'),node('p','','Explore the model, review decisions and the captured evidence.'));
    const actions=node('div','geometry-result-actions'),chatId=chat.chat_id;
    for(const [scope,label] of [['model','Open model'],['findings','View findings and evidence']]){const button=node('button','quiet',label);button.type='button';button.onclick=()=>geometryViewer.open(chatId,{sessionId,scope});actions.append(button);}
    card.append(actions);return card;
  }
  $('clear-document-context').onclick=()=>{if(documentContext)chosenFiles.delete(documentContext.file_id);viewer.clearSelection();renderFiles();};
  $('multi-select').onclick=()=>{viewer.multiSelect=!viewer.multiSelect;$('multi-select').setAttribute('aria-pressed',String(viewer.multiSelect));};
  $('document-download').onclick=()=>{const file=workState?.files?.find(f=>f.file_id===documentOpenId);if(file)downloadFile(file).catch(e=>notify(e.message));};
  $('zoom-in').onclick=()=>viewer.zoom(1.2);$('zoom-out').onclick=()=>viewer.zoom(1/1.2);$('zoom-fit').onclick=()=>viewer.zoom(1);
  $('page-prev').onclick=()=>viewer.next(-1).catch(e=>notify(e.message));$('page-next').onclick=()=>viewer.next(1).catch(e=>notify(e.message));
  const root=document.documentElement;
  const layoutKey='inspector-document-layout';let layout={files:210,dialogue:360,scale:1};
  try{Object.assign(layout,JSON.parse(read(localStorage,layoutKey)||'{}'));}catch{}
  function applyLayout(){layout.files=Math.max(150,Math.min(340,Number(layout.files)||210));layout.dialogue=Math.max(280,Math.min(innerWidth*.5,Number(layout.dialogue)||360));layout.scale=Math.max(.85,Math.min(1.3,Number(layout.scale)||1));root.style.setProperty('--files-width',layout.files+'px');root.style.setProperty('--dialogue-width',layout.dialogue+'px');root.style.setProperty('--ui-scale',layout.scale);$('ui-scale').value=String(layout.scale);write(localStorage,layoutKey,JSON.stringify(layout));}
  for(const [id,key,direction] of [['resize-files','files',1],['resize-dialogue','dialogue',-1]]){const handle=$(id);let drag=null;handle.onpointerdown=e=>{if(e.button!==0)return;drag={x:e.clientX,width:layout[key]};handle.setPointerCapture(e.pointerId);};handle.onpointermove=e=>{if(!drag)return;layout[key]=drag.width+(e.clientX-drag.x)*direction;applyLayout();};handle.onpointerup=handle.onpointercancel=()=>{drag=null;};handle.onkeydown=e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;e.preventDefault();layout[key]+=(e.key==='ArrowRight'?20:-20)*direction;applyLayout();};}
  $('ui-scale').onchange=()=>{layout.scale=Number($('ui-scale').value);applyLayout();};$('reset-layout').onclick=()=>{layout={files:210,dialogue:360,scale:1};applyLayout();viewer.zoom(1);};applyLayout();
  $('activity-open').onclick=()=>$('activity-dialog').showModal();
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
  async function switchConversation(id){
    if(activeRefresh)await activeRefresh;
    if(sending||workspaceBusy||!token)return;
    if($('message').value.trim()&&!confirm('Discard this unsent message and switch conversation?')){renderChatPicker();return;}
    remember(); // Keep the exact frozen payload in the old chat, including document selection.
    closeProgress4d();geometryViewer?.clear();epoch++; pending=null;chat=id?{chat_id:id}:null;create=null;
    if(id){try{pending=JSON.parse(read(sessionStorage,chatPendingKey(id))||'null');}catch{}}
    workState=null;chosenFiles.clear();selectionScope=null;uploadQueue=[];showTrash=false;selected=null;scopedSessions=[];scopedInspections=[];importRequest=null;
    $('selection').hidden=true;$('message').value='';historySignature='';resetDocument();remember();renderChatPicker();renderRails();renderConversation();notify();await refresh();
  }
  $('chat-picker').addEventListener('change',()=>switchConversation($('chat-picker').value));
  $('login-form').addEventListener('submit',async event=>{event.preventDefault(); token=$('token').value.trim(); epoch++; notify(); event.submitter.disabled=true; await refresh(); event.submitter.disabled=false;});
  $('lock').addEventListener('click',lock);
  $('chat-form').addEventListener('submit',event=>{event.preventDefault();send();});
  $('resume').addEventListener('click',send);
  $('new-chat').addEventListener('click',()=>switchConversation(''));
  for(const button of $('suggestions').querySelectorAll('button')) button.addEventListener('click',()=>{$('message').value=button.dataset.prompt;$('message').focus();});
  $('message').addEventListener('keydown',event=>{
    if(event.key!=='Enter'||event.isComposing||event.keyCode===229)return;
    if(event.metaKey||event.ctrlKey){event.preventDefault();const input=$('message');input.setRangeText('\n',input.selectionStart,input.selectionEnd,'end');input.dispatchEvent(new Event('input',{bubbles:true}));}
    else if(!event.shiftKey&&!event.altKey){event.preventDefault();send();}
  });
  setInterval(()=>{if(token&&!document.hidden)refresh();},5000);
  setInterval(()=>{if(token)updateActivityStatus();},1000);
  if(token) refresh();
})();
