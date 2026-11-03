"""Image-only SfM to rectangular IFC room registration.

Initial release requires one axis-aligned four-wall room and upright survey
photographs. Ambiguous 180-degree alignments are rejected, never guessed.
Only permanent IFC walls/slabs are registration targets; scheduled products are
excluded. No evaluator poses or synthetic state metadata are accepted.
"""
import json
from pathlib import Path
import numpy as np
import ifcopenshell
import ifcopenshell.geom
import pycolmap
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation
from scipy.optimize import least_squares


def reference(path):
    model=ifcopenshell.open(str(path))
    settings=ifcopenshell.geom.settings(); settings.set(settings.USE_WORLD_COORDS,True)
    meshes=[]; walls=[];mutable_panels=[]
    scheduled={o.id() for t in model.by_type('IfcTask') for rel in t.OperatesOn for o in rel.RelatedObjects}
    for identity in scheduled:
        obj=model.by_id(identity)
        if not obj.is_a('IfcProduct') or not obj.Representation:continue
        shape=ifcopenshell.geom.create_shape(settings,obj);vertices=np.array(shape.geometry.verts).reshape(-1,3)
        low,high=vertices.min(0),vertices.max(0);extent=high-low
        if extent[2]>1 and min(extent[:2])<.05 and max(extent[:2])*extent[2]>1:
            mutable_panels.append((low,high))
    for obj in model.by_type('IfcElement'):
        if obj.id() in scheduled or not obj.is_a() in ('IfcWall','IfcWallStandardCase','IfcSlab') or not obj.Representation:continue
        shape=ifcopenshell.geom.create_shape(settings,obj)
        v=np.array(shape.geometry.verts).reshape(-1,3);f=np.array(shape.geometry.faces).reshape(-1,3)
        meshes.append((v,f))
        if obj.is_a('IfcWall'):walls.append((v.min(0),v.max(0)))
    bounds=[]
    for axis in [0,1]:
        pair=[w for w in walls if w[1][axis]-w[0][axis]<.5 and w[1][1-axis]-w[0][1-axis]>1]
        if len(pair)!=2:raise ValueError('Registration requires one axis-aligned rectangular four-wall room')
        pair.sort(key=lambda w:w[0][axis]);bounds.append([pair[0][1][axis],pair[1][0][axis]])
    bounds.append([min(w[0][2] for w in walls),min(w[1][2] for w in walls)])
    bounds=np.array(bounds).T
    if np.any(bounds[1]-bounds[0]<1):raise ValueError('Invalid room dimensions')
    rng=np.random.default_rng(647)
    triangles=np.concatenate([v[f] for v,f in meshes]);cross=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0]);area=np.linalg.norm(cross,axis=1)
    keep=area>1e-10;triangles=triangles[keep];cross=cross[keep];area=area[keep]
    ids=rng.choice(len(area),100000,p=area/area.sum());uv=rng.random((len(ids),2));uv[uv.sum(1)>1]=1-uv[uv.sum(1)>1]
    tri=triangles[ids];points=tri[:,0]+uv[:,:1]*(tri[:,1]-tri[:,0])+uv[:,1:]*(tri[:,2]-tri[:,0])
    normals=cross[ids]/area[ids,None]
    # Outer wall faces and the underside of the slab are not observable from
    # this room. Keeping them introduces a wall-thickness translation ambiguity.
    stable=np.all((points>=bounds[0]-.005)&(points<=bounds[1]+.005),axis=1)
    scoring_points=points[stable].copy()
    for low,high in mutable_panels:
        stable &= ~(np.all((points>=low-.22)&(points<=high+.22),axis=1)&(np.abs(normals[:,2])<.2))
    points=points[stable];normals=normals[stable]
    if len(points)<10000:raise ValueError('Insufficient permanent reference geometry after excluding planned wall changes')
    return bounds,points,normals,scoring_points


def peak(values,low,high):
    values=values[(values>low)&(values<high)]
    if len(values)<30:raise ValueError('Insufficient plane support')
    hist,edges=np.histogram(values,bins=100,range=(low,high));i=hist.argmax();center=(edges[i]+edges[i+1])/2
    nearby=values[np.abs(values-center)<(high-low)/75]
    return float(np.median(nearby))


