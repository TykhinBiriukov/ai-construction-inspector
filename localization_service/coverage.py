"""Conservative measured surface tiles for the rectangular-room test, not inspection quality."""
import json
from pathlib import Path
import numpy as np
from localization_service.registration import backproject
from localization_service.openings import tile_intersects_opening

CELL = .10
TOLERANCE = .06


def surface_tiles(points, camera, room):
    """Require measured endpoints near an interior BIM surface; never fill a camera frustum."""
    length, width, height = room['length'], room['width'], room['height']
    # Axis, plane coordinate, inward normal, two surface coordinate axes.
    surfaces = [(0,0,1,1,2),(0,length,-1,1,2),(2,0,-1,0,1),(2,-width,1,0,1),(1,0,1,0,2)]
    tiles = {}
    for axis, plane, sign, u, v in surfaces:
        selected = points[np.abs(points[:,axis]-plane) <= TOLERANCE]
        if not len(selected): continue
        rays = camera-selected
        incidence = sign*rays[:,axis]/np.maximum(np.linalg.norm(rays,axis=1),1e-6)
        selected = selected[incidence >= .3]
        if not len(selected): continue
        indices, counts = np.unique(np.floor(selected[:,[u,v]]/CELL).astype(int),axis=0,return_counts=True)
        for (iu,iv),count in zip(indices,counts):
            if count < 3: continue
            center = np.zeros(3);center[axis]=plane+sign*.012;center[u]=(iu+.5)*CELL;center[v]=(iv+.5)*CELL
            # Entire tile must fit within the measured room surface.
            lo=center.copy();hi=center.copy();lo[[u,v]]-=CELL/2;hi[[u,v]]+=CELL/2
            if lo[0]<-1e-6 or hi[0]>length+1e-6 or lo[1]<-1e-6 or hi[1]>height+1e-6 or lo[2]<-width-1e-6 or hi[2]>1e-6: continue
            if axis!=1 and tile_intersects_opening(room,axis,plane,lo,hi):continue
            key=f'{axis}:{plane}:{iu}:{iv}'
            size=[CELL,CELL,CELL];size[axis]=.006
            tiles[key]={'id':key,'center':center.tolist(),'size':size}
    return tiles


def measured_coverage(room, alignment, observations):
    transform=np.array(alignment['T_bim_arkit']);tiles={}
    for item in observations:
        folder=Path(item['_folder']);frame=json.loads((folder/'frame.json').read_text())
        if not frame.get('confidence') or not frame.get('depth'): continue
        confidence=np.frombuffer((folder/'confidence.u8').read_bytes(),dtype='u1').copy()
        confidence[confidence!=2]=0
        points=backproject(frame,(folder/'depth.f32').read_bytes(),confidence.tobytes(),stride=2)
        points=points@transform[:3,:3].T+transform[:3,3]
        camera=(transform@np.array(frame['T_world_camera']))[:3,3]
        for key,tile in surface_tiles(points,camera,room).items():
            record=tiles.setdefault(key,tile|{'observation_ids':[]})
            record['observation_ids'].append(frame['observation_id'])
    return {'policy':'measured-room-tiles-v1','cell_size_m':CELL,'tiles':list(tiles.values()),
            'meaning':'High-confidence depth endpoints near BIM; not photo quality or inspection approval.'}
