'use strict';
const el = id => document.getElementById(id);
const project = new URLSearchParams(location.search).get('project');
const embeddedChat = location.pathname.startsWith('/ui/progress4d/') ? new URLSearchParams(location.search).get('chat') : null;
const embedded = /^[0-9a-f-]{36}$/i.test(embeddedChat || '');
const key = `progress4d:view:${project}`;
const incoming = new URLSearchParams(location.hash.slice(1)).get('access');
let token = embedded ? sessionStorage.getItem('inspector-operator-session') || '' : incoming || sessionStorage.getItem(key) || '';
if (incoming && !embedded) { sessionStorage.setItem(key, incoming); history.replaceState(null, '', location.pathname + location.search); }
let poller=null,activeTask=null,activeElement=null;
const phaseNames={frame:'Каркас',services:'Инженерные сети',boards:'Обшивка',finish:'Отделка стен',floor:'Пол',door:'Двери'};
const taskTitle=t=>phaseNames[t?.name]||t?.name||'';
function productTitle(p){const match=/^(conduit|outlet|frame|track|board|finish)-(\d+)$/.exec(p.name||'');return match?({conduit:'Кабельная трасса',outlet:'Розетка',frame:'Стойка каркаса',track:'Направляющая',board:'Панель обшивки',finish:'Участок отделки'}[match[1]]+' '+(Number(match[2])+1)):p.name;}
const surfaceBuffers=new Map();
const visualArtifact=s=>s?.result?.artifacts?.find(a=>a.name==='surface.glb')||s?.result?.artifacts?.find(a=>a.name==='synthetic-scene.glb');
const viewBounds=s=>s?.result?.visualization?.room_bounds_m||s?.result?.registration?.room_bounds_m;
async function surfaceBuffer(job,name){
 if(surfaceBuffers.has(job))return surfaceBuffers.get(job);
 const path=`${base()}/jobs/${job}/artifacts/${encodeURIComponent(name)}`,chunk=1024*1024;
 let data=null,total=null,offset=0;
 do {
  const response=await fetch(path,{headers:{Authorization:`Bearer ${token}`,Range:`bytes=${offset}-${offset+chunk-1}`},cache:'no-store',redirect:'error'});
  if(response.status===401)throw new Error('Доступ истёк. Откройте результат заново из Inspector.');
  if(!response.ok)throw new Error(`Получение GLB (${response.status})`);
  if(response.status===200){if(offset)throw new Error('Сервер изменил режим передачи модели');data=new Uint8Array(await response.arrayBuffer());break;}
  const range=/^bytes (\d+)-(\d+)\/(\d+)$/.exec(response.headers.get('content-range')||'');
  if(response.status!==206||!range||Number(range[1])!==offset)throw new Error('Некорректный диапазон модели');
  const size=Number(range[3]);if(size>128*1024*1024||size<12||total!==null&&size!==total)throw new Error('Некорректный размер модели');
  if(!data){total=size;data=new Uint8Array(total);}
  const part=new Uint8Array(await response.arrayBuffer());
  if(part.length!==Number(range[2])-offset+1||!part.length||offset+part.length>total)throw new Error('Неполная передача модели');
  data.set(part,offset);offset+=part.length;
 }while(offset<total);
 const buffer=data.buffer;surfaceBuffers.set(job,buffer);while(surfaceBuffers.size>3)surfaceBuffers.delete(surfaceBuffers.keys().next().value);return buffer;
}

let timeline = null, selected = null, points = [], yaw = .3, pitch = -.3, zoom = 1, generation = 0;
let cloudCenter=null,cloudRadius=1;
const canvas = el('cloud'), ctx = canvas.getContext('2d');
let surfaceView=null;try{surfaceView=ProgressSurface.create(el('surface'));}catch(error){console.warn('WebGL surface unavailable',error.message);}

