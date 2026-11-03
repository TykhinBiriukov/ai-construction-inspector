"""Additional realistic coverage from existing synthetic scenes, no scene changes.

Photographs are analyzer inputs. Camera poses remain evaluator-only. Original
captures are retained unchanged; this is a new capture revision, not a new truth.
"""
import argparse,json,hashlib
from pathlib import Path
from datetime import datetime,timedelta
import bpy
from mathutils import Vector
from PIL import Image

p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('output',type=Path);p.add_argument('--weeks',nargs='+',type=int,default=[1,2,3,4,5,6]);a=p.parse_args()
for week in a.weeks:
 source=a.source/'inputs/normal'/f'week-{week:02d}';evaluator=a.source/'evaluator/normal';target=a.output/'inputs/normal'/f'week-{week:02d}';target.mkdir(parents=True,exist_ok=True)
 truth=json.loads((evaluator/f'week-{week:02d}.json').read_text());manifest=json.loads((source/'capture-manifest.json').read_text())
 for item in manifest['photos']:
  dst=target/item['file']
  if not dst.exists():dst.hardlink_to(source/item['file'])
 bpy.ops.wm.open_mainfile(filepath=str((evaluator/f'week-{week:02d}.blend').resolve()))
 scene=bpy.context.scene;camera=scene.camera
 prefs=bpy.context.preferences.addons['cycles'].preferences;prefs.compute_device_type='METAL';prefs.refresh_devices()
 for device in prefs.devices:device.use=device.type=='METAL'
 scene.cycles.device='GPU';scene.cycles.samples=32;scene.cycles.use_denoising=True;scene.render.resolution_percentage=100;scene.render.resolution_x=1280;scene.render.resolution_y=960
 # Two overlapping frontal passes across the entire fit-out wall.
 poses=[]
 for y,z,xs in [(2.82,1.35,[.6,1.6,2.6,3.6,4.6,5.6,6.6]),(2.55,1.38,[6.4,5.4,4.4,3.4,2.4,1.4,.4])]:
  poses.extend([((x,y,z),(x,.15,1.38)) for x in xs])
 # Oblique/downward floor views retain wall features for pose estimation.
 for y,xs in [(2.8,[.8,2.0,3.2,4.4,5.6,6.4]),(.55,[6.4,5.6,4.4,3.2,2.0,.8])]:
  poses.extend([((x,y,1.65),(x,1.65 if y>2 else 1.9,.1)) for x in xs])
 poses.extend([((x,y,1.35),(.68,3.38,1.12)) for x,y in [(.5,.6),(1,.65),(1.35,.7),(.55,1.1)]])
 for i,(position,look_at) in enumerate(poses,start=len(manifest['photos'])):
  camera.location=position;camera.rotation_euler=(Vector(look_at)-camera.location).to_track_quat('-Z','Y').to_euler();bpy.context.view_layer.update()
  name=f'photo-{i:03d}.jpg';dest=target/name
  if not dest.exists():
   scene.render.filepath=str(dest.resolve());bpy.ops.render.render(write_still=True)
   with Image.open(dest) as im:
    exif=im.getexif();exif[271]='Synthetic camera';exif[272]='Blender 36mm sensor / 20mm lens';exif[41989]=20;exif[37386]=(20,1);exif[306]=(datetime(2026,9,13,17)+timedelta(weeks=week-1,seconds=i)).strftime('%Y:%m:%d %H:%M:%S');im.save(dest,quality=95,exif=exif)
  truth['cameras'].append(dict(file=name,camera_to_world_blender=[list(row) for row in camera.matrix_world]))
  print('PHOTO',week,i,flush=True)
 manifest['photos']=[dict(file=x['file'],sha256=hashlib.sha256((target/x['file']).read_bytes()).hexdigest()) for x in truth['cameras']]
 manifest['capture_revision']='frontal-wall-floor-door-v2';(target/'capture-manifest.json').write_text(json.dumps(manifest,indent=2))
 out=a.output/'evaluator/normal';out.mkdir(parents=True,exist_ok=True);(out/f'week-{week:02d}.json').write_text(json.dumps(truth,indent=2))
 print('WEEK_COMPLETE',week,len(manifest['photos']),flush=True)
