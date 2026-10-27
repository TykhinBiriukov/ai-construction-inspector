"""Triangle-mesh preparation and indexing, without reconstructing a point cloud."""

from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components


def read_mesh(path):
    """Read the little-endian triangle PLY used by the scene export contract."""
    types={"float":"<f4","float32":"<f4","double":"<f8","float64":"<f8",
           "uchar":"u1","uint8":"u1"}
    with Path(path).open("rb") as handle:
        if handle.readline().strip()!=b"ply":raise ValueError("Expected PLY")
        properties=[];element=None;vertex_count=face_count=None;face_property=None;format_seen=False;element_order=[]
        while True:
            line=handle.readline()
            if not line:raise ValueError("Truncated PLY header")
            tokens=line.decode("ascii").strip().split()
            if not tokens:continue
            if tokens[0]=="end_header":break
            if tokens[0]=="format":
                if tokens[1:]!=["binary_little_endian","1.0"]:raise ValueError("Expected binary_little_endian PLY 1.0")
                format_seen=True
            if tokens[0]=="element":
                element=tokens[1]
                element_order.append(element)
                if element=="vertex":vertex_count=int(tokens[2])
                elif element=="face":face_count=int(tokens[2])
                else:raise ValueError(f"Unsupported PLY element: {element}")
            if tokens[0]=="property":
                if element=="vertex":
                    if len(tokens)!=3 or tokens[1] not in types:raise ValueError("Unsupported vertex encoding")
                    if tokens[2] not in {"x","y","z","red","green","blue","alpha"}:
                        raise ValueError(f"Unsupported vertex attribute, refusing to discard: {tokens[2]}")
                    properties.append((tokens[2],types[tokens[1]]))
                elif element=="face":
                    if face_property is not None:raise ValueError("Extra face attributes need a dedicated adapter")
                    face_property=tokens[1:]
        if not format_seen or element_order!=["vertex","face"] or vertex_count is None or face_count is None or face_property!=["list","uchar","int","vertex_indices"]:
            raise ValueError("Expected vertex records followed by triangle face records")
        data=np.fromfile(handle,dtype=properties,count=vertex_count)
        records=np.fromfile(handle,dtype=[("count","u1"),("indices","<i4",3)],count=face_count)
        if len(data)!=vertex_count or len(records)!=face_count or handle.read(1):raise ValueError("Unexpected PLY length")
        if not np.all(records["count"]==3):raise ValueError("Only triangles are supported")
    if not {"x","y","z"}.issubset(data.dtype.names):raise ValueError("Missing vertex coordinates")
    vertices=np.column_stack([data[name] for name in ("x","y","z")]).astype(np.float64)
    colors=np.full((vertex_count,4),255,dtype=np.uint8)
    for axis,name in enumerate(("red","green","blue","alpha")):
        if name in data.dtype.names:colors[:,axis]=data[name]
    return vertices,colors,records["indices"].astype(np.int32)


def write_mesh(path,vertices,rgba,faces):
    storage="<f4" if np.array_equal(vertices.astype(np.float32).astype(np.float64),vertices) else "<f8"
    coordinate_type="float" if storage=="<f4" else "double"
    records=np.empty(len(vertices),dtype=[("xyz",storage,3),("rgba","u1",4)])
    records["xyz"]=vertices;records["rgba"]=rgba
    triangles=np.empty(len(faces),dtype=[("count","u1"),("indices","<i4",3)])
    triangles["count"]=3;triangles["indices"]=faces
    header=("ply\nformat binary_little_endian 1.0\n"
            f"element vertex {len(vertices)}\nproperty {coordinate_type} x\nproperty {coordinate_type} y\nproperty {coordinate_type} z\n"
            "property uchar red\nproperty uchar green\nproperty uchar blue\nproperty uchar alpha\n"
            f"element face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n")
    with Path(path).open("wb") as handle:
        handle.write(header.encode("ascii"));records.tofile(handle);triangles.tofile(handle)


