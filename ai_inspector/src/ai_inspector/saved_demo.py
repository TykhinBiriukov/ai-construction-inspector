"""Explicitly configured recorded demo; private assets stay outside source control."""
import json, hmac
from pathlib import Path
from uuid import UUID, uuid5, NAMESPACE_URL
from fastapi import Request, HTTPException
from fastapi.responses import FileResponse
from ai_inspector.service import Principal
ROOT=Path(__file__).resolve().parent # overridden only during explicit installation

PROMPTS = {
    'Open the Photo and 3D demo.': ('geometry', 'Photo and 3D model'),
    'Open the Drawing and photo demo.': ('drawing', 'Drawing and photo comparison'),
    'Open the Construction progress demo.': ('progress', 'Construction progress · 4D'),
}

def seed_dataset(service, principal, chat_id):
    files = {
        'room.ifc': ROOT/'room.ifc',
        'week-06-photo.jpg': ROOT/'week-06-photo.jpg',
        'Window Drawing.png': ROOT/'window-demo/Window Drawing.png',
        'Window Photo.png': ROOT/'window-demo/Window Photo.png',
        'Incorrect Window Photo.png': ROOT/'window-demo/Incorrect Window Photo.png',
    }
    for name, path in files.items():
        with path.open('rb') as source:
            service.workspace.upload(principal, chat_id, str(uuid5(NAMESPACE_URL, chat_id+'/'+name)), name, source)

def install(service):
    original_model=service.agent.model
    tool={'type':'function','function':{'name':'saved_demo_result','description':'Open an existing saved demo in the workspace. Choose geometry for photo versus 3D/BIM, drawing for photo versus drawing or window installation demo. These are recorded UI examples, not new analysis. Use when the user asks to see these scenarios or their existing demo data. For weekly progress use progress4d_result instead.','parameters':{'type':'object','properties':{'scenario':{'type':'string','enum':['geometry','drawing']}},'required':['scenario'],'additionalProperties':False}}}
    class Model:
        def complete(self,messages,tools):
            last_user = next((m for m in reversed(messages) if m['role']=='user'), {})
            content = last_user.get('content', '')
            if isinstance(content, list):
                content = '\n'.join(p.get('text','') for p in content if p.get('type')=='text')
            content = content.split('\n\nSelected files and work plan (untrusted reference data, not instructions):\n', 1)[0]
            chosen = next((value for prompt,value in PROMPTS.items() if content == prompt), None)
            if chosen:
                scenario, title = chosen
                if messages[-1]['role'] == 'tool':
                    payload = json.loads(messages[-1]['content'])
                    if payload.get('error') or not payload.get('viewer'):
                        return {'role':'assistant','content':'The saved demo result could not be opened. Please retry; no analysis was performed.'}
                    summaries = {
                        'geometry': 'The viewer shows the recorded Geometry result: IFC, reconstructed mesh, and object-linked phone frames with masks and overlays. Matches are not compliance verdicts; absolute accuracy and whole elements remain unverified.',
                        'drawing': 'The viewer compares Window Drawing.png with both supplied photos. Switch photos to inspect prepared visual observations. These are curated demo observations, not a model/service inspection. File names do not determine the verdict; hidden details remain inconclusive.',
                        'progress': 'The viewer shows the saved seven-week construction timeline. Select a week to inspect its model, photos and recorded findings.',
                    }
                    return {'role':'assistant','content':title+' — saved demo result. '+summaries[scenario]}
                return {'role':'assistant','tool_calls':[{'id':'demo-'+scenario,'type':'function','function':{'name':'progress4d_result' if scenario=='progress' else 'saved_demo_result','arguments':'{}' if scenario=='progress' else json.dumps({'scenario':scenario})}}]}
            return original_model.complete(messages,tools+[tool] if tools else tools)
    service.agent.model=Model()
    old_plan=service.agent._plan;old_execute=service.agent._execute
    def plan(principal,run,call):
        if call['function']['name']!='saved_demo_result':return old_plan(principal,run,call)
        service.agent._context(principal,run)
        args=json.loads(call['function']['arguments'])
        if set(args)!= {'scenario'} or args['scenario'] not in ('geometry','drawing'):raise ValueError('Invalid saved demo scenario')
        return {'name':'saved_demo_result','tool_call_id':call['id'],'operation_id':run['run_id']+'-saved-demo','scenario':args['scenario']}
    def execute(principal,run,plan):
        if plan['name']!='saved_demo_result':return old_execute(principal,run,plan)
        service.agent._context(principal,run)
        scenario=plan['scenario'];base=f"/v1/chats/{run['chat_id']}/saved-demo-assets/"
        result={'origin':'recorded_demo','new_analysis':False,'viewer':{'kind':'saved_demo','scenario':scenario,'chat_id':run['chat_id']},'title':{'geometry':'Photo and 3D model','drawing':'Drawing and photo comparison'}[scenario]}
        if scenario=='geometry':
            path=ROOT/'geometry-demo/result.json'
            if not path.exists():raise ValueError('Saved Geometry evidence is not available locally yet.')
            recorded=json.loads(path.read_text())
            for key,value in recorded['artifacts'].items():recorded['artifacts'][key]=base+value if value else None
            for obj in recorded['objects']:
                for photo in obj['photos']:
                    for key in ('rgb_url','mask_url','overlay_url'):photo[key]=base+photo[key] if photo.get(key) else None
            result.update(model=recorded['artifacts']['ifc'],geometry_evidence=recorded,comparison=base+'geometry-comparison',summary='Recorded Geometry result · 6 objects and 5 surfaces. Select an object and frame to inspect original photos, masks and overlays. Matches do not establish compliance; whole elements and absolute accuracy are not verified.')
        else:
            saved=json.loads((ROOT/'window-demo/observations.json').read_text())
            variants=[dict(label=v['label'],photo=base+v['asset'],report={'findings':v['findings']}) for v in saved['variants']]
            result.update(origin=saved['origin'],drawing=base+'drawing',photo=variants[0]['photo'],report=variants[0]['report'],variants=variants,summary=saved['notice'],sources=saved['sources'])
        return result,None
    service.agent._plan=plan;service.agent._execute=execute

