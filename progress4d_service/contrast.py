"""Resolve ambiguous coating evidence by comparing actual before/after photographs."""
import base64,json,os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import httpx

def refine(previous,current,before_paths,after_paths,output,key=None):
    key=key or os.environ.get('PROGRESS4D_META_API_KEY','')
    if not key:return current
    prior={p['id']:p for p in previous.get('perception',{}).get('products',[])}
    candidates=[]
    for p in current.get('perception',{}).get('products',[]):
        old=prior.get(p['id'])
        if p['state']!='uncertain' or not old or old['state']!='absent':continue
        if 'coating' not in p.get('specification','').lower():continue
        if len(p['views'])!=2 or len(old['views'])!=2:continue
        if p['label'] in before_paths and p['label'] in after_paths:candidates.append(p)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    def check(p):
        content=[dict(type='text',text=f'''Compare construction progress for target {p['label']}: {p['specification']}.
The BEFORE card and NOW card each contain two photographs A and B of the same projected location. Red lines are project annotations, not physical edges. Prior inspection found this coating absent. Inspect the photos yourself: that prior result does not imply a change has occurred.
Decide whether NOW shows an applied finish coat compared with the previously exposed board. Require a coherent visible change in surface texture/finish across BOTH current views, not just exposure/lighting or a plain white panel. A clearly altered finished surface relative to visible raw paper/fasteners before can confirm coating. No change or ambiguous lighting must remain uncertain. Do not infer hidden thickness or material grade. Return JSON with state (present/absent/uncertain), views (A,B), reason (specific comparative evidence).''')]
        for label,path in [('BEFORE',before_paths[p['label']]),('NOW',after_paths[p['label']])]:
            content.extend([dict(type='text',text=label),dict(type='image_url',image_url={'url':'data:image/jpeg;base64,'+base64.b64encode(Path(path).read_bytes()).decode()})])
        try:
            with httpx.Client(timeout=90,follow_redirects=False) as client:
                r=client.post('https://api.meta.ai/v1/chat/completions',headers={'Authorization':'Bearer '+key},json=dict(model='muse-spark-1.3',messages=[dict(role='user',content=content)],response_format={'type':'json_object'},reasoning_effort='low',max_completion_tokens=3000))
                r.raise_for_status();choice,=r.json()['choices'];assert choice['finish_reason']=='stop';answer=json.loads(choice['message']['content'])
            assert answer['state'] in ('present','absent','uncertain') and isinstance(answer['reason'],str) and len(answer['reason'])<=1500
            (output/(p['label']+'.json')).write_text(json.dumps(answer,indent=2))
            if answer['state']=='present' and set(answer.get('views',[]))=={'A','B'}:
                p.update(state='present',reason=answer['reason'],temporal_evidence=True,comparison_basis='two_view_before_after_photographs')
        except (httpx.HTTPError,ValueError,KeyError,AssertionError,TypeError):
            (output/(p['label']+'-error.json')).write_text(json.dumps({'status':'comparison_unavailable'}))
    with ThreadPoolExecutor(max_workers=2) as executor:list(executor.map(check,candidates))
    return current