def prepare_mesh(vertices,rgba,faces,area_epsilon=1e-12):
    if vertices.ndim!=2 or vertices.shape[1]!=3 or faces.ndim!=2 or faces.shape[1]!=3:
        raise ValueError("Expected N x 3 vertices and M x 3 triangle indices")
    if rgba.shape!=(len(vertices),4) or not np.issubdtype(faces.dtype,np.integer):
        raise ValueError("Expected RGBA per vertex and integer face indices")
    if not np.isfinite(area_epsilon) or area_epsilon<0:raise ValueError("Invalid area threshold")
    finite=np.isfinite(vertices).all(axis=1)
    finite_ids=np.flatnonzero(finite)
    unique,first,inverse=np.unique(vertices[finite],axis=0,return_index=True,return_inverse=True)
    unique_sources=finite_ids[first]
    original_to_unique=np.full(len(vertices),-1,dtype=np.int32)
    original_to_unique[finite]=inverse
    in_range=np.all((faces>=0)&(faces<len(vertices)),axis=1)
    face_ids=np.flatnonzero(in_range)
    mapped=original_to_unique[faces[in_range]]
    finite_face=np.all(mapped>=0,axis=1)
    nonfinite_face_count=int((~finite_face).sum())
    face_ids=face_ids[finite_face];mapped=mapped[finite_face]
    triangles=unique[mapped]
    double_area=np.linalg.norm(np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0]),axis=1)
    nondegenerate=double_area>2*area_epsilon
    degenerate_count=int((~nondegenerate).sum())
    face_ids=face_ids[nondegenerate];mapped=mapped[nondegenerate]
    if not len(mapped):raise ValueError("No valid triangles remain")
    # Sorted vertex triples catch duplicate geometry with either winding order.
    _,first_face,face_inverse=np.unique(np.sort(mapped,axis=1),axis=0,return_index=True,return_inverse=True)
    selected=mapped[first_face]
    source_face_to_prepared=np.full(len(faces),-1,dtype=np.int32)
    source_face_to_prepared[face_ids]=face_inverse
    used,compact_inverse=np.unique(selected,return_inverse=True)
    compact_faces=compact_inverse.reshape(-1,3).astype(np.int32)
    unique_to_compact=np.full(len(unique),-1,dtype=np.int32)
    unique_to_compact[used]=np.arange(len(used))
    source_vertex_to_prepared=np.full(len(vertices),-1,dtype=np.int32)
    source_vertex_to_prepared[finite]=unique_to_compact[inverse]
    prepared_sources=unique_sources[used]
    cleaned=unique[used]
    cleaned_colors=rgba[prepared_sources]
    tri=cleaned[compact_faces]
    cross=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0])
    lengths=np.linalg.norm(cross,axis=1)
    face_normals=cross/lengths[:,None]
    vertex_normals=np.zeros_like(cleaned)
    for corner in range(3):np.add.at(vertex_normals,compact_faces[:,corner],cross)
    normal_lengths=np.linalg.norm(vertex_normals,axis=1)
    np.divide(vertex_normals,normal_lengths[:,None],out=vertex_normals,where=normal_lengths[:,None]>0)
    # No coordinate is averaged or moved; duplicates keep the first source color.
    mapped_finite=source_vertex_to_prepared[finite]
    comparable=mapped_finite>=0
    color_conflicts=int(np.any(rgba[finite][comparable]!=cleaned_colors[mapped_finite[comparable]],axis=1).sum())
    mapping={"source_vertex_to_prepared":source_vertex_to_prepared,
             "prepared_vertex_source_representative":prepared_sources.astype(np.int32),
             "source_face_to_prepared":source_face_to_prepared,
             "prepared_face_source_representative":face_ids[first_face].astype(np.int32)}
    geometry={"vertices":cleaned,"rgba":cleaned_colors,"faces":compact_faces,
              "face_normals":face_normals,"vertex_normals":vertex_normals,"face_areas_m2":lengths*.5}
    report={"source_vertices":len(vertices),"source_faces":len(faces),
            "nonfinite_vertices_removed":int((~finite).sum()),
            "exact_duplicate_vertices_merged":int(finite.sum()-len(unique)),
            "faces_with_invalid_indices_removed":int((~in_range).sum()),
            "faces_with_nonfinite_vertices_removed":nonfinite_face_count,
            "degenerate_faces_removed":degenerate_count,
            "duplicate_faces_removed":len(mapped)-len(selected),
            "unused_unique_vertices_removed":len(unique)-len(used),
            "duplicate_position_observations_with_different_color":color_conflicts,
            "prepared_vertices":len(cleaned),"prepared_faces":len(compact_faces),
            "zero_vertex_normals":int((normal_lengths==0).sum()),
            "area_epsilon_m2":area_epsilon,
            "bounds_m":[cleaned.min(axis=0).tolist(),cleaned.max(axis=0).tolist()]}
    return geometry,mapping,report


