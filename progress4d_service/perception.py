"""Evidence-scoped visual product states from projected IFC locations.

The model classifies presence only. Product counts come from explicit IFC IDs,
not invented lengths/volumes. Every positive/negative state needs both views.
"""
import base64
import io
import json
import os
from pathlib import Path
from typing import Literal
import numpy as np
import ifcopenshell
import ifcopenshell.geom
import pycolmap
import cv2
from PIL import Image,ImageDraw
import httpx
from pydantic import BaseModel,ConfigDict,Field


class Finding(BaseModel):
    model_config=ConfigDict(extra='forbid')
    label:str
    state:Literal['present','absent','occluded','uncertain']
    views:list[Literal['A','B']]
    reason:str=Field(max_length=400)


class Findings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    items:list[Finding]


def products(ifc):
    f=ifcopenshell.open(str(ifc));settings=ifcopenshell.geom.settings();settings.set(settings.USE_WORLD_COORDS,True)
    result=[];seen=set()
    for task in f.by_type('IfcTask'):
        for rel in task.OperatesOn:
            for obj in rel.RelatedObjects:
                if not obj.is_a('IfcProduct') or not obj.Representation:continue
                if obj.GlobalId in seen:raise ValueError('Product assigned to multiple tasks is unsupported')
                seen.add(obj.GlobalId)
                shape=ifcopenshell.geom.create_shape(settings,obj);v=np.array(shape.geometry.verts).reshape(-1,3)
                low,high=v.min(0),v.max(0)
                corners=np.array([[x,y,z] for x in [low[0],high[0]] for y in [low[1],high[1]] for z in [low[2],high[2]]])
                result.append(dict(id=obj.GlobalId,name=obj.Name or obj.is_a(),specification=obj.Description or task.Description or '',task_id=task.GlobalId,corners=corners))
    return result


