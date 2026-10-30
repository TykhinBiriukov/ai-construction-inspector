"""Bounded minion adapter. Inputs are selected chat files; endpoint/credentials are server-owned."""
import base64
import hashlib
import json
from pathlib import PurePosixPath
from uuid import UUID
import httpx
from pydantic import Field
from .schemas import APIModel
from .errors import APIError


class CompareArguments(APIModel):
    drawing_file_id: str = Field(min_length=1, max_length=150)
    photo_file_id: str = Field(min_length=1, max_length=150)
    drawing_page: int | None = Field(default=None, ge=1, le=200)
    task: str | None = Field(default=None, min_length=1, max_length=4000,
        description='Self-contained user comparison request, including relevant earlier instructions and confirmed file roles. Always supply this, even after a short confirmation. Do not invent criteria.')


class ResultArguments(APIModel):
    task_id: UUID


DESCRIPTIONS = {
    'drawing_photo_compare': 'Compare one explicitly selected drawing and one selected photo using the dedicated minion. Pass file IDs from selected_files and a self-contained task from the conversation, not just the latest confirmation. PDF drawings require the caller-specified page; ask if missing. No IFC/BIM/3D. Returns running or a saved report; running is not a verdict.',
    'drawing_photo_result': 'Read a previously submitted drawing/photo task belonging to this chat. Do not repeatedly poll in one turn; if still running, tell the user and check on their next request.',
}
SCHEMAS = {'drawing_photo_compare': CompareArguments, 'drawing_photo_result': ResultArguments}
TOOLS = [{'type':'function','function':{'name':name,'description':DESCRIPTIONS[name],
          'parameters':schema.model_json_schema()}} for name,schema in SCHEMAS.items()]


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
        f=service.workspace.download(principal,run['chat_id'],file_id)
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
    return {'task_id':operation_id,'question':question,'assets':assets}


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
            result={k:value.get(k) for k in ('task_id','status','report','assets','provenance','audit')}
            if value['status']=='failed':
                result['error']={'code':'comparison_failed','message':'Minion analysis failed; no inspection verdict is available','retryable':False}
                if value.get('error') == 'invalid_report_schema':
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
    result=client.request('POST',comparison['task_id'],body)
    result['source_bindings']=[{k:p[k] for k in ('asset_id','file_id','sha256','page')} for p in comparison['assets']]
    return result
