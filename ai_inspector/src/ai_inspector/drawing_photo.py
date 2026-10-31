"""Bounded minion adapter. Inputs are selected chat files; endpoint/credentials are server-owned."""
import base64
import hashlib
import json
from pathlib import PurePosixPath
from uuid import UUID
import httpx
from pydantic import Field, field_validator
from .schemas import APIModel
from .errors import APIError


class CompareArguments(APIModel):
    checklist_task_id: UUID | None = Field(default=None, description='Optional existing comparison_tasks task whose checklist to reuse. Normally omit: server reuses the latest compatible saved checklist in this chat for the same drawing/page.')
    new_checklist: bool = Field(default=False, description='True only when the user explicitly requests revised/new requirements or a different inspection scope. A replacement photo alone must keep false.')
    drawing_file_id: str = Field(min_length=1, max_length=150)
    photo_file_id: str = Field(min_length=1, max_length=150)
    drawing_page: int | None = Field(default=None, ge=1, le=200,
        description='PDF drawings only: the user-confirmed 1-based page number, e.g. 2. For PNG/JPEG omit this field. Never use the string "null". Ask for the page when a PDF page is unspecified.')

    @field_validator('drawing_page', mode='before')
    @classmethod
    def normalize_absent_page(cls, value):
        # A known model serialization mistake; PDF page requirements still apply in planning.
        return None if value == 'null' else value

    task: str | None = Field(default=None, min_length=1, max_length=4000,
        description='Self-contained user comparison request, including relevant earlier instructions and confirmed file roles. Always supply this, even after a short confirmation. Do not invent criteria.')


class ResultArguments(APIModel):
    task_id: UUID | None = Field(default=None, description='ID from comparison_tasks. May be omitted only when this chat has exactly one comparison.')


DESCRIPTIONS = {
    'drawing_photo_compare': 'Compare one explicitly selected drawing and one selected photo using the dedicated minion. Pass file IDs from selected_files and a self-contained task from the conversation, not just the latest confirmation. For PNG/JPEG omit drawing_page: {"drawing_file_id":"<selected drawing ID>","photo_file_id":"<selected photo ID>","task":"<full user task>"}. For PDF add "drawing_page":2 only when the user selected page 2; ask if the page is missing. Do not send "drawing_page":"null". No IFC/BIM/3D. The runtime waits for a saved report; running is not a verdict. Same-drawing replacement photos reuse the saved checklist automatically; do not set new_checklist for a new photo. Use new_checklist=true only for an explicitly requested new scope or checklist revision.',
    'drawing_photo_result': 'Read a previously submitted drawing/photo task from comparison_tasks. No selected files are needed. Omit task_id only if there is exactly one task. The runtime handles bounded automatic waiting; do not submit a duplicate comparison.',
}
SCHEMAS = {'drawing_photo_compare': CompareArguments, 'drawing_photo_result': ResultArguments}
TOOLS = [{'type':'function','function':{'name':name,'description':DESCRIPTIONS[name],
          'parameters':schema.model_json_schema()}} for name,schema in SCHEMAS.items()]


def task_context(runs, limit=5):
    """Latest observed task states from persisted tool results, never assistant prose.

    Caller supplies this chat's bounded runs newest first. Keep multiple identities
    so omitted-ID status requests fail closed when the target is ambiguous.
    """
    tasks = {}
    for run in runs:
        for tool in reversed(run.get('tool_results', [])):
            if tool.get('name') not in SCHEMAS:
                continue
            result = tool.get('result', {})
            try:
                task_id = str(UUID(result.get('task_id', '')))
            except (ValueError, TypeError, AttributeError):
                continue
            if task_id not in tasks:
                tasks[task_id] = {'task_id': task_id, 'last_observed_status': result.get('status'),
                                  'observed_at': run.get('updated_at')}
            if result.get('checklist') and not tasks[task_id].get('checklist_id'):
                tasks[task_id]['checklist_id'] = result['checklist'].get('checklist_id')
                tasks[task_id]['checklist_scope'] = result['checklist'].get('task')
            if result.get('source_bindings'):
                tasks[task_id]['source_bindings'] = result['source_bindings']
    return list(tasks.values())[:limit]