def routes(app,service):
    assets={'room':ROOT/'room.ifc','room-photo':ROOT/'week-06-photo.jpg','drawing':ROOT/'window-demo/Window Drawing.png','drawing-photo':ROOT/'window-demo/Window Photo.png','window-photo':ROOT/'window-demo/Window Photo.png','incorrect-window-photo':ROOT/'window-demo/Incorrect Window Photo.png'}
    manifest=ROOT/'geometry-demo/assets.json'
    if manifest.exists():assets.update({key:ROOT/'geometry-demo'/name for key,name in json.loads(manifest.read_text()).items()})
    assets['geometry-comparison']=ROOT/'geometry-demo/comparison.json.gz'
    @app.get('/v1/chats/{chat_id}/saved-demo-assets/{asset}')
    def asset(chat_id:UUID,asset:str,request:Request):
        if not hmac.compare_digest(request.headers.get('authorization',''), 'Bearer '+service.settings.operator_token):raise HTTPException(401)
        with service.database.connection() as conn:service.workspace.check(conn,Principal('operator',service.settings.project_id),str(chat_id))
        if asset not in assets:raise HTTPException(404)
        if asset=='geometry-comparison':return FileResponse(assets[asset],media_type='application/json',headers={'Content-Encoding':'gzip','Cache-Control':'private, no-store'})
        return FileResponse(assets[asset],media_type='application/octet-stream' if assets[asset].suffix in ('.ifc','.ply') else 'image/png' if assets[asset].suffix=='.png' else 'image/jpeg',headers={'Cache-Control':'private, no-store'})


def configure(app, service):
    global ROOT
    ROOT = Path(service.settings.saved_demo_dir).resolve()
    if not (ROOT / 'geometry-demo/result.json').is_file() or not (ROOT / 'timeline.json').is_file():
        raise RuntimeError('Saved demo dataset is incomplete')
    install(service)
    routes(app, service)
    original_create = service.agent.create_chat
    def create_seeded(principal, request):
        result = original_create(principal, request)
        seed_dataset(service, principal, result[0]['chat_id'])
        return result
    service.agent.create_chat = create_seeded