def register(model_path,ifc):
    bounds,target,normals,scoring_points=reference(ifc);tree=cKDTree(target);score_tree=cKDTree(scoring_points)
    model=pycolmap.Reconstruction(str(model_path))
    points=np.array([p.xyz for p in model.points3D.values() if p.error<1.5 and p.track.length()>=4])
    if len(points)<500:raise ValueError('Insufficient stable reconstruction tracks')
    ups=np.array([-im.cam_from_world().matrix()[1,:3] for im in model.images.values()]);up=ups.mean(0);up/=np.linalg.norm(up)
    centers=np.array([im.projection_center() for im in model.images.values()]);centered=centers-centers.mean(0);flat=centered-np.outer(centered@up,up)
    _,_,v=np.linalg.svd(flat);x=v[0];y=np.cross(up,x);base=np.array([x,y,up])
    if bounds[1,1]-bounds[0,1]>bounds[1,0]-bounds[0,0]:base=np.array([y,-x,up])
    projected=points@base.T
    # Split clouds by alternating tracks before fitting; score unused tracks.
    train=points[::2];heldout=points[1::2]
    train=train[::max(1,len(train)//5000)]
    candidates=[]
    for sign in [1,-1]:
        rotation=np.diag([sign,sign,1])@base;local=points@rotation.T
        locations=[]
        for axis in [0,1]:
            q=np.quantile(local[:,axis],[.03,.5,.97]);locations.append([peak(local[:,axis],q[0]-.1,q[1]),peak(local[:,axis],q[1],q[2]+.1)])
        locations=np.array(locations).T
        scales=(bounds[1,:2]-bounds[0,:2])/(locations[1]-locations[0]);scale=float(scales[np.argmax(bounds[1,:2]-bounds[0,:2])])
        floor=peak(local[:,2],np.quantile(local[:,2],.02),np.quantile(local[:,2],.45))
        translation=np.r_[bounds[:,:2].mean(0)-scale*locations.mean(0),bounds[0,2]-scale*floor]
        for iteration in range(18):
            transformed=scale*train@rotation.T+translation
            distance,idx=tree.query(transformed)
            keep=distance<min(.25,max(.12,float(np.quantile(distance,.75))))
            if np.sum(keep)<300:break
            p=transformed[keep];q=target[idx[keep]];n=normals[idx[keep]]
            # Local Sim(3) increment, robust point-to-plane ICP.
            def residual(a):
                shifted=np.exp(a[6])*(p@Rotation.from_rotvec(a[:3]).as_matrix().T)+a[3:6]
                return np.sum((shifted-q)*n,axis=1)
            fit=least_squares(residual,np.zeros(7),loss='soft_l1',f_scale=.025,max_nfev=30)
            delta=Rotation.from_rotvec(fit.x[:3]).as_matrix();factor=np.exp(fit.x[6])
            rotation=delta@rotation;translation=factor*delta@translation+fit.x[3:6];scale*=factor
            if np.linalg.norm(fit.x)<1e-6:break
        transformed=scale*heldout@rotation.T+translation
        distance,indices=tree.query(transformed)
        orientation_distance,_=score_tree.query(transformed)
        score=float(np.mean(np.minimum(orientation_distance,.3)))
        support=float(np.mean(distance<.08))
        axes=np.argmax(np.abs(normals[indices]),axis=1)
        axis_support=[int(np.sum((distance<.05)&(axes==axis))) for axis in range(3)]
        camera=scale*centers@rotation.T+translation
        inside=float(np.mean(np.all((camera>bounds[0]-.1)&(camera<bounds[1]+.1),axis=1)))
        candidates.append(dict(scale=scale,rotation=rotation.tolist(),translation=translation.tolist(),score_m=score,support_within_8cm=support,cameras_inside=inside,axis_inlier_counts=axis_support))
    candidates.sort(key=lambda c:c['score_m']);best=candidates[0];margin=(candidates[1]['score_m']-best['score_m'])/max(best['score_m'],1e-6)
    accepted=best['support_within_8cm']>=.3 and min(best['axis_inlier_counts'])>=50 and best['cameras_inside']>=.9 and margin>=.08
    return dict(status='registered' if accepted else 'inconclusive',method='upright-rectangular-room-robust-similarity-v1',
                transform=best if accepted else None,candidates=candidates,orientation_margin=margin,room_bounds_m=bounds.tolist(),
                limitations=['Rectangular axis-aligned single-room scope; upright photos required.',
                             'Held-out structural fit is not validation of fine finish layers or hidden work.'])


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('model');p.add_argument('ifc');p.add_argument('output');a=p.parse_args()
    result=register(a.model,a.ifc);Path(a.output).write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
