"""Deterministic multi-view synthetic fit-out surveys, using Blender Cycles CPU.

The input scene is exported from the supplied IFC. Camera ground truth and stage
metadata are written only under evaluator/. Analyzer inputs are photographs.
Run with the separate bpy environment, not the analysis service interpreter.
"""
import argparse
import json
import math
import random
import hashlib
from datetime import datetime,timedelta
from pathlib import Path

import bpy
from PIL import Image
from mathutils import Vector


def material(name, color, roughness=.7, noise_scale=35, metallic=0.):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes.get('Principled BSDF')
    bsdf.inputs['Roughness'].default_value = roughness
    bsdf.inputs['Metallic'].default_value = metallic
    tex = nodes.new('ShaderNodeTexNoise')
    tex.inputs['Scale'].default_value = noise_scale
    tex.inputs['Detail'].default_value = 4
    coord = nodes.new('ShaderNodeTexCoord')
    links.new(coord.outputs['Generated'], tex.inputs['Vector'])
    ramp = nodes.new('ShaderNodeValToRGB')
    ramp.color_ramp.elements[0].color = (*[c*.65 for c in color], 1)
    ramp.color_ramp.elements[1].color = (*color, 1)
    links.new(tex.outputs['Fac'], ramp.inputs['Fac'])
    links.new(ramp.outputs['Color'], bsdf.inputs['Base Color'])
    bump = nodes.new('ShaderNodeBump')
    bump.inputs['Strength'].default_value = .18
    bump.inputs['Distance'].default_value = .015
    links.new(tex.outputs['Fac'], bump.inputs['Height'])
    links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])
    return mat


def box(name, origin, size, mat, bevel=.003):
    bpy.ops.mesh.primitive_cube_add(size=1, location=[a+b/2 for a,b in zip(origin,size)])
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    if bevel:
        mod = obj.modifiers.new('Manufactured edges', 'BEVEL')
        mod.width, mod.segments = bevel, 2
        obj.modifiers.new('Weighted normals', 'WEIGHTED_NORMAL')
    return obj


def cylinder(name, start, end, radius, mat):
    direction = Vector(end)-Vector(start)
    bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=radius,
                                      depth=direction.length, location=(Vector(start)+Vector(end))/2)
    obj = bpy.context.object
    obj.name = name
    obj.rotation_euler = direction.to_track_quat('Z','Y').to_euler()
    obj.data.materials.append(mat)
    return obj


def photo_material(name,materials):
        concrete=material(name,(.55,.52,.47),noise_scale=110)
        nodes,links=concrete.node_tree.nodes,concrete.node_tree.links
        position=nodes.new('ShaderNodeNewGeometry')
        mapping=nodes.new('ShaderNodeVectorMath'); mapping.operation='SCALE'
        mapping.inputs[3].default_value=.12
        links.new(position.outputs['Position'],mapping.inputs[0])
        for filename,socket in [('Diffuse.jpg','Base Color'),('Displacement.jpg',None)]:
            tex=nodes.new('ShaderNodeTexImage')
            tex.image=bpy.data.images.load(str(materials/filename),check_existing=True)
            tex.projection='BOX'; tex.projection_blend=.15
            tex.extension='EXTEND'
            links.new(mapping.outputs['Vector'],tex.inputs['Vector'])
            if socket:
                links.new(tex.outputs['Color'],nodes.get('Principled BSDF').inputs[socket])
            else:
                tex.image.colorspace_settings.name='Non-Color'
                bump=nodes.new('ShaderNodeBump'); bump.inputs['Distance'].default_value=.006
                bump.inputs['Strength'].default_value=.4
                links.new(tex.outputs['Color'],bump.inputs['Height'])
                links.new(bump.outputs['Normal'],nodes.get('Principled BSDF').inputs['Normal'])
        return concrete


