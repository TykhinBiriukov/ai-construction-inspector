"""Approximate opening contours from semantic mesh edges, without model calls.

Exact wall/object interfaces and incomplete object/unknown boundaries are kept
separately. Rectangles are hypotheses unless all four sides have wall support.
IFC geometry is never an input to grouping, plane fitting or line estimation.
"""
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from geometry_engine.common.mesh import read_mesh
from geometry_engine.segmentation.mask_projection import file_hash, save_json
from geometry_engine.segmentation.service import _rename
from geometry_engine.capture.session_layout import prepare_session
from geometry_engine.common.plane_fitting import fit_frame_plane


@dataclass(frozen=True)
class MeshBoundarySettings:
    plane_tolerance_m: float = .06
    group_gap_m: float = .08
    line_tolerance_m: float = .04
    min_wall_side_length_m: float = .10
    min_wall_side_coverage: float = .15

    def __post_init__(self):
        if any(not np.isfinite(v) or v<=0 for v in asdict(self).values()):
            raise ValueError('Mesh boundary thresholds must be positive and finite')
        if self.min_wall_side_coverage>1:raise ValueError('Invalid side coverage')


def mesh_edge_incidence(faces):
    """Return unique undirected edges and their incident faces (manifold or open)."""
    faces=np.asarray(faces)
    edges=np.concatenate((faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]))
    edges=np.sort(edges,axis=1);owners=np.tile(np.arange(len(faces)),3)
    order=np.lexsort((edges[:,1],edges[:,0]));edges=edges[order];owners=owners[order]
    start=np.r_[0,np.flatnonzero(np.any(edges[1:]!=edges[:-1],axis=1))+1]
    count=np.diff(np.r_[start,len(edges)])
    left=owners[start];right=np.full(len(start),-1,int);right[count==2]=owners[start[count==2]+1]
    return edges[start],left,right,count


def semantic_contour_edges(edges,left,right,count,classes,instances,members,category,wall,extra_faces=None):
    """All observed object rims and their strictly observed wall interfaces."""
    selected=(classes==category)&np.isin(instances,members)
    if extra_faces is not None:selected[np.asarray(extra_faces,dtype=int)]=True
    a=selected[left];b=np.zeros(len(left),bool);valid=right>=0;b[valid]=selected[right[valid]]
    rim=(a!=b)&(count<=2)
    neighbour=np.where(a,right,left)
    strict=rim&(neighbour>=0)
    strict[strict]=classes[neighbour[strict]]==wall
    return edges[rim],edges[strict]


def _basis(normal):
    n=np.asarray(normal,float);n=n/np.linalg.norm(n)
    # Deterministic sign, independent of IFC or camera viewing direction.
    if n[np.argmax(np.abs(n))]<0:n=-n
    vertical=np.array([0.,1.,0.]);vertical-=n*(vertical@n);vertical/=np.linalg.norm(vertical)
    horizontal=np.cross(vertical,n)
    return np.array([horizontal,vertical,n])


def _box(points,axes):
    q=points@axes[:2].T
    return np.array([q.min(0),q.max(0)])