const stages={reconstruction:'Реконструкция по фото',registration:'Привязка к IFC',dense_reconstruction:'Восстановление глубины',surface_reconstruction:'Построение поверхности',surface_texturing:'Текстурирование фотографиями',visual_product_analysis:'Проверка элементов на фото'};
const names = {queued:'В очереди',reconstructing:'Восстановление геометрии',sparse_complete:'Разреженная реконструкция получена',inconclusive:'Недостаточно данных',complete:'Обработка завершена',failed:'Обработка не завершилась'};
async function get(path, binary=false) {
  const response = await fetch(path, {headers:{Authorization:`Bearer ${token}`},cache:'no-store',redirect:'error'});
  if (response.status === 401) { token='';sessionStorage.removeItem(key); throw new Error('Доступ истёк. Откройте результат заново из Inspector.'); }
  if (!response.ok) throw new Error(`Результат недоступен (${response.status}).`);
  if(binary){try{return await response.blob();}catch(error){throw new Error('Чтение файла ('+response.status+'): '+error.message);}}return response.json();
}
const base = () => embedded ? `/v1/chats/${encodeURIComponent(embeddedChat)}/progress4d/data` : `../v1/projects/${encodeURIComponent(project)}`;
function date(value) { return new Date(value).toLocaleString('ru-RU',{dateStyle:'medium',timeStyle:'short'}); }
function draw(){}
async function select() {
  const epoch=++generation;surfaceView?.cancelPending();
  selected=timeline.surveys.find(s=>s.job_id===el('survey').value);points=[];cloudCenter=null;
  const keepsSurface=surfaceView&&visualArtifact(selected);
  if(!keepsSurface){surfaceView?.clear();el('surface').hidden=true;canvas.hidden=false;}draw();renderWeeks();
  el('download').disabled=true;el('empty').hidden=false;
  if(!selected){el('status').textContent='Съёмок пока нет';return;}
  sessionStorage.setItem(key+':survey',selected.job_id);
  const snapshot=selected;
  el('status').textContent=`${date(snapshot.captured_at)} · ${snapshot.zone_id}${['queued','reconstructing'].includes(snapshot.status)?' · '+(stages[snapshot.stage]||names[snapshot.status]):''}`;
  el('quality').textContent=snapshot.result?.geometric_quality==='failed_evaluation'
    ?'Эта реконструкция не прошла проверку точности. Её нельзя использовать для измерений и оценки объёмов работ.'
    :snapshot.result?.registration?.status==='registered'?'Реконструкция в системе координат IFC, метры. Невосстановленные поверхности остаются пустыми; точность тонких слоёв не подтверждена.':'Совмещение с проектом не подтверждено. Измерения недоступны.';
  renderReport(snapshot); renderEvidence(snapshot);renderElements();
  el('empty').textContent='Геометрия для просмотра пока недоступна';
  const artifacts=snapshot.result?.artifacts||[];
  el('download').disabled=!snapshot.result;
  if(surfaceView&&visualArtifact(snapshot)){
    el('empty').textContent='Загрузка фактической поверхности…';
    try{const buffer=await surfaceBuffer(snapshot.job_id,visualArtifact(snapshot).name);if(epoch!==generation)return;
      canvas.hidden=true;el('surface').hidden=false;await surfaceView.load(buffer,viewBounds(snapshot),visualArtifact(snapshot).name==='synthetic-scene.glb');
      if(epoch!==generation)return;surfaceView.clip(el('cutaway').checked);applyView();el('empty').hidden=true;

    }catch(error){console.warn('Surface load failed',error.stack||error.message);if(epoch===generation){el('status').textContent='Поверхность не загружена: '+error.message;el('empty').textContent='Не удалось загрузить поверхность. Нажмите «Обновить».';}}return;
  }
  applyView();

}
async function refresh() {
  el('refresh').disabled=true;
  try {
    if(!/^[0-9a-f-]{36}$/i.test(project||''))throw new Error('Откройте результат из Inspector.');
    timeline=await get(`${base()}/timeline`);
    const previous=selected?.job_id||sessionStorage.getItem(key+':survey');
    el('survey').replaceChildren(...timeline.surveys.map(s=>{const option=document.createElement('option');option.value=s.job_id;option.textContent=`${date(s.captured_at)} — ${s.zone_id}`;return option;}));
    if(previous&&timeline.surveys.some(s=>s.job_id===previous))el('survey').value=previous;
    else if(timeline.surveys.length)el('survey').value=timeline.surveys.at(-1).job_id;
    await select();
  } catch(error) {el('status').textContent=error.message;}
  finally{el('refresh').disabled=false;clearTimeout(poller);if(token&&['queued','reconstructing'].includes(selected?.status))poller=setTimeout(refresh,5000);}
}
el('view-mode').onchange=applyView;el('cutaway').onchange=()=>surfaceView?.clip(el('cutaway').checked);
el('reset-view').onclick=()=>{const bounds=viewBounds(selected);if(bounds)surfaceView?.frame(bounds);yaw=.3;pitch=-.3;zoom=1;draw();};
el('refresh').onclick=refresh;el('survey').onchange=()=>{stopPlayback();select();};
el('download').onclick=async()=>{
  if(!selected)return;
  const snapshot=selected;
  try{const blob=await get(`${base()}/report`,true);
    const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`progress-${project}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(error){el('status').textContent=error.message;}
};
refresh();

let playback=null,evidenceUrl=null,evidenceGeneration=0;
function stopPlayback(){if(playback)clearTimeout(playback);playback=null;el('play').textContent='▶ Все недели';}
function renderWeeks(){
  el('week-tabs').replaceChildren(...(timeline?.surveys||[]).map((s,i)=>{
    const button=document.createElement('button');button.className='week-tab';button.setAttribute('aria-current',String(s.job_id===selected?.job_id));
    const available=visualArtifact(s);if(!available)button.classList.add('pending');
    const title=document.createElement('strong');title.textContent=`Неделя ${i+1}`;const subtitle=document.createElement('small');subtitle.textContent=new Date(s.captured_at).toLocaleDateString('ru-RU',{day:'numeric',month:'short'});
    button.append(title,subtitle);button.onclick=()=>{stopPlayback();el('survey').value=s.job_id;select();};return button;
  }));
}
el('play').onclick=async()=>{
  if(playback){stopPlayback();return;}
  const ready=timeline.surveys.filter(s=>visualArtifact(s));
  if(!ready.length)return;
  el('play').textContent='Ⅱ Остановить';let index=0;
  async function advance(){if(!playback)return;el('survey').value=ready[index].job_id;await select();if(!playback)return;index=(index+1)%ready.length;playback=setTimeout(advance,2800);}
  playback=setTimeout(advance,0);
};
function renderProgress(snapshot,historical){
 const rows=historical?.tasks||[],byId=new Map(rows.map(t=>[t.task_id,t]));
 const confirmed=rows.reduce((n,t)=>n+t.currently_observed_present+t.retained_past_present,0),total=rows.reduce((n,t)=>n+t.total_products,0);
 const complete=rows.filter(t=>t.currently_observed_present+t.retained_past_present===t.total_products&&t.total_products>0).length;
 const week=timeline.surveys.findIndex(s=>s.job_id===snapshot.job_id)+1;
 el('summary').replaceChildren(...[[`${week} / ${timeline.surveys.length}`,'Неделя наблюдения'],[`${complete} / ${timeline.schedule.tasks.length}`,'Этапов полностью подтверждено на эту дату']].map(([value,label])=>{const box=document.createElement('div');box.className='metric';const b=document.createElement('strong');b.textContent=value;const span=document.createElement('span');span.textContent=label;box.append(b,span);return box;}));
 el('phase-cards').replaceChildren(...timeline.schedule.tasks.map(task=>{
  const t=byId.get(task.id),count=t?t.currently_observed_present+t.retained_past_present:0,total=t?.total_products||task.products.length,percent=total?Math.round(100*count/total):0;
  const late=historical?.delay_impacts?.some(x=>x.task_id===task.id),done=total>0&&count===total;
  const card=document.createElement('button');card.className='phase';card.type='button';card.setAttribute('aria-pressed',String(activeTask===task.id));card.onclick=()=>{chooseTask(task.id);document.querySelector('.viewer-panel').scrollIntoView({behavior:'smooth',block:'start'});};const head=document.createElement('div');head.className='phase-head';const name=document.createElement('h3');name.textContent=taskTitle(task);const badge=document.createElement('span');badge.className='badge '+(done?'complete':late?'late':count?'active':'');badge.textContent=done?'Подтверждено':late?'Отставание':!t?'Обрабатывается':count?(t.currently_observed_absent===0&&t.unknown>0?'Частично видно':'В работе'):t.unknown===total?'Нет данных':'Не подтверждено';head.append(name,badge);
  const track=document.createElement('div');track.className='progress-track';const fill=document.createElement('i');fill.style.width=percent+'%';track.append(fill);
  const foot=document.createElement('div');foot.className='phase-foot';const qty=document.createElement('b');qty.textContent=t?.unknown===total?'Недостаточно наблюдений':`${count} / ${total} · ${percent}%`;const plan=document.createElement('span');plan.textContent='План: '+new Date(task.finish).toLocaleDateString('ru-RU',{day:'numeric',month:'short'});foot.append(qty,plan);card.append(head,track,foot);return card;
 }));
}
function renderReport(snapshot){
  el('report-date').textContent='Состояние и прогноз на '+date(snapshot.captured_at);
  const report=timeline.report||{},historical=report.snapshots?.find(s=>s.job_id===snapshot.job_id);renderProgress(snapshot,historical);
  const observations=new Map((historical?.tasks||[]).map(t=>[t.task_id,t]));
  const forecasts=new Map((historical?.delay_scenarios||report.delay_scenarios||[]).map(t=>[t.task_id,t]));
  const isLatest=Boolean(historical);
  const taskName=id=>taskTitle(timeline.schedule.tasks.find(t=>t.id===id))||id;
  el('delay-summary').textContent=isLatest?(historical?.delay_impacts||[]).map(x=>`${taskName(x.task_id)}: после планового окончания ${x.overdue_days.toFixed(1)} дн., часть элементов явно отсутствует. Зависимые работы: ${x.affected_successors.map(taskName).join(', ')||'нет'}.`).join(' '):'';
  el('tasks').replaceChildren(...(timeline.schedule?.tasks||[]).map(task=>{
    const t=observations.get(task.id),f=forecasts.get(task.id),row=document.createElement('tr');
    const estimate=isLatest&&f?.finish?`${/Scenario|Conditional/.test(f.basis||'')?'Сценарий: ':''}${date(f.finish)}${f.delay_days>0?' · задержка '+f.delay_days.toFixed(1)+' дн.':''}`:'Недостаточно данных';
    const values=[taskTitle(task),task.finish.slice(0,10),t?`${t.currently_observed_present} / ${t.total_products}`:'—',t?.retained_past_present??'—',t?.unknown??'—',isLatest?estimate:'Нет анализа на эту дату'];
    values.forEach((text,i)=>{const cell=document.createElement('td');if(t&&i>=2&&i<=4){const button=document.createElement('button');button.className='count-link';button.textContent=String(text);button.setAttribute('aria-label',`${taskTitle(task)}: ${['','','видно сейчас','ранее подтверждено','неизвестно'][i]} ${text}`);button.onclick=()=>chooseTask(task.id,['','','present','retained','unknown'][i],true);cell.append(button);}else cell.textContent=String(text);if(text===estimate&&f?.basis)cell.title=f.basis;row.append(cell);});return row;
  }));
  const a=el('activity'),c=a.getContext('2d'),ratio=devicePixelRatio||1,w=a.clientWidth||600,h=220;
  a.width=w*ratio;a.height=h*ratio;c.scale(ratio,ratio);c.clearRect(0,0,w,h);
  const history=(report.snapshots||[]).filter(x=>new Date(x.captured_at)<=new Date(snapshot.captured_at));
  let before=new Set();const values=history.map((s,i)=>{const ids=new Set(s.tasks.flatMap(t=>t.known_present_product_ids||[]));const added=[...ids].filter(id=>!before.has(id)).length,removed=s.tasks.reduce((n,t)=>n+t.observed_removals.length,0);before=ids;const index=timeline.surveys.findIndex(x=>x.job_id===s.job_id);return {added,removed,index,label:`Неделя ${index+1}`};});
  el('activity-note').textContent='Зелёный — впервые подтверждённые элементы; оранжевый — наблюдаемый демонтаж. Первая неделя задаёт исходное состояние. Это изменения результата работ, а не присутствие рабочих.';
  if(!values.length)return;
  const max=Math.max(1,...values.map(v=>Math.max(v.added,v.removed))),step=(w-60)/Math.max(timeline.surveys.length,values.length),baseline=160;
  c.font='11px system-ui';c.textAlign='right';
  for(let i=0;i<4;i++){const y=baseline-i*40;c.strokeStyle='#edf1eb';c.beginPath();c.moveTo(35,y);c.lineTo(w-10,y);c.stroke();c.fillStyle='#95a095';c.fillText(String(Math.round(max*i/3)),28,y+4);}
  values.forEach((v,i)=>{const x=45+v.index*step,bw=Math.min(45,step*.38),height=v.added/max*120,removed=v.removed/max*120;
    c.fillStyle=i===values.length-1?'#386f4e':'#91b29a';c.beginPath();c.roundRect(x,baseline-height,bw,Math.max(2,height),[4,4,0,0]);c.fill();
    if(v.removed){c.fillStyle='#c59058';c.fillRect(x+bw+4,baseline-removed,bw*.45,removed);}
    c.textAlign='center';c.fillStyle='#3f5c48';c.fillText(String(v.added),x+bw/2,baseline-height-9);c.fillStyle='#7b887c';c.fillText(v.label,x+bw/2,baseline+24);
  });
}
function renderEvidence(snapshot){
  const resolved=new Map((timeline.report?.snapshots?.find(s=>s.job_id===snapshot.job_id)?.products||[]).map(p=>[p.id,p]));
  const items=(snapshot.result?.perception?.products||currentElements()).map(p=>({...p,...resolved.get(p.id)})).filter(p=>!activeTask||p.task_id===activeTask);
  el('evidence-select').replaceChildren(...items.map(p=>{const option=document.createElement('option');option.value=p.label;option.textContent=productTitle(p)+' · '+({present:'виден',absent:'отсутствует',occluded:'закрыт',uncertain:'неопределённо'}[p.state]||p.state);return option;}));
  el('evidence-select').onchange=()=>loadEvidence(snapshot,items);
  loadEvidence(snapshot,items);
}
async function loadEvidence(snapshot,items){
  el('evidence-date').textContent='Фото от '+date(snapshot.captured_at);
  const epoch=++evidenceGeneration,p=items.find(p=>p.label===el('evidence-select').value);
  el('evidence-image').hidden=true;el('evidence-reason').textContent=p?.reason||'Нет результата анализа';
  if(evidenceUrl){URL.revokeObjectURL(evidenceUrl);evidenceUrl=null;}
  if(!p||p.views.length<2)return;
  try{const blob=await get(`${base()}/jobs/${snapshot.job_id}/artifacts/${p.label}.jpg`,true);if(epoch!==evidenceGeneration)return;evidenceUrl=URL.createObjectURL(blob);el('evidence-image').src=evidenceUrl;el('evidence-image').hidden=false;}catch(error){if(epoch===evidenceGeneration)el('evidence-reason').textContent=error.message;}
}

function currentElements(){
 const snapshot=timeline?.report?.snapshots?.find(s=>s.job_id===selected?.job_id),resolved=new Map((snapshot?.products||[]).map(p=>[p.id,p]));
 const known=new Set((snapshot?.tasks||[]).flatMap(t=>t.known_present_product_ids||[]));
 const source=selected?.result?.perception?.products||[...timeline.surveys].reverse().find(s=>new Date(s.captured_at)<new Date(selected.captured_at)&&s.result?.perception?.products)?.result.perception.products?.map(p=>({...p,state:'uncertain',views:[],reason:'Нет нового подтверждения на выбранную дату.'}))||[];
 return source.map(p=>{const item={...p,...resolved.get(p.id)};item.category=item.state==='present'?'present':item.state==='absent'?'absent':known.has(p.id)?'retained':'unknown';return item;});
}
function applyView(){
 const filter=el('element-filter').value;
 const products=currentElements().filter(p=>(!activeTask||p.task_id===activeTask)&&( !activeTask||filter==='all'||p.category===filter)&&(!activeElement||p.id===activeElement));
 surfaceView?.configure(products,el('view-mode').value,Boolean(activeTask));
 if(el('view-mode').value==='project'){canvas.hidden=true;el('surface').hidden=false;el('empty').hidden=products.length>0;}
 el('selection-bar').hidden=!activeTask;
 el('selection-label').textContent=activeTask?taskTitle(timeline.schedule.tasks.find(t=>t.id===activeTask))+(activeElement?' · выбран один элемент':' · область этапа по проекту'):'';
 if(visualArtifact(selected)?.name==='synthetic-scene.glb'){el('quality').textContent='3D-модель выбранной недели. Подтверждения работ и фотографии доступны в разделах ниже.';return;}
 if(selected?.result?.geometric_quality==='failed_evaluation'){el('quality').textContent='Эта реконструкция не прошла проверку точности. Её нельзя использовать для измерений и оценки объёмов работ.';return;}
 if(activeTask)el('quality').textContent='Выделены проектные области выбранного этапа. Съёмка обрезана по этим областям; скрытые конструкции не дорисовываются.';
 else el('quality').textContent=el('view-mode').value==='project'?'Проектные границы элементов IFC; это не фактическая реконструкция.':'Поверхность восстановлена по фотографиям. Невосстановленные участки остаются пустыми.';
}
function chooseTask(id,filter='all',scroll=false){
 activeTask=id;activeElement=null;el('element-filter').value=filter;
 if(el('view-mode').value==='surface')el('view-mode').value='combined';
 renderProgress(selected,timeline.report?.snapshots?.find(s=>s.job_id===selected.job_id));renderElements();renderEvidence(selected);applyView();
 if(scroll)el('element-panel').scrollIntoView({behavior:'smooth',block:'start'});
}
function renderElements(){
 el('element-panel').hidden=!activeTask;if(!activeTask)return;
 const all=currentElements().filter(p=>p.task_id===activeTask),filter=el('element-filter').value,items=all.filter(p=>filter==='all'||p.category===filter);
 el('element-title').textContent=taskTitle(timeline.schedule.tasks.find(t=>t.id===activeTask))+' · '+date(selected.captured_at)+' · Элементов: '+items.length;
 const labels={present:'Виден сейчас',retained:'Ранее подтверждён',unknown:'Неизвестно',absent:'Отсутствует'};
 const list=items.map(p=>{const row=document.createElement('div');row.className='element-row';
  const show=document.createElement('button');show.className='element-name';show.textContent=productTitle(p);show.setAttribute('aria-pressed',String(activeElement===p.id));show.onclick=()=>{activeElement=activeElement===p.id?null:p.id;applyView();renderElements();document.querySelector('.viewer-panel').scrollIntoView({behavior:'smooth',block:'start'});};
  const status=document.createElement('span');status.textContent=labels[p.category];const photo=document.createElement('button');photo.textContent='Фото и причина';photo.onclick=()=>{
   let source=selected,item=p;
   if(p.category==='retained'){const earlier=timeline.surveys.filter(s=>new Date(s.captured_at)<new Date(selected.captured_at)).reverse();for(const survey of earlier){const observed=survey.result?.perception?.products?.find(x=>x.id===p.id&&x.state==='present');if(observed){source=survey;item=observed;break;}}}
   el('evidence-select').value=p.label;loadEvidence(source,[item]);el('evidence-details').open=true;el('evidence-details').scrollIntoView({behavior:'smooth',block:'start'});
  };
  const detail=document.createElement('small');detail.textContent='IFC '+p.id;row.append(show,status,photo,detail);return row;});
 if(!list.length){const empty=document.createElement('p');empty.textContent='В этой категории элементов нет.';list.push(empty);}el('element-list').replaceChildren(...list);
}
el('element-filter').onchange=()=>{activeElement=null;renderElements();applyView();};
el('clear-selection').onclick=()=>{activeTask=null;activeElement=null;el('element-panel').hidden=true;renderProgress(selected,timeline.report?.snapshots?.find(s=>s.job_id===selected.job_id));renderEvidence(selected);applyView();};