def plan_compare(service, principal, run, args, operation_id):
    # Optional only for compatibility with already frozen pre-task tool calls.
    question = args.task if args.task is not None else run['text']
    if not question.strip():
        raise APIError(422,'comparison_task_required','Specify the comparison task')
    if len(question) > 4000:
        raise APIError(422,'comparison_task_too_long','Please shorten the comparison task to 4000 characters')
    if args.drawing_file_id == args.photo_file_id:
        raise APIError(422,'comparison_inputs_invalid','Select separate drawing and photo files')
    assets=[]
    for kind,file_id in [('drawing',args.drawing_file_id),('photo',args.photo_file_id)]:
        if file_id not in run.get('file_ids',[]):
            raise APIError(422,'comparison_file_not_selected','Only files selected for this message may be compared')
        try:
            f=service.workspace.download(principal,run['chat_id'],file_id)
        except APIError as error:
            if error.body['code'] != 'not_found':
                raise
            raise APIError(422, 'comparison_file_unavailable',
                           'A selected comparison file is no longer available. Ask the user to select an available file.') from None
        ext=PurePosixPath(f['name']).suffix.lower()
        mime={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.pdf':'application/pdf'}.get(ext)
        if not mime or (kind=='photo' and mime=='application/pdf') or not f.get('sha256'):
            raise APIError(422,'comparison_file_type','Use PNG/JPEG photos and PNG/JPEG/PDF drawings')
        page=args.drawing_page if kind=='drawing' and mime=='application/pdf' else None
        if mime=='application/pdf' and page is None:
            raise APIError(422,'comparison_page_required','Specify the drawing PDF page')
        if f['byte_length'] > 6_000_000:
            raise APIError(422,'comparison_file_too_large','Each minion input must be at most 6 MB')
        assets.append(dict(asset_id=kind,kind=kind,mime_type=mime,page=page,file_id=file_id,sha256=f['sha256']))
    if args.new_checklist and args.checklist_task_id:
        raise APIError(422, 'comparison_checklist_conflict', 'Choose saved checklist reuse or an explicit new checklist, not both')
    checklist_task_id = None
    if service.settings.drawing_photo_workflow == 'staged' and not args.new_checklist:
        with service.database.connection() as conn:
            rows = conn.execute('SELECT data FROM agent_runs WHERE chat_id=? ORDER BY sequence DESC LIMIT 200', (run['chat_id'],)).fetchall()
        tasks = task_context([json.loads(row['data']) for row in rows], limit=200)
        drawing = assets[0]
        for task in tasks:
            if args.checklist_task_id and task['task_id'] != str(args.checklist_task_id):
                continue
            old = next((a for a in task.get('source_bindings', []) if a.get('asset_id') == 'drawing'), {})
            if task.get('checklist_id') and task.get('last_observed_status') != 'running' and old.get('sha256') == drawing['sha256'] and old.get('page') == drawing['page']:
                checklist_task_id = task['task_id']
                break
        if args.checklist_task_id and not checklist_task_id:
            raise APIError(422, 'comparison_checklist_unavailable', 'The saved checklist must belong to this chat and the same drawing/page')
    elif args.checklist_task_id:
        raise APIError(422, 'comparison_checklist_unavailable', 'Saved checklists require staged analysis')
    return {'task_id':operation_id,'question':question,'assets':assets,
            'workflow':service.settings.drawing_photo_workflow,'checklist_task_id':checklist_task_id}


def bound_task(service, principal, chat_id, task_id):
    with service.database.connection() as conn:
        service.workspace.check(conn,principal,chat_id)
        rows=conn.execute('SELECT data FROM agent_runs WHERE chat_id=? ORDER BY sequence DESC LIMIT 200',(chat_id,)).fetchall()
    for row in rows:
        prior=json.loads(row['data'])
        pending=prior.get('pending_tool') or {}
        if pending.get('name')=='drawing_photo_compare' and pending.get('comparison',{}).get('task_id')==task_id:
            return
        if any(t.get('name')=='drawing_photo_compare' and t.get('operation_id')==task_id for t in prior.get('tool_results',[])):
            return
    raise APIError(404,'comparison_task_not_found','Comparison task is not bound to this chat')


class MinionClient:
    def __init__(self, settings, *, transport=None):
        self.settings,self.transport=settings,transport

    def evidence(self, task_id, finding_index, kind, ref_index):
        base, token = self.settings.drawing_photo_base_url, self.settings.drawing_photo_service_token
        if not base or not token:
            raise APIError(503, 'comparison_not_configured', 'Comparison evidence is unavailable')
        path = f'/v1/tasks/{UUID(task_id)}/evidence/{finding_index}/{kind}/{ref_index}'
        try:
            with httpx.Client(timeout=30, follow_redirects=False, transport=self.transport) as client:
                with client.stream('GET', base.rstrip('/') + path, headers={'Authorization': 'Bearer ' + token}) as response:
                    if response.status_code == 404:
                        raise APIError(404, 'evidence_not_found', 'This evidence image is no longer available')
                    if response.status_code != 200:
                        raise APIError(503, 'evidence_unavailable', 'Could not load the evidence image. Please try again.', retryable=True)
                    if response.headers.get('content-type', '').split(';')[0] != 'image/jpeg':
                        raise ValueError
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 6_000_000:
                            raise ValueError
                    if not raw.startswith(b'\xff\xd8\xff'):
                        raise ValueError
                    return bytes(raw)
        except httpx.HTTPError:
            raise APIError(503, 'evidence_unavailable', 'Could not load the evidence image. Please try again.', retryable=True) from None
        except ValueError:
            raise APIError(502, 'invalid_evidence', 'The evidence image could not be displayed') from None

    def request(self, method, task_id, body=None):
        base=self.settings.drawing_photo_base_url
        token=self.settings.drawing_photo_service_token
        if not base or not token:
            raise APIError(503,'comparison_not_configured','Drawing/photo service is not configured',retryable=True)
        path='/v1/tasks' if method=='POST' else '/v1/tasks/'+str(UUID(task_id))
        try:
            with httpx.Client(timeout=20,follow_redirects=False,transport=self.transport) as client:
                with client.stream(method,base.rstrip('/')+path,headers={'Authorization':'Bearer '+token,'Prefer':'respond-async'},json=body) as response:
                    if response.status_code not in (200,202):
                        if method == 'GET' and response.status_code == 404:
                            raise APIError(404, 'comparison_task_not_found', 'The comparison task has not been accepted by the service')
                        retry=response.status_code in (408,429,503) or response.status_code>=500
                        code='comparison_unavailable'
                        if response.status_code==409:
                            # A bounded JSON detail distinguishes busy from immutable-ID conflicts.
                            detail=bytearray()
                            for chunk in response.iter_bytes():
                                detail.extend(chunk[:4096-len(detail)])
                                if len(detail)>=4096:break
                            retry=b'service_busy' in detail
                        raise APIError(503 if retry else 502,code,'Drawing/photo request was not accepted',retryable=retry)
                    raw=bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw)>1_000_000:raise ValueError
            value=json.loads(raw)
            if not isinstance(value,dict) or value.get('task_id')!=task_id or value.get('status') not in ('running','completed','needs_input','failed'):
                raise ValueError
            if value['status'] in ('completed','needs_input') and not isinstance(value.get('report'),dict):raise ValueError
            result={k:value.get(k) for k in ('task_id','status','report','assets','provenance','audit','phase','checklist')}
            if value['status']=='failed':
                result['error']={'code':'comparison_failed','message':'Minion analysis failed; no inspection verdict is available','retryable':False}
                if value.get('error') in ('invalid_report_schema', 'invalid_stage_output'):
                    result['error']={'code':'comparison_invalid_report',
                        'message':'The analysis service could not validate its generated report or evidence references. No verdict is available. This technical failure does not establish a problem with image quality.',
                        'retryable':False}
            return result
        except httpx.HTTPError:
            raise APIError(503,'comparison_unavailable','Drawing/photo service did not respond; retry the same message',retryable=True) from None
        except (ValueError,TypeError,KeyError,RecursionError):
            raise APIError(502,'comparison_invalid_response','Drawing/photo service returned an invalid response') from None