def build(scene_data, week, scenario, materials=None):
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    concrete = material('Concrete aggregate', (.55,.52,.47), noise_scale=110)
    wall_materials=[]
    if materials:
        concrete=photo_material('Concrete floor',materials)
        for name in ['concrete_layers','concrete_slab_wall','concrete_block_wall','chipped_concrete']:
            wall_materials.append(photo_material(name,materials/name))
    metal = material('Galvanised steel', (.54,.57,.59), .32, 65, .8)
    board = material('Gypsum paper', (.61,.66,.61), noise_scale=160)
    white = material('Painted plaster', (.82,.79,.72), noise_scale=180)
    timber = material('Timber', (.39,.23,.10), noise_scale=8)
    cable = material('Electrical conduit', (.09,.11,.13), .4)
    red = material('Tool case', (.45,.035,.018), .45)
    glass = material('Window glass', (.65,.78,.86), .12)
    bsdf = glass.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Transmission Weight'].default_value = .75
    bsdf.inputs['IOR'].default_value = 1.45
    L,W,H = scene_data['dimensions']
    for element in scene_data['elements']:
        if element['ifc_class'] in ('IfcDoor', 'IfcWindow'):
            continue
        mesh = bpy.data.meshes.new(element['id'])
        mesh.from_pydata(element['vertices'], [], element['faces'])
        mesh.update()
        obj = bpy.data.objects.new(element['name'], mesh)
        bpy.context.collection.objects.link(obj)
        selected=concrete
        if element['ifc_class']=='IfcWall' and wall_materials:
            selected=wall_materials[int(element['name'].split('-')[1])-1]
        obj.data.materials.append(selected)
    box('Ceiling added as synthetic assumption', (0,0,H), (L,W,.15), white)
    # Window frame details are synthetic assumptions, not measured features.
    for y in [0,W/3,2*W/3,W-.05]:
        box('Window mullion', (L+.04,y,.976), (.07,.05,1.513), white)
    for z in [.976,2.439]:
        box('Window rail', (L+.04,0,z), (.07,W,.05), white)
    box('Glazing', (L+.09,.05,1.026), (.015,W-.1,1.413), glass, 0)
    # Fixed masonry texture details create natural correspondences across surveys.
    for x in [i*.42 for i in range(18)]:
        for z in [.35,.8,1.25,1.7,2.15,2.6]:
            box('Concrete form tie', (min(x+.08,L-.1),.002,z), (.018,.006,.018), cable, .001)
    count = 11
    completed = {'frame':0,'services':0,'boards':0,'finish':0,'floor':0,'door':0}
    frame_n = count
    service_n = 0 if week == 1 else count
    board_n = 0 if week < 3 else (5 if week == 3 else count)
    finish_n = 0 if week < 5 else count
    if scenario == 'delayed':
        service_n = 0 if week < 4 else (6 if week == 4 else count)
        board_n = 0 if week < 5 else (5 if week == 5 else count)
        finish_n = 0
    for i in range(count):
        x = .15 + i*.62
        if i < frame_n:
            for dx in [0,.58]:
                box(f'frame-{i}-{dx}', (x+dx,.065,0), (.035,.065,H-.06), metal)
            for z in [.02,H-.08]:
                box(f'track-{i}-{z}', (x,.065,z), (.615,.065,.035), metal)
            completed['frame'] += 1
        if i < service_n:
            cylinder(f'conduit-{i}', (x+.28,.105,.4), (x+.28,.105,H-.12), .012, cable)
            box(f'outlet-{i}', (x+.24,.09,.35), (.08,.075,.09), cable)
            completed['services'] += 1
        removed = scenario == 'rework' and week == 5 and i in (3,4,5)
        if i < board_n and not removed:
            box(f'board-{i}', (x,.14,.015), (.604,.013,H-.045), board)
            for z in [.18,.65,1.2,1.8,2.5]:
                for dx in [.025,.575]:
                    cylinder('Screw', (x+dx,.153,z), (x+dx,.155,z), .0035, metal)
            completed['boards'] += 1
        if i < finish_n and not removed:
            box(f'finish-{i}', (x,.155,.02), (.61,.003,H-.05), white, .001)
            completed['finish'] += 1
    if week == 6 and scenario != 'delayed':
        rows = 10
        for iy in range(rows):
            for ix in range(12):
                box('Floor plank', (ix*.6,.18+iy*.3,.002), (.597,.297,.018), timber, .001)
        completed['floor'] = rows
    if week == 6 and scenario != 'delayed':
        box('Installed door', (.218,W+.07,.01), (.919,.04,2.163), timber)
        cylinder('Door handle', (.98,W+.015,1.02), (.98,W+.08,1.02), .018, metal)
        completed['door'] = 1
    # Construction clutter changes by week and must not count as installed work.
    rng = random.Random(2900+week)
    for i in range(8 if week < 6 else 2):
        x,y = rng.uniform(1,L-1),rng.uniform(1,W-.45)
        box('Loose timber offcut', (x,y,.025), (.3+rng.random()*.5,.09,.035), timber)
    box('Toolbox', (2.4,2.3,.02), (.48,.28,.25), red)
    if week < 6:
        for i in range(4):
            box('Uninstalled boards stacked', (4.6,2.4,.03+i*.014), (1.8,.65,.013), board)
        for x in [3.1,3.6]:
            cylinder('Trestle leg', (x,1.9,.02), (x,2.15,.85), .025, metal)
            cylinder('Trestle leg', (x,2.4,.02), (x,2.15,.85), .025, metal)
        box('Work bench', (2.95,1.85,.85), (.8,.6,.045), timber)
    world = bpy.data.worlds.new('Daylight')
    bpy.context.scene.world = world
    world.use_nodes = True
    world.node_tree.nodes['Background'].inputs[0].default_value = (.65,.75,1,1)
    world.node_tree.nodes['Background'].inputs[1].default_value = .35
    for name,position,power,size,target in [
        ('Window daylight',(L+.45,W/2,2),550,3,(3,W/2,1)),
        ('Work light',(2,W/2,H-.2),160,1,(2,W/2,0)),
        ('Fill',(5,W/2,H-.2),110,1,(5,W/2,0))]:
        light = bpy.data.lights.new(name,'AREA')
        light.energy,light.shape,light.size = power,'DISK',size
        obj = bpy.data.objects.new(name,light)
        bpy.context.collection.objects.link(obj)
        obj.location = position
        obj.rotation_euler = (Vector(target)-obj.location).to_track_quat('-Z','Y').to_euler()
    return completed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scene', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--weeks', type=int, nargs='+', default=[1,2,3,4,5,6])
    ap.add_argument('--scenario', choices=['normal','delayed','rework','missing'], default='normal')
    ap.add_argument('--views', type=int, default=36)
    ap.add_argument('--samples', type=int, default=48)
    ap.add_argument('--width', type=int, default=1280)
    ap.add_argument('--materials',type=Path)
    ap.add_argument('--device',choices=['CPU','METAL'],default='CPU')
    args = ap.parse_args()
    data = json.loads(args.scene.read_text())
    for week in args.weeks:
        truth = build(data,week,args.scenario,args.materials)
        scene = bpy.context.scene
        scene.render.engine = 'CYCLES'
        scene.cycles.device = 'CPU'
        if args.device=='METAL':
            preferences=bpy.context.preferences.addons['cycles'].preferences
            preferences.compute_device_type='METAL'
            preferences.refresh_devices()
            for device in preferences.devices: device.use=device.type=='METAL'
            scene.cycles.device='GPU'
        scene.cycles.samples = args.samples
        scene.cycles.use_denoising = True
        scene.render.threads_mode = 'FIXED'
        scene.render.threads = 6
        scene.render.resolution_x,scene.render.resolution_y = args.width, int(args.width*.75)
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = 'JPEG'
        scene.render.image_settings.quality = 95
        camera_data = bpy.data.cameras.new('Survey camera')
        camera_data.lens,camera_data.sensor_width = 20,36
        camera_data.clip_start = .03
        camera = bpy.data.objects.new('Survey camera',camera_data)
        bpy.context.collection.objects.link(camera)
        scene.camera = camera
        folder = args.output/'inputs'/args.scenario/f'week-{week:02d}'
        folder.mkdir(parents=True,exist_ok=True)
        evaluator = args.output/'evaluator'/args.scenario
        evaluator.mkdir(parents=True,exist_ok=True)
        # Exact final installed geometry can seed the design IFC. This file is
        # generator output, never a reconstruction result.
        if week == 6 and args.scenario == 'normal':
            prefixes={'frame-':'frame','track-':'frame','conduit-':'services',
                      'outlet-':'services','board-':'boards','finish-':'finish',
                      'Floor plank':'floor','Installed door':'door','Door handle':'door'}
            planned=[]
            bpy.context.view_layer.update()
            for obj in bpy.context.scene.objects:
                phase=next((v for k,v in prefixes.items() if obj.name.startswith(k)),None)
                if phase and obj.type=='MESH':
                    obj.data.calc_loop_triangles()
                    planned.append(dict(name=obj.name,phase=phase,
                        vertices=[list(obj.matrix_world @ v.co) for v in obj.data.vertices],
                        faces=[list(t.vertices) for t in obj.data.loop_triangles]))
            (evaluator/'planned-elements.json').write_text(json.dumps(planned))
        poses=[]
        for i in range(args.views):
            if args.scenario == 'missing' and week == 4 and i >= args.views//4:
                continue
            # Continuous ellipse with inward views covers the entire room.
            angle = 2*math.pi*i/args.views
            camera.location = (3.61+2.65*math.cos(angle),1.67+1.02*math.sin(angle),1.55+.12*math.sin(2*angle))
            target = Vector((3.61-1.8*math.cos(angle),1.67-.7*math.sin(angle),1.35))
            camera.rotation_euler = (target-camera.location).to_track_quat('-Z','Y').to_euler()
            bpy.context.view_layer.update()
            name=f'photo-{i:03d}.jpg'
            scene.render.filepath = str(folder/name)
            bpy.ops.render.render(write_still=True)
            # Intrinsics are legitimate camera metadata; extrinsic poses stay hidden.
            with Image.open(folder/name) as frame:
                exif=frame.getexif()
                exif[271]='Synthetic camera'
                exif[272]='Blender 36mm sensor / 20mm lens'
                exif[41989]=20
                exif[37386]=(20,1)
                exif[306]=(datetime(2026,9,13,17)+timedelta(weeks=week-1,seconds=i)).strftime('%Y:%m:%d %H:%M:%S')
                frame.save(folder/name,quality=95,exif=exif)
            poses.append({'file':name,'camera_to_world_blender':[list(row) for row in camera.matrix_world]})
        (folder/'capture-manifest.json').write_text(json.dumps(dict(
            synthetic=True,zone_id='room-002',
            captured_at=(datetime(2026,9,13,17)+timedelta(weeks=week-1)).isoformat()+'+02:00',
            photos=[dict(file=p['file'],sha256=hashlib.sha256((folder/p['file']).read_bytes()).hexdigest()) for p in poses]),indent=2))
        (evaluator/f'week-{week:02d}.json').write_text(json.dumps({'synthetic':True,'week':week,'counts':truth,'cameras':poses},indent=2))
        bpy.ops.wm.save_as_mainfile(filepath=str(evaluator/f'week-{week:02d}.blend'))


if __name__ == '__main__':
    main()
