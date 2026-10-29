"""Rectangular wall apertures in the current right-handed, Y-up room frame."""
from copy import deepcopy
from itertools import product
import numpy as np


def bounds_after(low, high, transform):
    corners=np.array(list(product(*zip(low,high))))
    points=corners@transform[:3,:3].T+transform[:3,3]
    return points.min(axis=0).tolist(),points.max(axis=0).tolist()


def wall_for(low,high,length,width,tolerance=.06):
    candidates=[]
    for axis,plane in [(0,0.),(0,length),(2,0.),(2,-width)]:
        if low[axis]-tolerance<=plane<=high[axis]+tolerance:
            candidates.append((axis,plane))
    if len(candidates)!=1:raise ValueError('Opening must belong unambiguously to one room wall')
    return candidates[0]


def apertures(room):
    if 'openings' in room:return room['openings']
    # Existing persisted single-door sessions retain their original frame.
    return [dict(axis=0,plane=0.,low=[0.,0.,-room['door_offset']-room['door_width']],
                 high=[0.,room['door_height'],-room['door_offset']])]


def aperture_points(points,room,margin=.08):
    """Ignore points at or beyond any aperture, never foreground interior objects."""
    result=np.zeros(len(points),dtype=bool)
    for opening in apertures(room):
        axis=opening['axis'];plane=opening['plane'];other=2 if axis==0 else 0
        low,high=opening['low'],opening['high']
        inward=1 if axis==0 and plane==0 or axis==2 and plane<0 else -1
        through=(points[:,axis]-plane)*inward<margin
        through&=(points[:,1]>=low[1]-margin)&(points[:,1]<=high[1]+margin)
        through&=(points[:,other]>=low[other]-margin)&(points[:,other]<=high[other]+margin)
        result|=through
    return result


def tile_intersects_opening(room,axis,plane,low,high):
    other=2 if axis==0 else 0
    return any(o['axis']==axis and abs(o['plane']-plane)<1e-6 and
               low[1]<o['high'][1] and high[1]>o['low'][1] and
               low[other]<o['high'][other] and high[other]>o['low'][other]
               for o in apertures(room))


def select_entry(room,element_id,confirmed=True):
    """Rotate room about Y so the chosen door wall is X=0, without reflection."""
    door=next((d for d in room['doors'] if d['element_id']==element_id),None)
    if door is None:raise ValueError('Unknown entry door')
    length,width=room['length'],room['width'];axis,plane=door['axis'],door['plane']
    t=np.eye(4)
    if axis==0 and plane>0:
        t[:3,:3]=np.diag([-1,1,-1]);t[:3,3]=[length,0,-width]
    elif axis==2 and plane==0:
        t[:3,:3]=[[0,0,-1],[0,1,0],[1,0,0]];t[:3,3]=[0,0,-length]
    elif axis==2:
        t[:3,:3]=[[0,0,1],[0,1,0],[-1,0,0]];t[:3,3]=[width,0,0]
    result=deepcopy(room)
    if axis==2:result['length'],result['width']=width,length
    for collection in ('openings','doors'):
        for item in result[collection]:
            item['low'],item['high']=bounds_after(item['low'],item['high'],t)
            item['axis'],item['plane']=wall_for(item['low'],item['high'],result['length'],result['width'])
    selected=next(d for d in result['doors'] if d['element_id']==element_id)
    result.update(entry_element_id=element_id,entry_confirmed=confirmed,
                  bim_frame_id='room-y-up-entry:'+element_id,
                  T_bim_ifc=(t@np.array(room['T_bim_ifc'])).tolist(),
                  door_offset=-selected['high'][2],door_width=selected['high'][2]-selected['low'][2],
                  door_height=selected['high'][1])
    return result


def reframe_meshes(meshes,old_room,new_room):
    transform=np.array(new_room['T_bim_ifc'])@np.linalg.inv(np.array(old_room['T_bim_ifc']))
    return [dict(mesh,vertices=(np.array(mesh['vertices'])@transform[:3,:3].T+transform[:3,3]).tolist(),
                 normals=(np.array(mesh['normals'])@transform[:3,:3].T).tolist()) for mesh in meshes]