def make_cards(ifc,model_path,images,registration,output,high_resolution=False):
    model=pycolmap.Reconstruction(str(model_path));output=Path(output);output.mkdir(parents=True,exist_ok=True)
    tr=registration['transform'];rotation=np.array(tr['rotation']);translation=np.array(tr['translation']);scale=tr['scale']
    cards=[];inventory=[];image_tracks={}
    for im in model.images.values():
        samples=[p for p in im.points2D if p.has_point3D() and model.points3D[p.point3D_id].error<1.5 and model.points3D[p.point3D_id].track.length()>=4]
        image_tracks[im.name]=(np.array([p.xy for p in samples]),np.array([scale*rotation@model.points3D[p.point3D_id].xyz+translation for p in samples]))
    for index,product in enumerate(products(ifc)):
        label=f'P{index:03d}';world=(product['corners']-translation)@rotation/scale
        options=[]
        for im in model.images.values():
            cam=model.cameras[im.camera_id];pose=im.cam_from_world().matrix();xyz=world@pose[:,:3].T+pose[:,3]
            if np.min(xyz[:,2])<=.05:continue
            uv=cam.img_from_cam(xyz)
            if not np.isfinite(uv).all():continue
            low=uv.min(0);high=uv.max(0)
            # Require the entire intended location to lie within the frame.
            if np.any(low<3) or high[0]>cam.width-3 or high[1]>cam.height-3:continue
            size=high-low;area=float(np.prod(size))
            if area<120 or max(size)<20:continue
            score=area
            if high_resolution:
                extent=np.ptp(product['corners'],axis=0);axis=int(np.argmin(extent))
                center=product['corners'].mean(0);origin=scale*rotation@im.projection_center()+translation
                normal_view=abs((origin-center)[axis])/max(np.linalg.norm(origin-center),1e-6)
                score*=max(.05,normal_view)**2
            options.append((score,im.name,low,high,im.projection_center(),uv))
        options.sort(key=lambda x:-x[0]);chosen=[]
        for option in options:
            if chosen and np.linalg.norm(option[4]-chosen[0][4])*scale<.25:continue
            chosen.append(option)
            if len(chosen)==2:break
        metadata={k:v for k,v in product.items() if k!='corners'}
        metadata.update(label=label,views=[],state='uncertain',reason='Fewer than two usable projected views',
                        design_bounds_m=[product['corners'].min(0).tolist(),product['corners'].max(0).tolist()])
        if len(chosen)==2:
            cell_width,cell_height=(640,940) if high_resolution else (320,320)
            card=Image.new('RGB',(cell_width*2,cell_height+40),'white');draw=ImageDraw.Draw(card)
            draw.text((5,3),f'{label}: {product["name"]}',fill='black')
            for n,(_,name,low,high,camera_center,uv) in enumerate(chosen):
                with Image.open(Path(images)/name) as image:
                    pixels=np.asarray(image.convert('RGB'))
                    mask=np.zeros(pixels.shape[:2],np.uint8)
                    cv2.fillConvexPoly(mask,cv2.convexHull(np.rint(uv).astype(np.int32)),1)
                    mask=cv2.erode(mask,np.ones((7,7),np.uint8))
                    target_mask=mask
                    appearance=np.median(pixels[mask>0],axis=0).tolist() if np.any(mask) else None
                    pad=max(20,float(np.max(high-low))*.2)
                    rect=[max(0,int(low[0]-pad)),max(0,int(low[1]-pad)),min(image.width,int(high[0]+pad)),min(image.height,int(high[1]+pad))]
                    crop=image.convert('RGB').crop(rect);d=ImageDraw.Draw(crop)
                    # Keep the marker outside thin pipes/studs instead of painting over them.
                    d.rectangle((low[0]-rect[0]-5,low[1]-rect[1]-5,high[0]-rect[0]+5,high[1]-rect[1]+5),outline='red',width=2)
                    horizontal=high_resolution and int(np.argmin(np.ptp(product['corners'],axis=0)))==2
                    crop.thumbnail((cell_width-5,430 if horizontal else cell_height));card.paste(crop,(n*cell_width,30))
                    if horizontal:
                        context=image.convert('RGB');overview=ImageDraw.Draw(context)
                        overview.rectangle((low[0]-5,low[1]-5,high[0]+5,high[1]+5),outline='red',width=3)
                        context.thumbnail((cell_width-5,470));card.paste(context,(n*cell_width,500))
                        draw.text((n*cell_width+4,480),'Same photograph: wider context',fill='black')
                view='AB'[n];draw.text((n*cell_width+4,18),view,fill='black')
                camera_ifc=scale*rotation@camera_center+translation
                xy,track_world=image_tracks[name]
                # Bounding rectangles include wall/jamb pixels outside oblique
                # targets. Count occluders only inside the projected silhouette.
                mask=np.zeros(len(xy),dtype=bool)
                if len(xy):
                    pixels_xy=np.rint(xy).astype(int)
                    valid=np.all(pixels_xy>=0,axis=1)&(pixels_xy[:,0]<target_mask.shape[1])&(pixels_xy[:,1]<target_mask.shape[0])
                    mask[valid]=target_mask[pixels_xy[valid,1],pixels_xy[valid,0]]>0
                foreground=0;intersections=0;near_surface=0
                for observed in track_world[mask]:
                    ray=observed-camera_ifc
                    hit=ray_box(camera_ifc,ray,*metadata['design_bounds_m'])
                    if hit is not None:
                        intersections+=1;foreground+=int((hit-1)*np.linalg.norm(ray)>.06)
                        near_surface+=int(abs((hit-1)*np.linalg.norm(ray))<.03)
                metadata['views'].append(dict(view=view,photo=name,bbox_px=[*low.tolist(),*high.tolist()],camera_ifc_m=camera_ifc.tolist(),
                                               foreground_tracks=foreground,intersecting_tracks=intersections,near_surface_tracks=near_surface,appearance_rgb=appearance))
            card.save(output/f'{label}.jpg',quality=90);cards.append((label,card));metadata['reason']='Pending visual analysis'
        inventory.append(metadata)
    (output/'inventory.json').write_text(json.dumps(inventory,indent=2))
    return inventory,cards


def ray_box(origin,direction,low,high):
    origin=np.asarray(origin);direction=np.asarray(direction);low=np.asarray(low);high=np.asarray(high)
    parallel=np.abs(direction)<1e-10
    if np.any(parallel&((origin<low)|(origin>high))):return None
    safe=np.where(parallel,1,direction)
    a=(low-origin)/safe;b=(high-origin)/safe
    near=np.where(parallel,-np.inf,np.minimum(a,b));far=np.where(parallel,np.inf,np.maximum(a,b))
    first=float(np.max(near));last=float(np.min(far))
    return max(first,0) if last>=max(first,0) else None


