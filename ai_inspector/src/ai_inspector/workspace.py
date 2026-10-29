"""Chat-scoped files, planned work and operator observations. No model-owned verdicts."""
import csv
import io
import json
from datetime import datetime, timezone, timedelta
from pathlib import PurePosixPath
from typing import Annotated, Literal
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from pydantic import BaseModel, ConfigDict, Field
from .errors import APIError
from .storage import canonical_json

class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

FileIDs = Annotated[list[Annotated[str, Field(max_length=150)]], Field(max_length=8)]
class WorkItem(Input):
    revision: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)
    location: str = Field(default='', max_length=200)
    reference: str = Field(default='', max_length=300)
    deleted: bool = False

class Observation(Input):
    item_id: UUID
    status: Literal['confirmed', 'needs_review', 'not_done']
    note: str = Field(min_length=3, max_length=4000)
    author: str = Field(min_length=1, max_length=100)
    observed_at: datetime
    file_ids: FileIDs = Field(default_factory=list)

class ImportPlan(Input):
    request_id: UUID
    file_id: str = Field(max_length=150)


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_evidence(value):
    """Keep arbitrary artifact credentials/URLs out of model context."""
    if isinstance(value, dict):
        return {k: safe_evidence(v) for k,v in value.items() if not any(part in k.lower() for part in ('url','token','secret','object_key','bucket','storage'))}
    if isinstance(value, list):
        return [safe_evidence(v) for v in value[:100]]
    return value