def group_mesh_instances(rows, settings):
    """Merge overlapping coplanar window fragments; never merge doors by class."""
    groups=[]
    def compatible(a,b):
        if a['category']!=b['category'] or a['category']!='window':return False
        if a.get('plane') is None or b.get('plane') is None:return False
        an,ad=a['plane'];bn,bd=b['plane']
        if abs(an@bn)<np.cos(np.deg2rad(8)):return False
        return (np.median(np.abs(a['points']@bn+bd))<=settings.plane_tolerance_m and
                np.median(np.abs(b['points']@an+ad))<=settings.plane_tolerance_m)
    for row in sorted(rows,key=lambda r:(r['category'],r['instance_id'])):
        if row.get('plane') is None:continue
        axes=_basis(row['plane'][0]);box=_box(row['points'],axes);matches=[]
        for g in groups:
            if not all(compatible(row,r) for r in g):continue
            other=_box(np.concatenate([r['points'] for r in g]),axes)
            gap=np.maximum(0,np.maximum(box[0]-other[1],other[0]-box[1]))
            if np.linalg.norm(gap)<=settings.group_gap_m:matches.append(g)
        if not matches:groups.append([row]);continue
        destination=matches[0];destination.append(row)
        for other in matches[1:]:
            if all(compatible(a,b) for a in destination for b in other):
                destination.extend(other);groups.remove(other)
    for row in rows:
        if row.get('plane') is not None:continue
        matches=[]
        for g in groups:
            if row['category']!='window' or g[0]['category']!='window':continue
            if g[0].get('plane') is None:continue
            n,d=g[0]['plane'];axes=_basis(n)
            if np.quantile(np.abs(row['points']@n+d),.9)>settings.plane_tolerance_m:continue
            a=_box(row['points'],axes);b=_box(np.concatenate([r['points'] for r in g]),axes)
            if np.linalg.norm(np.maximum(0,np.maximum(a[0]-b[1],b[0]-a[1])))<=settings.group_gap_m:matches.append(g)
        if len(matches)==1:matches[0].append(row)
        else:groups.append([row])
    return groups


def approximate_rectangle(vertices,rim_edges,wall_edges,normal,offset,settings=None):
    """Fit four gravity-aligned lines to outer edge support, preserving residuals."""
    settings=settings or MeshBoundarySettings();original_normal=np.asarray(normal,float);axes=_basis(normal)
    normal=axes[2];offset=float(offset)*(1 if normal@original_normal>=0 else -1)
    # The original plane normal may be sign-flipped by the deterministic basis.
    origin=vertices[np.unique(rim_edges)].mean(0)
    origin-=normal*(origin@normal+offset)
    points=(vertices[np.unique(rim_edges)]-origin)@axes.T
    low,high=np.quantile(points[:,:2],[.005,.995],axis=0)
    edges3=vertices[rim_edges];q=(edges3-origin)@axes.T
    strict=(vertices[wall_edges]-origin)@axes.T if len(wall_edges) else np.empty((0,2,3))
    quality={'rectangular_prior':True,'gravity_aligned_prior':True,'side_support':{},'reasons':[],
             'absolute_accuracy_verified':False,'boundary_definition_verified':False}
    values={}
    for side,axis,limit in [('left',0,low[0]),('right',0,high[0]),('bottom',1,low[1]),('top',1,high[1])]:
        tangent=1-axis;span=high[tangent]-low[tangent]
        delta=q[:,1,:2]-q[:,0,:2];length=np.linalg.norm(delta,axis=1)
        straight=np.abs(delta[:,axis])<=np.maximum(.002,length*np.sin(np.deg2rad(15)))
        mid=q[:,:,:2].mean(1)
        near=straight&(np.abs(mid[:,axis]-limit)<=max(.04,.03*(high[axis]-low[axis])))
        # Robust constant-coordinate line fit, weighted by actual edge length.
        if near.any():
            x=mid[near,axis];weights=length[near];order=np.argsort(x)
            value=float(x[order][np.searchsorted(np.cumsum(weights[order]),weights.sum()/2)])
        else:value=float(limit);quality['reasons'].append('weak_object_rim_'+side)
        values[side]=value
        err=np.abs(mid[near,axis]-value) if near.any() else np.array([])
        if len(strict):
            ds=strict[:,1,:2]-strict[:,0,:2];sl=np.linalg.norm(ds,axis=1);sm=strict[:,:,:2].mean(1)
            supported=(np.abs(sm[:,axis]-value)<=settings.line_tolerance_m)&(np.abs(ds[:,axis])<=np.maximum(.002,sl*np.sin(np.deg2rad(20))))
            coverage=np.zeros(32,bool)
            for segment in strict[supported]:
                a,b=np.clip(np.sort((segment[:,tangent]-low[tangent])/max(span,1e-6))*32,0,31).astype(int)
                coverage[a:b+1]=True
            wall_length=float(sl[supported].sum());fraction=float(coverage.mean())
        else:wall_length=0.;fraction=0.
        observed=wall_length>=settings.min_wall_side_length_m and fraction>=settings.min_wall_side_coverage
        if not observed:quality['reasons'].append('incomplete_wall_interface_'+side)
        quality['side_support'][side]={'wall_interface_length_m':wall_length,'coverage_fraction':fraction,
            'rim_edge_count':int(near.sum()),'rim_line_p95_error_m':float(np.quantile(err,.95)) if len(err) else None,
            'observed':bool(observed)}
    width=values['right']-values['left'];height=values['top']-values['bottom']
    if min(width,height)<.1 or width>15 or height>5:quality['reasons'].append('implausible_rectangle')
    local=np.array([[values['left'],values['top'],0],[values['right'],values['top'],0],
                    [values['right'],values['bottom'],0],[values['left'],values['bottom'],0]])
    corners=local@axes+origin
    return {'status':'accepted' if not quality['reasons'] else 'needs_review',
        'candidate_corners_world_m':corners.tolist(),'candidate_width_m':float(width),'candidate_height_m':float(height),
        'corners_world_m':corners.tolist() if not quality['reasons'] else None,
        'width_m':float(width) if not quality['reasons'] else None,'height_m':float(height) if not quality['reasons'] else None,
        'axes_world':axes.tolist(),'quality':quality}