def mesh_topology(vertices,faces,areas):
    edges=np.concatenate((faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]))
    low=edges.min(axis=1).astype(np.uint64);high=edges.max(axis=1).astype(np.uint64)
    keys,inverse,counts=np.unique((low<<np.uint64(32))|high,return_inverse=True,return_counts=True)
    direction=np.where(edges[:,0]<edges[:,1],1.,-1.)
    orientation=np.bincount(inverse,weights=direction)
    u=(keys>>np.uint64(32)).astype(np.int32);v=(keys&np.uint64((1<<32)-1)).astype(np.int32)
    graph=coo_matrix((np.ones(len(keys),dtype=np.uint8),(u,v)),shape=(len(vertices),len(vertices))).tocsr()
    component_count,labels=connected_components(graph,directed=False)
    component_areas=np.bincount(labels[faces[:,0]],weights=areas,minlength=component_count)
    order=np.argsort(component_areas)[::-1]
    return labels.astype(np.int32),{
        "connected_components":int(component_count),"boundary_edges":int((counts==1).sum()),
        "nonmanifold_edges":int((counts>2).sum()),
        "inconsistent_winding_edges":int(((counts==2)&(np.abs(orientation)>1)).sum()),
        "total_surface_area_m2":float(areas.sum()),
        "largest_component_areas_m2":[float(component_areas[i]) for i in order[:10]],
        "small_components_removed":False,"holes_filled":False,"winding_reoriented":False,
    }


def build_bvh(vertices,faces,leaf_size=64):
    triangles=vertices[faces]
    lower=triangles.min(axis=1);upper=triangles.max(axis=1);centers=(lower+upper)*.5
    order=np.arange(len(faces),dtype=np.int32)
    bounds_min=[];bounds_max=[];left=[];right=[];start=[];count=[]
    def build(begin,end):
        identity=len(left);ids=order[begin:end]
        bounds_min.append(lower[ids].min(axis=0));bounds_max.append(upper[ids].max(axis=0))
        left.append(-1);right.append(-1);start.append(begin);count.append(end-begin)
        if end-begin>leaf_size:
            axis=int(np.argmax(np.ptp(centers[ids],axis=0)))
            middle=(end-begin)//2
            order[begin:end]=ids[np.argpartition(centers[ids,axis],middle)]
            left[identity]=build(begin,begin+middle);right[identity]=build(begin+middle,end)
            count[identity]=0
        return identity
    build(0,len(faces))
    return {"bounds_min":np.array(bounds_min),"bounds_max":np.array(bounds_max),
            "left":np.array(left,dtype=np.int32),"right":np.array(right,dtype=np.int32),
            "start":np.array(start,dtype=np.int32),"count":np.array(count,dtype=np.int32),
            "face_order":order}