def execute(service, principal, run, plan, client):
    if plan['name']=='drawing_photo_result':
        bound_task(service,principal,run['chat_id'],plan['task_id'])
        return client.request('GET',plan['task_id'])
    comparison=plan['comparison']; assets=[]
    # A timeout does not mean admission failed. Reconcile the immutable task ID
    # before resending bytes, including old saved requests awaiting manual Resume.
    recovery = run.get('comparison_retry', {})
    previously_waited = any(e.get('operation_id') == plan['operation_id'] and e.get('status') == 'waiting'
                            for e in run.get('activity_events', []))
    if recovery.get('attempts', 0) > 1 or previously_waited:
        try:
            result = client.request('GET', comparison['task_id'])
            result['source_bindings'] = [{k:p[k] for k in ('asset_id','file_id','sha256','page')} for p in comparison['assets']]
            return result
        except APIError as error:
            if error.body['code'] != 'comparison_task_not_found':
                raise
    for pinned in comparison['assets']:
        f=service.workspace.download(principal,run['chat_id'],pinned['file_id'])
        if f.get('sha256')!=pinned['sha256']:
            raise APIError(409,'comparison_source_changed','Selected input changed; start a new comparison')
        path=service.bim_store.path_for(f['sha256'])
        with path.open('rb') as handle: raw=handle.read(6_000_001)
        if len(raw)>6_000_000 or hashlib.sha256(raw).hexdigest()!=pinned['sha256']:
            raise APIError(409,'comparison_source_changed','Input bytes do not match the pinned source')
        assets.append({k:pinned[k] for k in ('asset_id','kind','mime_type','page')}|{'data_base64':base64.b64encode(raw).decode()})
    body={'request_id':comparison['task_id'],'question':comparison['question'],
          'context':'Inspector-selected chat files. No measured geometry or independent alignment supplied.','assets':assets}
    if comparison.get('workflow', 'legacy') == 'staged':
        body['workflow'] = 'staged'
        if comparison.get('checklist_task_id'):
            body['checklist_task_id'] = comparison['checklist_task_id']
    result=client.request('POST',comparison['task_id'],body)
    result['source_bindings']=[{k:p[k] for k in ('asset_id','file_id','sha256','page')} for p in comparison['assets']]
    return result
