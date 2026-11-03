"""Render a clean completion survey from week six; poses remain evaluator-only."""
import argparse
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import bpy
from mathutils import Matrix
from PIL import Image
from render_surveys import box, cylinder, material


def build(source):
    bpy.ops.wm.open_mainfile(filepath=str(source/'evaluator/normal/week-06.blend'))
    for obj in list(bpy.context.scene.objects):
        if obj.name.startswith(('Loose timber offcut', 'Toolbox', 'Uninstalled boards stacked',
                                'Trestle leg', 'Work bench', 'Window mullion', 'Window rail', 'Glazing')):
            bpy.data.objects.remove(obj, do_unlink=True)
    for light in bpy.context.scene.objects:
        if light.type=='LIGHT':
            light.visible_camera=False; light.visible_glossy=False; light.visible_transmission=False
    L,W,H = 7.219,3.331,2.775
    paint=material('Week seven painted plaster',(.82,.79,.72),.78,180)
    paint.node_tree.nodes.get('Bump').inputs['Distance'].default_value=.0005
    frame=material('Installed white window frame',(.86,.87,.85),.28,150)
    frame.node_tree.nodes.get('Bump').inputs['Strength'].default_value=0
    gasket=material('Window rubber seals',(.035,.04,.04),.65)
    glass=material('Installed clear glazing',(.94,.97,1),.06)
    nodes=glass.node_tree.nodes
    bsdf=nodes.get('Principled BSDF')
    for socket in ['Base Color','Normal']:
        for link in list(bsdf.inputs[socket].links): glass.node_tree.links.remove(link)
    bsdf.inputs['Base Color'].default_value=(.94,.97,1,1)
    bsdf.inputs['Transmission Weight'].default_value=1
    bsdf.inputs['IOR'].default_value=1.45
    # The original window opening spans the short wall, z=.976..2.489 m.
    box('Finished opposite short wall',(0,0,.02),(.018,W,H-.02),paint)
    box('Finished window wall below',(L-.018,0,.02),(.018,W,.956),paint)
    box('Finished window wall above',(L-.018,0,2.489),(.018,W,H-2.489),paint)
    # Refresh the existing north wall finish without changing the door opening.
    north=bpy.data.objects.get('Wall-02-door-7.219m')
    if north: north.data.materials.clear(); north.data.materials.append(paint)
    for y in [0,W-.08]: box('Installed window outer jamb',(L-.01,y,.976),(.12,.08,1.513),frame)
    for z in [.976,2.409]: box('Installed window outer rail',(L-.01,.08,z),(.12,W-.16,.08),frame)
    for y in [W/3-.035,2*W/3-.035]:
        box('Installed window central mullion',(L-.02,y,1.056),(.13,.07,1.353),frame)
    for i in range(3):
        y0=.08 if i==0 else i*W/3+.035
        y1=W-.08 if i==2 else (i+1)*W/3-.035
        for y in [y0,y1-.012]: box('Glazing vertical seal',(L+.015,y,1.056),(.015,.012,1.353),gasket,0)
        for z in [1.056,2.397]: box('Glazing horizontal seal',(L+.015,y0,z),(.015,y1-y0,.012),gasket,0)
        box('Installed glass pane',(L+.055,y0+.012,1.068),(.008,y1-y0-.024,1.329),glass,.001)
    box('Finished window sill',(L-.13,0,.945),(.32,W,.031),frame)
    cylinder('Window handle stem',(L-.025,W/3-.06,1.68),(L-.075,W/3-.06,1.68),.012,frame)
    cylinder('Window handle grip',(L-.075,W/3-.06,1.68),(L-.075,W/3-.06,1.56),.012,frame)
    # Finish exposed perimeter strips left by the original rectangular plank grid.
    floor=bpy.data.objects.get('Floor plank')
    if floor is None: floor=next(o for o in bpy.context.scene.objects if o.name.startswith('Floor plank'))
    wood=floor.data.materials[0]
    box('Finished floor north edge',(0,3.18,.002),(L,.151,.018),wood)
    box('Finished floor east edge',(7.2,.18,.002),(.019,3,.018),wood)
    for x in [.018,L-.032]: box('Short wall skirting',(x,.158,.02),(.014,W-.158,.085),frame)
    box('South wall skirting',(.032,.158,.02),(L-.064,.014,.085),frame)
    for x0,x1 in [(0,.218),(1.137,L)]: box('North wall skirting',(x0,W-.014,.02),(x1-x0,.014,.085),frame)
    return bpy.context.scene


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--indices',type=int,nargs='+')
    ap.add_argument('--overwrite',action='store_true');ap.add_argument('--samples',type=int,default=32);args=ap.parse_args()
    scene=build(args.source)
    preferences=bpy.context.preferences.addons['cycles'].preferences
    preferences.compute_device_type='METAL';preferences.refresh_devices()
    for device in preferences.devices: device.use=device.type=='METAL'
    scene.cycles.device='GPU';scene.cycles.samples=args.samples;scene.cycles.use_denoising=True
    scene.render.threads_mode='FIXED';scene.render.threads=4
    folder=args.output/'inputs/normal/week-07';folder.mkdir(parents=True,exist_ok=True)
    evaluator=args.output/'evaluator/normal';evaluator.mkdir(parents=True,exist_ok=True)
    poses=json.loads((args.source/'evaluator/normal/week-06.json').read_text())['cameras']
    for i,p in enumerate(poses):
        if args.indices and i not in args.indices: continue
        target=folder/p['file']
        if target.exists() and not args.overwrite: continue
        scene.camera.matrix_world=Matrix(p['camera_to_world_blender'])
        bpy.context.view_layer.update();scene.render.filepath=str(target)
        bpy.ops.render.render(write_still=True)
        with Image.open(target) as im:
            exif=im.getexif();exif[271]='Synthetic camera';exif[272]='Blender 36mm sensor / 20mm lens'
            exif[41989]=20;exif[37386]=(20,1)
            exif[306]=(datetime(2026,10,25,17)+timedelta(seconds=i)).strftime('%Y:%m:%d %H:%M:%S')
            im.save(target,quality=95,exif=exif)
        print('COMPLETED',p['file'],flush=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(evaluator/'week-07.blend'))
    (evaluator/'week-07.json').write_text(json.dumps(dict(synthetic=True,week=7,cameras=poses,
        changes=['installed framed glazing and sill','finished both short walls','cleaned room and finished perimeter'],
        baseline_note='New window and short-wall finishes are outside the original six-task IFC baseline.'),indent=2))
    if all((folder/p['file']).exists() for p in poses):
        (folder/'capture-manifest.json').write_text(json.dumps(dict(synthetic=True,zone_id='room-002',
            captured_at='2026-10-25T17:00:00+01:00',photos=[dict(file=p['file'],sha256=hashlib.sha256((folder/p['file']).read_bytes()).hexdigest()) for p in poses]),indent=2))

if __name__=='__main__': main()