class Workspace:
    def __init__(self, service):
        self.service = service
        self.db = service.database

    def check(self, conn, principal, chat_id):
        return self.service.agent._chat(conn, principal, chat_id)

    def files(self, conn, principal, chat_id):
        chat = self.check(conn, principal, chat_id)
        files = {r['file_id']: json.loads(r['data']) for r in conn.execute(
            'SELECT file_id, data FROM workspace_files WHERE chat_id=?', (chat_id,)).fetchall()}
        runs = [json.loads(r['data']) for r in conn.execute(
            'SELECT data FROM agent_runs WHERE chat_id=? ORDER BY sequence DESC LIMIT 200', (chat_id,)).fetchall()]
        for run in runs:
            if run.get('reply'):
                identity = 'result-' + run['run_id']
                if identity not in files:
                    content = canonical_json({'reply': run['reply'], 'tools': safe_evidence(run.get('tool_results', []))})
                    files[identity] = dict(file_id=identity, name='inspector-'+run['run_id'][:8]+'.json', origin='inspector',
                                          text=content[:16000], text_truncated=len(content)>16000, content=content,
                                          byte_length=len(content.encode()), created_at=run['updated_at'], deleted=False)
        ids = list(dict.fromkeys([chat.get('session_id')] + [r.get('session_id') for r in runs]))
        for identity in ids:
            if not identity:
                continue
            row = conn.execute('SELECT data FROM sessions WHERE session_id=? AND project_id=?', (identity,principal.project_id)).fetchone()
            if not row:
                continue
            session = json.loads(row['data'])
            if session.get('source') or session.get('geometry_result'):
                fid = 'capture-' + identity
                if fid not in files:
                    from .agent import session_summary
                    content = canonical_json({'session': session_summary(session), 'geometry_result': safe_evidence(session.get('geometry_result'))})
                    files[fid] = dict(file_id=fid, name='capture-'+identity[:8]+'.json', origin='capture', text=content[:16000],
                                      content=content, text_truncated=len(content)>16000, byte_length=len(content.encode()),
                                      created_at=session['created_at'], deleted=False)
            inspection = conn.execute('SELECT data FROM inspections WHERE inspection_id=?', (session['inspection_id'],)).fetchone()
            model = json.loads(inspection['data']).get('bim_model') if inspection else None
            if model:
                fid = 'bim-' + session['inspection_id']
                if fid not in files:
                    files[fid] = dict(file_id=fid, name=model['filename'], origin='reference', sha256=model['sha256'],
                                      byte_length=model['byte_length'], text=None, text_truncated=False,
                                      created_at=session['created_at'], deleted=False)
        return files

    def state(self, principal, chat_id):
        with self.db.connection() as conn:
            conn.execute('BEGIN')
            files = self.files(conn, principal, chat_id)
            items = [json.loads(r['data']) for r in conn.execute('SELECT data FROM workspace_items WHERE chat_id=? ORDER BY item_id', (chat_id,)).fetchall()]
            observations = [json.loads(r['data']) for r in conn.execute('SELECT data FROM workspace_observations WHERE chat_id=? ORDER BY observation_id', (chat_id,)).fetchall()]
        counts = dict(confirmed=0, needs_review=0, not_done=0, no_data=0)
        for item in items:
            active = [o for o in observations if o['item_id']==item['item_id'] and not o['withdrawn']]
            latest = max(active, key=lambda o:(o['observed_at'],o['created_at'],o['observation_id']), default=None)
            item['status'] = latest['status'] if latest else 'no_data'
            item['latest_observation'] = latest
            if not item['deleted']:
                counts[item['status']] += 1
        total = sum(counts.values())
        public_files = [{k:v for k,v in f.items() if k not in ('text','content','sha256')} |
                        {'content_available': f.get('text') is not None} for f in files.values()]
        return dict(files=public_files, items=items, observations=observations,
                    progress=dict(counts=counts,total=total,confirmed_percent=round(counts['confirmed']/total*100,1) if total else None,
                                  basis='operator observations; equal weight per planned item'))

    def upload(self, principal, chat_id, file_id, filename, source):
        file_id = str(file_id)
        name = PurePosixPath((filename or 'file').replace('\\','/')).name[:200]
        if not name or any(ord(c)<32 for c in name):
            raise APIError(422,'invalid_filename','Invalid file name')
        with self.db.connection() as conn:
            self.check(conn,principal,chat_id)
        with self.service.bim_store.stage(source) as staged:
            text = None
            if name.lower().endswith(('.txt','.md','.csv','.json','.ifc','.xml','.log')):
                with staged.path.open('rb') as handle:
                    raw = handle.read(64001)
                try:
                    text = raw[:64000].decode('utf-8-sig')[:16000]
                except UnicodeDecodeError:
                    text = None
            metadata = dict(file_id=file_id,name=name,origin='upload',sha256=staged.sha256,
                            byte_length=staged.byte_length,text=text,text_truncated=text is not None and staged.byte_length>len(text.encode()),
                            created_at=now(),deleted=False)
            with self.db.connection() as conn:
                row=conn.execute('SELECT data FROM workspace_files WHERE file_id=? AND chat_id=?',(file_id,chat_id)).fetchone()
                if row:
                    prior=json.loads(row['data'])
                    if prior['sha256']!=staged.sha256 or prior['name']!=name:
                        raise APIError(409,'idempotency_conflict','Upload ID has different content')
                    return {'file_id':file_id}
            self.service.bim_store.publish(staged)
        with self.db.transaction() as conn:
            self.check(conn,principal,chat_id)
            row=conn.execute('SELECT data FROM workspace_files WHERE file_id=? AND chat_id=?',(file_id,chat_id)).fetchone()
            if row:
                previous=json.loads(row['data'])
                if previous.get('sha256')!=metadata['sha256'] or previous.get('name')!=name:
                    raise APIError(409,'idempotency_conflict','Upload ID has different content')
            else:
                conn.execute('INSERT INTO workspace_files (file_id,chat_id,data) VALUES (?,?,?)',(file_id,chat_id,canonical_json(metadata)))
        return {'file_id':file_id}

    def trash(self,principal,chat_id,file_id,deleted):
        with self.db.transaction() as conn:
            file=self.files(conn,principal,chat_id).get(file_id)
            if not file:
                raise APIError(404,'not_found','File not found in this conversation')
            file['deleted']=deleted
            row=conn.execute('SELECT data FROM workspace_files WHERE file_id=? AND chat_id=?',(file_id,chat_id)).fetchone()
            if row:
                conn.execute('UPDATE workspace_files SET data=? WHERE file_id=? AND chat_id=?',(canonical_json(file),file_id,chat_id))
            else:
                conn.execute('INSERT INTO workspace_files (file_id,chat_id,data) VALUES (?,?,?)',(file_id,chat_id,canonical_json(file)))
        return {'file_id':file_id,'deleted':deleted}

    def download(self,principal,chat_id,file_id):
        with self.db.connection() as conn:
            file=self.files(conn,principal,chat_id).get(file_id)
        if not file or file['deleted']:
            raise APIError(404,'not_found','File not available')
        return file

    def context(self,conn,principal,chat_id,file_ids):
        files=self.files(conn,principal,chat_id)
        selected=[]
        for identity in dict.fromkeys(file_ids):
            file=files.get(identity)
            if not file or file['deleted']:
                raise APIError(422,'file_unavailable','A selected file is no longer available. Update your selection.')
            selected.append(dict(file_id=identity,name=file['name'],origin=file['origin'],
                                 text=(file.get('text') or '')[:10000],
                                 content_available=file.get('text') is not None,text_truncated=file.get('text_truncated',False) or len(file.get('text') or '')>10000))
        items=[json.loads(r['data']) for r in conn.execute('SELECT data FROM workspace_items WHERE chat_id=?',(chat_id,)).fetchall()]
        observations=[json.loads(r['data']) for r in conn.execute('SELECT data FROM workspace_observations WHERE chat_id=?',(chat_id,)).fetchall()]
        # Snapshot the user's selected evidence and plan with the durable request.
        counts = dict(confirmed=0,needs_review=0,not_done=0,no_data=0)
        summary=[]
        for item in items:
            if item['deleted']:
                continue
            matching=[o for o in observations if o['item_id']==item['item_id'] and not o['withdrawn']]
            latest=max(matching,key=lambda o:(o['observed_at'],o['created_at'],o['observation_id']),default=None)
            status=latest['status'] if latest else 'no_data'; counts[status]+=1
            summary.append(dict(item_id=item['item_id'],title=item['title'],location=item['location'],reference=item['reference'],
                                status=status,latest_observation={k:latest[k] for k in ('observed_at','author','note','evidence')} if latest else None))
        for item in summary:
            if item['latest_observation']:
                item['latest_observation']['note']=item['latest_observation']['note'][:400]
        return {'selected_files':selected,'progress_counts':counts,'planned_items':summary[:50],
                'items_truncated':len(summary)>50,'basis':'operator observations, not independently verified by AI'}

    def save_item(self,principal,chat_id,item_id,request):
        item_id=str(item_id); fields=request.model_dump(exclude={'revision'})
        with self.db.transaction() as conn:
            self.check(conn,principal,chat_id)
            row=conn.execute('SELECT data FROM workspace_items WHERE item_id=? AND chat_id=?',(item_id,chat_id)).fetchone()
            prior=json.loads(row['data']) if row else None
            if prior and prior['revision']==request.revision+1 and all(prior[k]==v for k,v in fields.items()):
                return prior
            if (prior['revision'] if prior else 0)!=request.revision:
                raise APIError(409,'item_conflict','Plan item changed. Reload before editing.')
            if not prior and conn.execute('SELECT COUNT(*) AS n FROM workspace_items WHERE chat_id=?',(chat_id,)).fetchone()['n']>=200:
                raise APIError(422,'plan_limit','At most 200 planned items per conversation')
            item=dict(fields,item_id=item_id,revision=request.revision+1,created_at=prior['created_at'] if prior else now())
            if prior and prior.get('plan_file_id'):
                item['plan_file_id']=prior['plan_file_id']
            if prior:
                conn.execute('UPDATE workspace_items SET data=? WHERE item_id=? AND chat_id=?',(canonical_json(item),item_id,chat_id))
            else:
                conn.execute('INSERT INTO workspace_items (item_id,chat_id,data) VALUES (?,?,?)',(item_id,chat_id,canonical_json(item)))
        return item

    def observe(self,principal,chat_id,observation_id,request):
        observed=request.observed_at
        if observed.tzinfo is None or observed>datetime.now(timezone.utc)+timedelta(minutes=5):
            raise APIError(422,'invalid_observation_time','Use a timezone-aware observation date, not a future date')
        body=request.model_dump(mode='json'); body['observed_at']=observed.astimezone(timezone.utc).isoformat()
        with self.db.transaction() as conn:
            self.check(conn,principal,chat_id)
            row=conn.execute('SELECT data FROM workspace_observations WHERE observation_id=? AND chat_id=?',(str(observation_id),chat_id)).fetchone()
            if row:
                previous=json.loads(row['data'])
                if any(previous[k]!=v for k,v in body.items()):
                    raise APIError(409,'idempotency_conflict','Observation ID has different content')
                return previous
            row=conn.execute('SELECT data FROM workspace_items WHERE item_id=? AND chat_id=?',(str(request.item_id),chat_id)).fetchone()
            if not row or json.loads(row['data'])['deleted']:
                raise APIError(404,'not_found','Plan item not available')
            files=self.files(conn,principal,chat_id)
            evidence=[]
            for identity in request.file_ids:
                file=files.get(identity)
                if not file or file['deleted']:
                    raise APIError(422,'file_unavailable','Observation evidence is not available')
                evidence.append({'file_id':identity,'name':file['name'],'origin':file['origin']})
            if request.status=='confirmed' and evidence and all(f['origin']=='inspector' for f in evidence):
                raise APIError(422,'evidence_required','An AI response alone cannot confirm construction work. Add source evidence or record your own observation without an AI file.')
            result=dict(body,observation_id=str(observation_id),created_at=now(),withdrawn=False,evidence=evidence,source='operator_observation')
            conn.execute('INSERT INTO workspace_observations (observation_id,chat_id,data) VALUES (?,?,?)',(str(observation_id),chat_id,canonical_json(result)))
        return result

    def withdraw(self,principal,chat_id,observation_id):
        with self.db.transaction() as conn:
            self.check(conn,principal,chat_id)
            row=conn.execute('SELECT data FROM workspace_observations WHERE observation_id=? AND chat_id=?',(str(observation_id),chat_id)).fetchone()
            if not row:
                raise APIError(404,'not_found','Observation not found')
            value=json.loads(row['data']);value['withdrawn']=True
            conn.execute('UPDATE workspace_observations SET data=? WHERE observation_id=? AND chat_id=?',(canonical_json(value),str(observation_id),chat_id))
        return value

    def import_plan(self,principal,chat_id,request):
        # Parse the uploaded original, never an LLM's guess at a plan.
        file=self.download(principal,chat_id,request.file_id)
        if file['origin']!='upload' or not file['name'].lower().endswith(('.csv','.json')):
            raise APIError(422,'invalid_plan','Select an uploaded CSV or JSON plan')
        if file['byte_length']>256000:
            raise APIError(413,'plan_too_large','Plan imports are limited to 256 KB')
        path=self.service.bim_store.path_for(file['sha256'])
        try:
            text=path.read_text(encoding='utf-8-sig')
            rows=json.loads(text) if file['name'].lower().endswith('.json') else list(csv.DictReader(io.StringIO(text)))
            if not isinstance(rows,list) or not 1<=len(rows)<=200:
                raise ValueError()
            records=[WorkItem.model_validate(dict(row,revision=0)) for row in rows]
            if any(record.deleted for record in records):
                raise ValueError()
        except (ValueError,TypeError,UnicodeError):
            raise APIError(422,'invalid_plan','Plan must contain 1–200 rows with title, optional location and reference') from None
        scope='workspace-plan:'+chat_id; body=request.model_dump(mode='json')
        with self.db.transaction() as conn:
            self.check(conn,principal,chat_id)
            replay=self.db.replay(conn,scope,str(request.request_id),body)
            if replay:
                return replay
            count=conn.execute('SELECT COUNT(*) AS n FROM workspace_items WHERE chat_id=?',(chat_id,)).fetchone()['n']
            if count+len(records)>200:
                raise APIError(422,'plan_limit','At most 200 planned items per conversation')
            for i,record in enumerate(records):
                item_id=str(uuid5(request.request_id,str(i)))
                item=dict(record.model_dump(),revision=1,item_id=item_id,created_at=now(),plan_file_id=file['file_id'])
                conn.execute('INSERT INTO workspace_items (item_id,chat_id,data) VALUES (?,?,?)',(item_id,chat_id,canonical_json(item)))
            result={'imported':len(records)}
            self.db.save_receipt(conn,scope,str(request.request_id),body,result)
        return result