class MeshBoundaryService:
    def __init__(self,settings=None):self.settings=settings or MeshBoundarySettings()

    def run(self,directory,output_dir=None,*,resume=False,progress=None):
        session=prepare_session(directory);output=Path(output_dir).resolve() if output_dir else session/'mesh_boundaries'
        if output.parent!=session or output.name in {'input','original','segmentation','mesh_segmentation','mesh_preprocessing'}:
            raise ValueError('Use a dedicated stage directory inside the session')
        mesh=session/'mesh_preprocessing/mesh.ply';seg=session/'mesh_segmentation'
        fingerprints={str(p):file_hash(p) for p in [mesh,seg/'labels.npz',seg/'objects.json',seg/'report.json']}
        if output.exists():
            if not resume:raise FileExistsError(output)
            report=json.loads((output/'objects.json').read_text(encoding='utf-8'))
            if report.get('input_fingerprints')!=fingerprints or report.get('parameters')!=asdict(self.settings):
                raise ValueError('Mesh or settings changed; use a new output')
            return report
        vertices,_,faces=read_mesh(mesh)
        with np.load(seg/'labels.npz',allow_pickle=False) as data:
            classes=data['face_class_id'].copy();instances=data['face_instance_id'].copy()
        objects=json.loads((seg/'objects.json').read_text(encoding='utf-8'));names=objects['labels']
        if classes.shape!=(len(faces),) or instances.shape!=classes.shape:raise ValueError('Segmentation length differs from mesh')
        source=json.loads((seg/'report.json').read_text(encoding='utf-8'))
        if source.get('source_mesh_sha256',source.get('source_sha256'))!=file_hash(mesh):
            raise ValueError('Segmentation does not match the prepared mesh')
        rows=[]
        for obj in objects['objects']:
            if obj['label'] not in ('door','window'):continue
            points=vertices[np.unique(faces[instances==obj['instance_id']])]
            if not len(points):continue
            row={**obj,'category':obj['label'],'points':points,'plane':None}
            try:
                n,d,support=fit_frame_plane(points[::max(1,len(points)//2000)])
                row.update(plane=(n,d),plane_support=support)
            except ValueError:pass
            rows.append(row)
        if progress:progress('Extracting semantic mesh edges')
        edges,left,right,count=mesh_edge_incidence(faces);results=[]
        with TemporaryDirectory(prefix='.mesh-boundaries-',dir=session) as tmp:
            stage=Path(tmp)/'result';stage.mkdir()
            groups=group_mesh_instances(rows,self.settings)
            # Semantically labelled faces without a fused instance still belong
            # to the class contour. Attach only spatially bounded, coplanar ones.
            extra={i:[] for i in range(len(groups))}
            unassigned=np.flatnonzero((instances<0)&np.isin(classes,[names.index(c) for c in ('door','window') if c in names]))
            centers=vertices[faces[unassigned]].mean(1) if len(unassigned) else np.empty((0,3))
            distances=np.full((len(groups),len(unassigned)),np.inf)
            for i,group in enumerate(groups):
                seed=next((r for r in group if r.get('plane') is not None),None)
                if seed is None:continue
                n,d=seed['plane'];axes=_basis(n);bounds=_box(np.concatenate([r['points'] for r in group]),axes)
                q=centers@axes[:2].T;distance=np.abs(centers@n+d)
                eligible=(classes[unassigned]==names.index(group[0]['category']))&(distance<=self.settings.plane_tolerance_m)
                eligible &= np.all(q>=bounds[0]-.08,axis=1)&np.all(q<=bounds[1]+.08,axis=1)
                distances[i,eligible]=distance[eligible]
            if len(unassigned) and len(groups):
                best=np.argmin(distances,axis=0);valid=np.min(distances,axis=0)<np.inf
                for i in range(len(groups)):extra[i]=unassigned[valid&(best==i)].tolist()
            for index,group in enumerate(groups):
                identity=f'mesh_{group[0]["category"]}_{index:03d}';members=[r['instance_id'] for r in group]
                category=group[0]['category'];rim,wall=semantic_contour_edges(edges,left,right,count,classes,instances,members,names.index(category),names.index('wall'),extra[index])
                points=np.concatenate([r['points'] for r in group]);result={'status':'needs_review','quality':{'reasons':['no_plane_or_contour']}}
                plane=None
                try:
                    n,d,support=fit_frame_plane(points[::max(1,len(points)//3000)])
                    keep=np.max(np.abs(vertices[rim]@n+d),axis=1)<=self.settings.plane_tolerance_m
                    rim=rim[keep];wall=wall[np.max(np.abs(vertices[wall]@n+d),axis=1)<=self.settings.plane_tolerance_m]
                    if len(rim)<4:raise ValueError('Insufficient planar contour')
                    result=approximate_rectangle(vertices,rim,wall,n,d,self.settings)
                    plane={'normal':n.tolist(),'offset_m':float(d),'support':support}
                except ValueError as error:result['quality']['reasons']=[str(error)]
                face_ids=np.union1d(np.flatnonzero(np.isin(instances,members)&(classes==names.index(category))),extra[index]).astype(int)
                np.savez_compressed(stage/(identity+'.npz'),rim_edges=rim,wall_interface_edges=wall,face_ids=face_ids)
                result.update(object_id=identity,category=category,measurement_target='door_opening' if category=='door' else 'outer_frame',
                    source_instance_ids=members,observation_ids=sorted({o for r in group for o in r.get('observation_ids',[])}),
                    plane=plane,face_count=len(face_ids),rim_edge_count=len(rim),wall_interface_edge_count=len(wall),
                    artifact=identity+'.npz',artifact_sha256=file_hash(stage/(identity+'.npz')),
                    unassigned_class_faces_included=len(extra[index]),method='semantic_mesh_contour_approximation',absolute_accuracy_verified=False)
                results.append(result)
                if progress:progress(f'{identity}: {len(rim)} rim edges / {len(wall)} direct wall edges / {result["status"]}')
            for path,digest in fingerprints.items():
                if file_hash(path)!=digest:raise ValueError('Input changed during contour extraction')
            report={'schema_version':'1.0','status':'completed','coordinate_system':'arkit','unit':'meter',
                'objects':results,'parameters':asdict(self.settings),'model':None,'input_fingerprints':fingerprints,
                'boundary_method':'semantic_mesh_contour_approximation','grouping_uses_ifc':False,
                'limitations':['Approximation uses existing segmentation; label errors remain.',
                    'Candidate rectangles may bridge unknown contour parts; each side retains observed wall-interface support.',
                    'Plane projection and rectangular prior are approximations; no certified accuracy.']}
            save_json(stage/'objects.json',report);save_json(stage/'report.json',{k:v for k,v in report.items() if k!='objects'})
            _rename(stage,output)
        return report