def geometric_visibility_gate(products):
    # Conservative contradiction check: foreground reconstructed tracks or a
    # directly observed covering product invalidate claims about hidden layers.
    for product in products:
        if product['state'] not in ('present','absent'):continue
        if product['state']=='absent' and product.get('design_bounds_m'):
            extent=np.diff(np.array(product['design_bounds_m']),axis=0)[0]
            if product.get('evidence_version')!='multiview_v2' and ((extent[2]<.05 and min(extent[:2])>.1) or (sorted(extent)[1]<.1 and any(v.get('intersecting_tracks',0)<3 for v in product['views']))):
                product['raw_visual_state']='absent';product['state']='uncertain'
                product['reason']='Absence of a small product or thin floor layer is not corroborated by sufficient reconstructed background evidence.'
                continue
        if any(v.get('intersecting_tracks',0)>=5 and v.get('foreground_tracks',0)/v['intersecting_tracks']>=.6 for v in product['views']):
            product['raw_visual_state']=product['state'];product['state']='occluded'
            product['reason']='Reconstructed foreground blocks the projected product location.'
    blockers=[p for p in products if p['state']=='present' and p.get('design_bounds_m')]
    for product in products:
        if product['state'] not in ('present','absent') or not product.get('design_bounds_m'):continue
        blocked=0
        low,high=np.asarray(product['design_bounds_m']);center=(low+high)/2
        axis=int(np.argmax(high-low));samples=[]
        for fraction in [.25,.5,.75]:
            point=center.copy();point[axis]=low[axis]+fraction*(high[axis]-low[axis]);samples.append(point)
        for view in product['views']:
            n=view.get('intersecting_tracks',0)
            if n>=5 and view.get('foreground_tracks',0)/n>=.6:
                blocked+=1;continue
            if 'camera_ifc_m' not in view:continue
            origin=np.array(view['camera_ifc_m']);covered=0
            for point in samples:
                ray=point-origin;distance=np.linalg.norm(ray)
                for other in blockers:
                    if other['id']==product['id'] or other['task_id']==product['task_id']:continue
                    hit=ray_box(origin,ray,*other['design_bounds_m'])
                    if hit is not None and hit>0 and (1-hit)*distance>.001:
                        covered+=1;break
            if covered>=2:blocked+=1
        if blocked:
            product['raw_visual_state']=product['state'];product['state']='occluded'
            product['reason']='Foreground geometry or a directly observed covering product blocks the required two-view evidence.'
    return products


def appearance_gate(products):
    # A sub-centimetre coating cannot be distinguished from its substrate by
    # this depth accuracy. Preserve a visual candidate for temporal checking.
    for p in products:
        if p['state']!='present' or not p.get('design_bounds_m'):continue
        extent=np.diff(np.array(p['design_bounds_m']),axis=0)[0]
        if min(extent)<.01 and np.prod(np.sort(extent)[1:])>.1:
            p['candidate_visual_state']='present';p['state']='uncertain'
            p['requires_temporal_appearance']=True
            p['reason']='Thin coating requires a prior substrate observation and a corroborating appearance change; a single survey cannot establish it.'
    # Isolated wood-like patches are insufficient evidence of installed floor
    # covering. Require a contiguous group of at least six planned floor units.
    groups={}
    for p in products:
        if p['state']!='present' or not p.get('design_bounds_m'):continue
        lo,hi=np.asarray(p['design_bounds_m']);size=hi-lo
        if size[2]<.05 and min(size[:2])>5*size[2]:groups.setdefault(p['task_id'],[]).append(p)
    for members in groups.values():
        remaining=set(range(len(members)))
        while remaining:
            component={remaining.pop()};front=list(component)
            while front:
                a=front.pop();alo,ahi=np.asarray(members[a]['design_bounds_m'])
                for b in list(remaining):
                    blo,bhi=np.asarray(members[b]['design_bounds_m'])
                    gap=np.maximum(0,np.maximum(alo-bhi,blo-ahi))
                    if np.linalg.norm(gap)<.04:remaining.remove(b);component.add(b);front.append(b)
            if len(component)<6:
                for index in component:
                    p=members[index];p['candidate_visual_state']='present';p['state']='uncertain';p['reason']='An isolated patch does not establish a continuous installed floor covering.'
    return products


