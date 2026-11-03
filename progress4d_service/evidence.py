"""High-resolution two-view installation evidence, separate from material compliance."""
import base64,io,json,os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Literal
import httpx
from pydantic import BaseModel,ConfigDict,Field
from .perception import geometric_visibility_gate

class Item(BaseModel):
    model_config=ConfigDict(extra='forbid')
    label:str
    state:Literal['present','absent','occluded','uncertain']
    views:list[Literal['A','B']]
    observed_surface:Literal['exposed_frame','exposed_services','raw_board','finished_coating','floor_covering','bare_substrate','door','other','uncertain']
    material_verified:bool
    reason:str=Field(max_length=600)
class Response(BaseModel):
    model_config=ConfigDict(extra='forbid')
    items:list[Item]

PROMPT='''Assess observed CONSTRUCTION INSTALLATION at the marked IFC targets. No schedule dates or expected completion states are provided.
Each separately attached card has two independent photographs A and B of one labelled product. Red outlines indicate the DESIGN location, never proof of presence. Return one item per requested label. Copy the label ID exactly (for example P075); never append a colon, product name or description to it.
- present: this product/physical layer is directly visible and installed in BOTH A and B.
- absent: the intended location is exposed and visibly empty/bare in BOTH; no occluder prevents inspection.
- occluded: another installed layer or foreground object hides the target. Do not infer direct visibility of framing or services from a completed wall. For board INSTALLATION, clearly visible sheet/panel geometry and physical boundaries can establish an installed board even when its face has a coating; this is not a measurement of hidden board material or thickness.
- uncertain: material/identity/visibility is genuinely undecidable or the two views conflict.
Separate INSTALLATION from exact MATERIAL COMPLIANCE. Exact timber species is irrelevant. However, a flat grey surface is NOT evidence of installed flooring: bare concrete/screed is itself flat and continuous. For flooring, require visible laid covering with physical edges/joints or a clearly distinct finish surface in both views. The red rectangle is NOT a plank edge. Never infer finished level from a photo or design outline. Use the wider context under each floor detail to distinguish bare construction substrate from installed covering. Offcuts, stacked boards, tools and trestles are not installed flooring.
For finishing work, inspect paper texture, fasteners, filled seams and coating detail. Raw gypsum may look pale and smooth under bright light. A white/smooth panel and absence of discernible screws at low resolution do NOT establish an applied paint coat: classify that finish uncertain. Require positive coating evidence, such as a visibly different applied finish or clearly finished surface detail, in both views. Visible designed panel joints alone do not disprove a finish coat. Board installation and applied finish are separate progress questions.
Small targets need actual visible evidence, not similarity to neighboring elements. Do not fill unknown items from the plan or assume neighbouring work is identical. State the specific photo evidence in reason. views must contain both A and B for present/absent.
'''

def classify(inventory,cards,output,key=None):
    key=key or os.environ.get('PROGRESS4D_META_API_KEY','');output=Path(output)
    if not key:return dict(mode='unavailable',products=inventory,limitations=['Visual model unavailable'])
    lookup={p['label']:p for p in inventory}
    for p in inventory:p['evidence_version']='multiview_v2'
    def batch_run(offset):
        batch=cards[offset:offset+4];labels=[x[0] for x in batch]
        content=[dict(type='text',text=PROMPT+'\nTargets:\n'+'\n'.join(f"{label}: {lookup[label]['name']}: {lookup[label].get('specification','')}" for label in labels))]
        for label,card in batch:
            stream=io.BytesIO();card.save(stream,format='JPEG',quality=95)
            content.extend([dict(type='text',text=label),dict(type='image_url',image_url={'url':'data:image/jpeg;base64,'+base64.b64encode(stream.getvalue()).decode()})])
        schema=Response.model_json_schema()
        schema['$defs']['Item']['properties']['label']['enum']=labels
        body=dict(model='muse-spark-1.3',messages=[dict(role='user',content=content)],response_format={'type':'json_schema','json_schema':{'name':'InstallationEvidence','schema':schema}},max_completion_tokens=8000,reasoning_effort='low',stream=False)
        for attempt in range(2):
            diagnostic={}
            try:
                with httpx.Client(timeout=120,follow_redirects=False) as client:
                    response=client.post('https://api.meta.ai/v1/chat/completions',headers={'Authorization':'Bearer '+key},json=body)
                    if response.status_code!=200:raise ValueError('provider_http_'+str(response.status_code))
                    if len(response.content)>1_000_000:raise ValueError('Oversized response')
                    payload=response.json();choice,=payload['choices']
                    diagnostic=dict(finish_reason=choice.get('finish_reason'),model_output=choice.get('message',{}).get('content'))
                    if choice.get('finish_reason')!='stop':raise ValueError('Incomplete model response')
                    parsed=Response.model_validate_json(choice['message']['content'])
                    if len(parsed.items)!=len(labels) or {p.label for p in parsed.items}!=set(labels):raise ValueError('Evidence labels differ')
                for item in parsed.items:
                    state=item.state
                    if state in ('present','absent') and set(item.views)!={'A','B'}:state='uncertain'
                    lookup[item.label].update(state=state,reason=item.reason,observed_surface=item.observed_surface,material_verified=item.material_verified)
                (output/f'v2-batch-{offset:03d}.json').write_text(json.dumps(dict(response=parsed.model_dump(),usage=payload.get('usage')),indent=2));return
            except (httpx.HTTPError,ValueError,KeyError,TypeError) as exc:
                (output/f'v2-error-{offset:03d}-{attempt}.json').write_text(json.dumps(dict(error_type=type(exc).__name__,labels=labels,**diagnostic)))
        for label in labels:lookup[label].update(state='uncertain',reason='Model response unavailable or invalid')
    with ThreadPoolExecutor(max_workers=2) as executor:list(executor.map(batch_run,range(0,len(cards),4)))
    geometric_visibility_gate(inventory)
    result=dict(mode='live_meta',evidence_version='multiview_v2',products=inventory,limitations=['Installation evidence does not certify exact material, thickness, hidden work or physical volume.'])
    (output/'states.json').write_text(json.dumps(result,indent=2));return result