def raycast(origin,direction,vertices,faces,bvh):
    """Nearest two-sided triangle hit; distance is metres along a normalized ray."""
    origin=np.asarray(origin,dtype=float);direction=np.asarray(direction,dtype=float)
    length=np.linalg.norm(direction)
    if not np.isfinite(length) or length==0:raise ValueError("Invalid ray direction")
    direction=direction/length;nearest=np.inf;hit=-1;stack=[0]
    while stack:
        node=stack.pop();lo=bvh["bounds_min"][node];hi=bvh["bounds_max"][node]
        parallel=np.abs(direction)<1e-15
        if np.any(parallel&((origin<lo)|(origin>hi))):continue
        a=np.divide(lo-origin,direction,out=np.full(3,-np.inf),where=~parallel)
        b=np.divide(hi-origin,direction,out=np.full(3,np.inf),where=~parallel)
        if max(0.,np.minimum(a,b).max())>min(nearest,np.maximum(a,b).min()):continue
        if bvh["count"][node]==0:
            stack.extend((int(bvh["left"][node]),int(bvh["right"][node])));continue
        begin=int(bvh["start"][node]);end=begin+int(bvh["count"][node])
        ids=bvh["face_order"][begin:end];tri=vertices[faces[ids]]
        e1=tri[:,1]-tri[:,0];e2=tri[:,2]-tri[:,0]
        p=np.cross(direction,e2);det=np.einsum("ij,ij->i",e1,p)
        valid=np.abs(det)>1e-14
        inv=np.divide(1.,det,out=np.zeros(len(det)),where=valid)
        tvec=origin-tri[:,0];u=np.einsum("ij,ij->i",tvec,p)*inv
        q=np.cross(tvec,e1);v=q@direction*inv;t=np.einsum("ij,ij->i",e2,q)*inv
        valid &= (u>=-1e-10)&(v>=-1e-10)&(u+v<=1+1e-10)&(t>1e-8)&(t<nearest)
        if np.any(valid):
            choices=np.flatnonzero(valid);which=choices[np.argmin(t[choices])]
            nearest=float(t[which]);hit=int(ids[which])
    return {"face_index":hit,"distance_m":None if hit<0 else nearest}


def render_mesh_depth(vertices,faces,metadata):
    """Project the existing mesh into a camera; no point-cloud reconstruction."""
    transform=np.asarray(metadata["pose"]["T_world_from_camera"])
    camera=(vertices-transform[:3,3])@transform[:3,:3]
    triangles=camera[faces]
    triangles=triangles[np.all(triangles[:,:,2]>.05,axis=1)]
    k=metadata["intrinsics"];height,width=k["height"],k["width"]
    uv=triangles[:,:,:2]/triangles[:,:,2,None]
    uv[:,:,0]=uv[:,:,0]*k["fx"]+k["cx"];uv[:,:,1]=uv[:,:,1]*k["fy"]+k["cy"]
    lower=np.maximum(np.ceil(uv.min(1)),[0,0]).astype(int)
    upper=np.minimum(np.floor(uv.max(1)),[width-1,height-1]).astype(int)
    keep=np.all(lower<=upper,axis=1);zbuffer=np.full((height,width),np.inf)
    for triangle,image_triangle,lo,hi in zip(triangles[keep],uv[keep],lower[keep],upper[keep]):
        (x0,y0),(x1,y1),(x2,y2)=image_triangle
        denominator=(y1-y2)*(x0-x2)+(x2-x1)*(y0-y2)
        if abs(denominator)<1e-12:continue
        v,u=np.mgrid[lo[1]:hi[1]+1,lo[0]:hi[0]+1]
        a=((y1-y2)*(u-x2)+(x2-x1)*(v-y2))/denominator
        b=((y2-y0)*(u-x2)+(x0-x2)*(v-y2))/denominator;c=1-a-b
        inside=(a>=-1e-9)&(b>=-1e-9)&(c>=-1e-9)
        reciprocal=a/triangle[0,2]+b/triangle[1,2]+c/triangle[2,2]
        depth=np.divide(1,reciprocal,out=np.full_like(a,np.inf),where=inside&(reciprocal>0))
        section=zbuffer[lo[1]:hi[1]+1,lo[0]:hi[0]+1]
        np.minimum(section,depth,out=section)
    return zbuffer