def classify(inventory,cards,output,key=None,parallel=True,retries=1):
    key=key or os.environ.get('PROGRESS4D_META_API_KEY','')
    if not key:return dict(mode='unavailable',products=inventory,limitations=['Visual model not configured; no product completion inferred.'])
    if parallel and len(cards)>12:
        from concurrent.futures import ThreadPoolExecutor
        def batch_run(offset):
            batch=cards[offset:offset+12];labels={x[0] for x in batch}
            directory=Path(output)/f'group-{offset:03d}';directory.mkdir(exist_ok=True)
            return classify([p for p in inventory if p['label'] in labels],batch,directory,key,parallel=False)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(batch_run,range(0,len(cards),12)))
        appearance_gate(inventory)
        geometric_visibility_gate(inventory)
        result=dict(mode='live_meta',products=inventory,limitations=['Visual classifications, not measured hidden geometry or certified completion.','IFC product counts are not measured volumes.'])
        Path(output,'states.json').write_text(json.dumps(result,indent=2));return result
    lookup={p['label']:p for p in inventory}
    for offset in range(0,len(cards),12):
        batch=cards[offset:offset+12];width,height=batch[0][1].size
        sheet=Image.new('RGB',(2*width,height*((len(batch)+1)//2)),'white')
        for i,(_,card) in enumerate(batch):sheet.paste(card,((i%2)*width,(i//2)*height))
        stream=io.BytesIO();sheet.save(stream,format='JPEG',quality=90)
        labels=[x[0] for x in batch]
        specifications='\n'.join(f"{label}: {lookup[label].get('specification','No detailed material specification supplied')}" for label in labels)
        prompt=('Inspect installed construction products at the RED projected IFC locations. Each labelled row has two independent photos A/B. '
            'Return exactly one item for each label: '+', '.join(labels)+'. present means the NAMED product itself is visibly installed at that location in BOTH views; '
            'absent means the location is visibly clear and the product is absent in BOTH views. '
            'If another layer covers it, return occluded, NEVER absent or present. Do not infer hidden framing or wiring from completed boards. '
            'Do not count stored/loose materials as installed. Differentiate gypsum board from painted finish; a red box is design projection, NOT evidence. '
            'Small/unrecognizable crops or conflicting views must be uncertain. views must list A and B only when both support the classification. '
            'Do not infer dates, quantities or progress from product names. Use the following DESIGN specifications only to identify the target, never as proof that it exists:\n'+specifications+
            '\nIf material cannot be distinguished, return uncertain. Return JSON {"items":[{"label":"P000","state":"uncertain","views":[],"reason":"..."}]}.')
        body=dict(model='muse-spark-1.3',messages=[{'role':'user','content':[{'type':'text','text':prompt},{'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(stream.getvalue()).decode()}}]}],
                  response_format={'type':'json_schema','json_schema':{'name':'ProductStates','schema':Findings.model_json_schema()}},max_completion_tokens=8000,reasoning_effort='low',stream=False)
        try:
            with httpx.Client(timeout=120,follow_redirects=False) as client:
                response=client.post('https://api.meta.ai/v1/chat/completions',headers={'Authorization':'Bearer '+key},json=body)
                if response.status_code!=200:raise ValueError(f'provider_http_{response.status_code}')
                if len(response.content)>1_000_000:raise ValueError('Oversized model output')
                payload=response.json();choice,=payload['choices']
                if choice.get('finish_reason')!='stop':raise ValueError('Incomplete model output')
                content=choice['message']['content'];parsed=Findings.model_validate_json(content)
                if len(parsed.items)!=len(labels) or {x.label for x in parsed.items}!=set(labels):raise ValueError('Mismatched model labels')
                for finding in parsed.items:
                    state=finding.state
                    if state in ('present','absent') and set(finding.views)!={'A','B'}:state='uncertain'
                    lookup[finding.label].update(state=state,reason=finding.reason)
                Path(output,f'batch-{offset:03d}.json').write_text(json.dumps({'labels':labels,'response':parsed.model_dump(),'usage':payload.get('usage')},indent=2))
        except (httpx.HTTPError,ValueError,KeyError,TypeError) as exc:
            for label in labels:lookup[label].update(state='uncertain',reason='Visual analysis failed or returned invalid output')
            diagnostic={'error_type':type(exc).__name__,'labels':labels}
            if str(exc).startswith('provider_http_'):diagnostic['provider_status']=str(exc)
            Path(output,f'error-{offset:03d}.json').write_text(json.dumps(diagnostic))
            if retries:
                retry_path=Path(output)/f'retry-{offset:03d}';retry_path.mkdir(exist_ok=True)
                classify([lookup[label] for label in labels],batch,retry_path,key,parallel=False,retries=0)
    if parallel:
        appearance_gate(inventory)
        geometric_visibility_gate(inventory)
    result=dict(mode='live_meta',products=inventory,limitations=['Visual classifications, not measured hidden geometry or certified completion.','IFC product counts are not measured volumes.'])
    Path(output,'states.json').write_text(json.dumps(result,indent=2));return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('ifc');p.add_argument('model');p.add_argument('images');p.add_argument('registration');p.add_argument('output');a=p.parse_args()
    registration=json.loads(Path(a.registration).read_text());inventory,cards=make_cards(a.ifc,a.model,a.images,registration,a.output)
    result=classify(inventory,cards,a.output);print(json.dumps({'mode':result['mode'],'products':len(result['products'])}))
