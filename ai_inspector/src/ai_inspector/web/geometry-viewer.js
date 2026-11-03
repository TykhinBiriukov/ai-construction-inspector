'use strict';
// Presentation state only. Decisions and source bindings always come from the
// authenticated server projection; opening the viewer never starts an agent.
window.InspectionViewer = class InspectionViewer {
  constructor(dialog, api) {
    this.dialog=dialog;this.api=api;this.generation=0;this.assetGeneration=0;this.modelGeneration=0;this.mode='findings';this.view='photo';this.list='findings';this.frame=0;this.role='rgb';this.zoom=1;
    this.root=this.el('div','gv-shell');dialog.append(this.root);dialog.classList.add('geometry-viewer');
    const header=this.el('header','gv-header');header.append(this.el('div','', 'AI Inspector · Inspection results'));
    header.append(this.button('Close',()=>dialog.close(),'quiet'));this.root.append(header);
    const nav=this.el('nav','gv-modes');nav.setAttribute('aria-label','Results view');
    this.modeButtons={};for(const [key,label] of [['model','Whole model'],['findings','Findings']]){const b=this.button(label,()=>this.setMode(key),'gv-tab');this.modeButtons[key]=b;nav.append(b);}
    this.provenance=this.el('span','gv-provenance');nav.append(this.provenance);this.root.append(nav);
    this.notice=this.el('p','gv-notice');this.notice.setAttribute('role','status');this.root.append(this.notice);
    this.layout=this.el('div','gv-layout');this.root.append(this.layout);
    this.sidebar=this.el('aside','gv-sidebar');this.sidebar.setAttribute('aria-label','Findings and observations');this.layout.append(this.sidebar);
    const filters=this.el('div','gv-list-tabs');this.listButtons={};
    for(const [key,label] of [['findings','Reviewed'],['all','All objects']]){const b=this.button(label,()=>{this.list=key;this.renderList();},'gv-tab');filters.append(b);this.listButtons[key]=b;}
    this.sidebar.append(filters);this.mobilePicker=this.el('select','gv-mobile-picker');this.mobilePicker.setAttribute('aria-label','Select finding or object');this.mobilePicker.onchange=()=>{const row=this.data.observations.find(r=>r.id===this.mobilePicker.value);if(row)this.select(row);};this.sidebar.append(this.mobilePicker);this.rows=this.el('div','gv-rows');this.sidebar.append(this.rows);
    this.main=this.el('section','gv-main');this.layout.append(this.main);
    this.heading=this.el('h2');this.main.append(this.heading);
    this.status=this.el('span','gv-status');this.main.append(this.status);
    this.tabs=this.el('nav','gv-material-tabs');this.tabs.setAttribute('aria-label','Selected finding materials');this.tabButtons={};
    for(const [key,label] of [['model','Model'],['photo','Photos'],['next','Next steps']]){const b=this.button(label,()=>{this.view=key;this.main.scrollTop=0;this.renderMaterials();},'gv-tab');this.tabs.append(b);this.tabButtons[key]=b;}
    this.main.append(this.tabs);
    this.modelPanel=this.el('div','gv-model-panel');this.main.append(this.modelPanel);
    const tools=this.el('div','gv-model-tools');this.modelSelect=this.el('select');this.modelSelect.setAttribute('aria-label','Model source');
    this.modelSelect.onchange=()=>{this.modelKey=null;this.loadModel();};tools.append(this.modelSelect);
    tools.append(this.button('Fit',()=>this.model.zoom(1),'quiet'),this.button('Focus selected object',()=>this.focusModel(),'quiet'));
    const overlay=this.button('Overlay',()=>{},'quiet');overlay.disabled=true;overlay.title='A verified scan-to-IFC transform is not supplied';tools.append(overlay);this.modelPanel.append(tools);
    this.legend=this.el('div','gv-legend');for(const [status,label] of [['confirmed','! Confirmed discrepancy'],['needs_more_data','? More data needed'],['rejected','− Not confirmed']]){const item=this.el('span','',label);item.dataset.status=status;this.legend.append(item);}this.modelPanel.append(this.legend);
    this.modelHost=this.el('div','gv-model');this.modelPanel.append(this.modelHost);this.modelStatus=this.el('p','gv-caption');this.modelStatus.setAttribute('role','status');this.modelPanel.append(this.modelStatus);
    this.model=new ProjectDocuments.GeometryModel(this.modelHost,guid=>this.selectGuid(guid),text=>{this.modelStatus.textContent=text;});
    this.photoPanel=this.el('div','gv-photo-panel');this.main.append(this.photoPanel);
    this.photoTools=this.el('div','gv-photo-tools');this.frameSelect=this.el('select');this.frameSelect.setAttribute('aria-label','Captured frame');this.frameSelect.onchange=()=>{this.frame=Number(this.frameSelect.value);this.role='rgb';this.zoom=1;this.loadPhoto();};this.photoTools.append(this.frameSelect);
    this.roleButtons={};for(const [key,label] of [['rgb','Original'],['overlay','Overlay'],['mask','Mask'],['unreported','Image (role unknown)']]){const b=this.button(label,()=>{this.role=key;this.zoom=1;this.loadPhoto();},'gv-tab');this.photoTools.append(b);this.roleButtons[key]=b;}
    this.photoTools.append(this.button('−',()=>this.zoomPhoto(1/1.4),'quiet'),this.button('Fit',()=>{this.zoom=1;this.applyZoom();},'quiet'),this.button('+',()=>this.zoomPhoto(1.4),'quiet'));
    this.photoPanel.append(this.photoTools);
    this.imageHost=this.el('div','gv-image-host');this.image=this.el('img');this.image.alt='Evidence from the accepted capture';this.image.draggable=false;this.imageHost.append(this.image);this.photoPanel.append(this.imageHost);
    this.photoStatus=this.el('p','gv-caption');this.photoStatus.setAttribute('role','status');this.photoPanel.append(this.photoStatus);
    this.photoRetry=this.button('Try again',()=>this.loadPhoto(),'quiet');this.photoPanel.append(this.photoRetry);
    let pan;this.imageHost.onpointerdown=e=>{if(this.zoom<=1||e.button!==0)return;pan=[e.clientX,e.clientY,this.imageHost.scrollLeft,this.imageHost.scrollTop];this.imageHost.setPointerCapture(e.pointerId);e.preventDefault();};
    this.imageHost.onpointermove=e=>{if(pan){this.imageHost.scrollLeft=pan[2]+pan[0]-e.clientX;this.imageHost.scrollTop=pan[3]+pan[1]-e.clientY;}};this.imageHost.onpointerup=this.imageHost.onpointercancel=()=>{pan=null;};
    this.nextPanel=this.el('div','gv-next');this.main.append(this.nextPanel);
    this.compact=this.el('div','gv-compact');this.main.append(this.compact);
    this.details=this.el('div','gv-details');this.main.append(this.details);
    dialog.addEventListener('close',()=>this.release());
  }
  el(tag,className='',text){const n=document.createElement(tag);if(className)n.className=className;if(text!=null)n.textContent=String(text);return n;}
  button(label,fn,className='quiet'){const b=this.el('button',className,label);b.type='button';b.onclick=fn;return b;}
  label(row){const type={door:'Door',window:'Window',wall:'Wall',floor:'Floor',ceiling:'Ceiling'}[row?.source.type]||row?.source.type||'Object';const peers=(this.data?.observations||[]).filter(r=>r.source.type===row?.source.type);return `${type} ${Math.max(1,peers.findIndex(r=>r.id===row?.id)+1)}`;}
  statusLabel(row){return {confirmed:'Confirmed discrepancy',rejected:'Not confirmed',needs_more_data:'More data needed'}[row?.decision?.status]||'Not reviewed';}
  current(){return this.data?.observations.find(r=>r.id===this.selected)||null;}
  async open(chatId,{sessionId=null,scope='findings',caseId=null}={}){
    const same=chatId===this.chatId&&sessionId===this.sessionId;
    if(!same){this.release();this.data=null;this.selected=null;this.frame=0;this.role='rgb';}
    this.chatId=chatId;this.sessionId=sessionId;this.mode=scope;this.view=scope==='model'?'model':'photo';this.main.scrollTop=0;
    if(!this.dialog.open)this.dialog.showModal();
    await this.reload(caseId);
  }
  async reload(caseId=null){
    const version=++this.generation;this.notice.textContent='Loading inspection results…';this.notice.classList.remove('gv-error');
    try{
      const path=`/v1/chats/${encodeURIComponent(this.chatId)}/geometry-view`+(this.sessionId?`?session_id=${encodeURIComponent(this.sessionId)}`:'');
      const data=await this.api(path);if(version!==this.generation||!this.dialog.open)return;
      const changed=this.data?.geometry_job_id!==data.geometry_job_id;this.data=data;this.sessionId=data.session_id;
      if(changed){this.modelGeneration++;this.modelKey=null;this.model.clear();}
      this.selected=(caseId&&data.observations.find(r=>r.case_id===caseId)?.id)||data.observations.find(r=>r.id===this.selected)?.id||data.observations.find(r=>r.decision)?.id||data.observations[0]?.id||null;
      this.render();
    }catch(error){if(version!==this.generation)return;this.notice.classList.add('gv-error');this.notice.replaceChildren(this.el('span','',error.message||'Results are unavailable.'),this.button('Try again',()=>this.reload(),'quiet'));}
  }
  sync(chatId,signature){if(this.dialog.open&&this.chatId===chatId&&signature!==this.signature){this.signature=signature;this.reload();}}
  setMode(mode){this.mode=mode;if(mode==='model')this.view='model';this.main.scrollTop=0;this.render();}
  select(row){this.selected=row.id;this.frame=0;this.role='rgb';this.zoom=1;this.main.scrollTop=0;this.render();if(this.view==='model'&&this.mode==='findings')this.focusModel();}
  selectGuid(guid){const rows=this.data?.observations.filter(r=>r.source.ifc_guid===guid)||[];if(rows.length===1)this.select(rows[0]);else{if(rows.length){this.mode='findings';this.list='all';this.render();}this.modelStatus.textContent=rows.length?'This IFC element has several review entries. Select one in Findings.':'No captured observation is linked to this IFC element.';}}
  render(){
    this.root.dataset.mode=this.mode;for(const [key,b] of Object.entries(this.modeButtons))b.setAttribute('aria-pressed',String(key===this.mode));
    const data=this.data,row=this.current();this.tabs.hidden=this.mode==='model';this.status.hidden=this.mode==='model'||!row;
    this.heading.textContent=this.mode==='model'?'Reference model and captured geometry':row?this.label(row):'Inspection results';
    this.status.textContent=this.statusLabel(row);this.status.dataset.status=row?.decision?.status||'unreviewed';
    this.provenance.textContent=data.origin.is_synthetic?'Synthetic / demonstration source':data.origin.analysis_origin==='recorded_model'?'Recorded capture':data.origin.analysis_origin==='live_model'?'Captured source':'Source not yet processed';
    const reviewed=data.observations.filter(r=>r.decision).length;
    this.notice.textContent=data.review_status==='cancelled'?'Review cancelled. Accepted geometry remains available.':
      !data.geometry_job_id?'Geometry is not ready. The pinned reference model can still be viewed.':
      data.review_status==='completed'?`${reviewed} reviewed entries. Model correspondence alone does not establish compliance.`:
      data.review_status==='not_requested'?'Geometry observations are available. An inspection review has not been requested.':
      `Review: ${this.reviewLabel(data.review_status)} · ${reviewed} / ${data.observations.length} entries reviewed.`;
    const options=data.artifacts.map(a=>`${a.artifact_id}:${a.sha256}`).join('|');
    if(options!==this.modelOptions){this.modelOptions=options;const selected=this.modelSelect.value;this.modelSelect.replaceChildren();const order=['ifc','mesh','segmented_mesh','point_cloud'];for(const a of [...data.artifacts].sort((a,b)=>order.indexOf(a.artifact_id)-order.indexOf(b.artifact_id))){const option=this.el('option','',({ifc:'IFC reference',mesh:'Captured mesh',segmented_mesh:'Segmented mesh',point_cloud:'Point cloud'}[a.artifact_id]||a.artifact_id)+(a.available?'':' · exceeds preview limit'));option.value=a.artifact_id;option.disabled=!a.available;this.modelSelect.append(option);}if(data.artifacts.some(a=>a.artifact_id===selected&&a.available))this.modelSelect.value=selected;}
    this.renderList();this.renderDetails();this.renderMaterials();
  }
  reviewLabel(status){return {waiting_geometry:'waiting for geometry',preparing_evidence:'preparing evidence',reviewing:'checking evidence',summarizing:'preparing response',waiting_configuration:'waiting for service configuration',retrying:'retrying',failed:'needs attention'}[status]||status;}
  renderList(){
    const all=this.data?.observations||[],rows=this.list==='all'?all:all.filter(r=>r.decision);this.rows.replaceChildren();
    this.mobilePicker.replaceChildren();for(const row of rows){const option=this.el('option','',this.label(row)+' · '+this.statusLabel(row));option.value=row.id;this.mobilePicker.append(option);}this.mobilePicker.value=this.selected||'';this.mobilePicker.disabled=!rows.length;
    if(!rows.length){const option=this.el('option','','No reviewed entries — choose All objects');option.value='';this.mobilePicker.append(option);}
    for(const [key,b] of Object.entries(this.listButtons))b.setAttribute('aria-pressed',String(key===this.list));
    for(const row of rows){const b=this.button('',()=>this.select(row),'gv-row');b.setAttribute('aria-pressed',String(row.id===this.selected));b.append(this.el('strong','',this.label(row)));const status=this.el('span','gv-status',this.statusLabel(row));status.dataset.status=row.decision?.status||'unreviewed';b.append(status);if(row.decision)b.append(this.el('span','gv-row-reason',row.decision.decision_reason));this.rows.append(b);}
    if(!rows.length){this.rows.append(this.el('p','gv-empty','No accepted review decisions yet. Captured observations are available under All objects.'));}
  }
  renderDetails(){
    const row=this.current();this.details.hidden=this.mode==='model';this.compact.hidden=this.mode!=='model';this.details.replaceChildren();this.compact.replaceChildren();this.nextPanel.replaceChildren();
    if(!row){this.details.append(this.el('p','gv-empty','No object-level observations are available yet.'));this.nextPanel.append(this.el('p','gv-empty','No additional data requests are available.'));return;}
    this.compact.append(this.el('strong','',this.label(row)+' · '+this.statusLabel(row)),this.button('Open finding and photos',()=>{this.mode='findings';this.view='photo';this.main.scrollTop=0;this.render();},'quiet'));
    const decision=row.decision;
    const section=(title,text)=>{const group=this.el('section');group.append(this.el('h3','',title),this.el('p','',text));this.details.append(group);};
    section('Finding',decision?.decision_reason||'This is a Geometry observation. No inspection decision has been accepted for this entry.');
    if(decision?.detection_reason)section('Reason for review',decision.detection_reason);
    const source=row.source,limitations=[...(decision?.limitations||[])];
    if(source.quality?.boundary_supported_on_all_sides!==true&&source.quality)limitations.push('Boundaries are not supported on every side.');
    if(source.quality?.absolute_accuracy_verified!==true&&source.quality)limitations.push('Absolute measurement accuracy is not verified.');
    if(source.whole_element_verified===false)limitations.push('These statistics cover the observed area; the whole element is not verified.');
    if(limitations.length)section('Limitations',[...new Set(limitations)].join('\n'));
    if(source.quality?.issues?.length)section('Reported quality issues',source.quality.issues.map(s=>s.replaceAll('_',' ')).join('\n'));
    if(source.dimensions){const table=this.el('table','gv-measurements');const head=this.el('tr');for(const label of ['Measurement','IFC','Scan','Difference'])head.append(this.el('th','',label));table.append(head);for(const [key,value] of Object.entries(source.dimensions)){const tr=this.el('tr');for(const text of [key,...['ifc','scan','difference'].map(k=>this.mm(value[k]))])tr.append(this.el('td','',text));table.append(tr);}this.details.append(table);}
    if(source.displacement){const values=Object.entries(source.displacement).filter(([,v])=>v!=null);if(values.length)section('Reported displacement',values.map(([key,v])=>key.replaceAll('_',' ')+': '+this.mm(v)).join('\n'));}
    if(row.kind==='surface'){section('Observed surface statistics',`Compared area: ${this.number(source.compared_area_m2)} m²\nP95 absolute deviation: ${this.mm(source.p95_absolute_deviation)}\nArea outside processing tolerance: ${source.area_fraction_outside_tolerance==null?'Not reported':this.number(source.area_fraction_outside_tolerance*100)+'%'}`);}
    const processing=this.data.comparison.tolerance,acceptance=this.data.acceptance_tolerance_m;
    if(processing!=null)section('Tolerance',`Geometry processing parameter: ${this.mm(processing)}. Project acceptance rule: ${acceptance==null?'not supplied':this.mm(acceptance)}.`);
    const requests=decision?.data_requests||[];this.nextPanel.append(this.el('h3','',requests.length?'Additional evidence requested':'Next steps'));
    if(requests.length){const list=this.el('ol');for(const request of requests)list.append(this.el('li','',request));this.nextPanel.append(list);}
    else this.nextPanel.append(this.el('p','',decision?'No additional evidence was requested in this accepted decision.':'Additional data requests will appear after a review decision is accepted.'));
    this.nextPanel.append(this.el('p','gv-caption','Camera positions and safe viewing directions are not supplied. No precise capture points are inferred.'));
    if(row.frames.length)this.nextPanel.append(this.button('View captured photos',()=>{this.view='photo';this.renderMaterials();},'quiet'));
    if(this.data.result_file_id)this.details.append(this.button('Download complete review JSON',async()=>{try{const blob=await this.api(`/v1/chats/${encodeURIComponent(this.chatId)}/files/${encodeURIComponent(this.data.result_file_id)}/download`,{blob:true});const url=URL.createObjectURL(blob),a=this.el('a');a.href=url;a.download='geometry-review-report.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){this.notice.textContent=e.message;}},'quiet'));
  }
  number(v){return typeof v==='number'&&Number.isFinite(v)?new Intl.NumberFormat(undefined,{maximumFractionDigits:2}).format(v):'Not reported';}
  mm(v){return typeof v==='number'&&Number.isFinite(v)?this.number(v*1000)+' mm':'Not reported';}
  renderMaterials(){
    for(const [key,b] of Object.entries(this.tabButtons))b.setAttribute('aria-pressed',String(key===this.view));
    this.modelPanel.hidden=this.view!=='model';this.photoPanel.hidden=this.view!=='photo';this.nextPanel.hidden=this.view!=='next';
    if(this.view==='model'){this.clearPhoto();this.loadModel();}
    else{this.modelGeneration++;this.modelKey=null;this.model.clear();if(this.view==='photo')this.loadPhoto();else this.clearPhoto();}
  }
  async loadModel(){
    if(!this.data||this.view!=='model')return;const kind=(this.data.artifacts.find(a=>a.artifact_id===this.modelSelect.value&&a.available)||this.data.artifacts.find(a=>a.available))?.artifact_id;
    this.legend.hidden=kind!=='ifc'||!this.data.observations.some(r=>r.decision&&r.source.ifc_guid);
    if(!kind){this.modelStatus.textContent='No model is available for browser preview.';return;}
    const key=this.data.geometry_job_id+':'+this.sessionId+':'+kind;if(this.modelKey===key){this.bindMarkers();return;}
    const version=++this.modelGeneration;this.modelKey=key;this.model.clear();this.modelStatus.textContent='Loading model…';
    try{const blob=await this.api(this.assetPath('models',kind),{blob:true});if(version!==this.modelGeneration||!this.dialog.open)return;await this.model.openArtifact(kind,blob);if(version!==this.modelGeneration)return;this.bindMarkers();if(kind==='ifc'&&this.mode==='findings')this.focusModel();}
    catch(e){if(version===this.modelGeneration){this.modelKey=null;this.modelStatus.replaceChildren(this.el('span','',e.message||'Model unavailable.'),this.button('Try again',()=>this.loadModel(),'quiet'));}}
  }
  bindMarkers(){this.model.setFindings(this.data.observations.filter(r=>r.decision).map(r=>({id:r.id,guid:r.source.ifc_guid,status:r.decision.status,label:this.label(r)+' · '+this.statusLabel(r)})),this.selected);}
  focusModel(){const row=this.current();if(!row)return;if(this.modelSelect.value!=='ifc'){this.modelStatus.textContent='Per-segment positions are not supplied for this mesh. View the photo evidence or select the IFC reference.';return;}if(!this.model.focusGlobalId?.(row.source.ifc_guid))this.modelStatus.textContent='No unique IFC element can be focused for this observation. Photo evidence remains available.';}
  assetPath(kind,id){return `/v1/chats/${encodeURIComponent(this.chatId)}/geometry-view/${encodeURIComponent(this.sessionId)}/${kind}/${encodeURIComponent(id)}`;}
  clearPhoto(){this.assetGeneration++;if(this.imageUrl)URL.revokeObjectURL(this.imageUrl);this.imageUrl=null;this.image.removeAttribute('src');this.image.hidden=true;this.photoKey=null;}
  async loadPhoto(){
    if(this.view!=='photo')return;const row=this.current(),frames=row?.frames||[];this.frame=Math.max(0,Math.min(this.frame,frames.length-1));
    const frame=frames[this.frame];this.frameSelect.replaceChildren();frames.forEach((f,i)=>{const option=this.el('option','',f.frame_id?`Frame ${i+1} / ${frames.length}`:`Image ${i+1} · frame unreported`);option.value=String(i);this.frameSelect.append(option);});this.frameSelect.value=String(this.frame);this.frameSelect.disabled=frames.length<2;
    if(!frame?.views[this.role])this.role=frame?.views.rgb?'rgb':frame?.views.overlay?'overlay':frame?.views.mask?'mask':frame?.views.unreported?'unreported':'rgb';
    for(const [role,b] of Object.entries(this.roleButtons)){b.disabled=!frame?.views[role];b.hidden=role==='unreported'&&!frame?.views.unreported;b.setAttribute('aria-pressed',String(role===this.role));}
    this.photoRetry.hidden=true;const view=frame?.views[this.role];
    if(!view){this.clearPhoto();this.photoStatus.textContent=frames.length?'The accepted result has no available image binding for this frame.':'No photo evidence is supplied for this entry.';return;}
    const key=row.id+':'+frame.frame_id+':'+this.role+':'+view.image_id;if(this.photoKey===key)return;this.clearPhoto();this.photoKey=key;
    const version=this.assetGeneration;this.photoStatus.textContent='Loading verified evidence…';
    try{const blob=await this.api(this.assetPath('images',view.image_id),{blob:true});if(version!==this.assetGeneration||!this.dialog.open)return;this.imageUrl=URL.createObjectURL(blob);this.image.src=this.imageUrl;await this.image.decode();if(version!==this.assetGeneration)return;this.image.hidden=false;this.applyZoom();this.photoStatus.textContent=(this.role==='rgb'?'Original captured image. ':this.role==='overlay'?'Geometry overlay for this same captured frame. ':this.role==='mask'?'Segmentation mask for this same captured frame. ':'Captured image; its role and frame association were not reported. ')+(view.used_in_review===true?'Image cited by the accepted review.':'Captured evidence; it may include views not cited by the review.');}
    catch(e){if(version===this.assetGeneration){this.photoKey=null;this.photoStatus.textContent=e.message||'Evidence unavailable. Try again.';this.photoRetry.hidden=false;}}
  }
  zoomPhoto(factor){this.zoom=Math.min(5,Math.max(1,this.zoom*factor));this.applyZoom();}
  applyZoom(){this.imageHost.classList.toggle('gv-zoomed',this.zoom>1);this.image.style.width=this.zoom>1?`${this.zoom*100}%`:'';if(this.zoom===1){this.imageHost.scrollLeft=0;this.imageHost.scrollTop=0;}}
  release(){this.generation++;this.modelGeneration++;this.clearPhoto();this.model.clear();this.modelKey=null;}
  clear(){if(this.dialog.open)this.dialog.close();else this.release();this.data=null;this.chatId=null;this.sessionId=null;this.modelOptions=null;this.selected=null;this.signature=null;}
};
