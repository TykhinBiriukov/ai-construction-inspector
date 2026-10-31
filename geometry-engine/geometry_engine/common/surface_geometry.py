"""Display-only clipping to the interior half-spaces of a single convex room."""
import numpy as np


def surface_plane(vertices, faces):
    t=np.asarray(vertices)[faces]
    cross=np.cross(t[:,1]-t[:,0],t[:,2]-t[:,0])
    i=int(np.argmax(np.linalg.norm(cross,axis=1)))
    n=cross[i]/np.linalg.norm(cross[i])
    return t[i,0].copy(),n


def clip_mesh(vertices, faces, planes):
    result=[];triangles=[]
    for triangle in np.asarray(vertices)[faces]:
        polygon=list(triangle)
        for origin,normal in planes:
            clipped=[]
            for a,b in zip(polygon,polygon[1:]+polygon[:1]):
                da,db=np.dot(a-origin,normal),np.dot(b-origin,normal)
                inside_a,inside_b=da>=-1e-8,db>=-1e-8
                if inside_a:clipped.append(a)
                if inside_a!=inside_b:clipped.append(a+(b-a)*(da/(da-db)))
            polygon=clipped
            if not polygon:break
        for i in range(1,len(polygon)-1):
            tri=np.array([polygon[0],polygon[i],polygon[i+1]])
            if np.linalg.norm(np.cross(tri[1]-tri[0],tri[2]-tri[0]))<1e-10:continue
            triangles.append([len(result),len(result)+1,len(result)+2]);result.extend(tri)
    return np.asarray(result,float).reshape(-1,3),np.asarray(triangles,int).reshape(-1,3)


def opening_on_wall(vertices, faces, plane):
    origin,n=plane
    v=np.asarray(vertices)-np.outer((np.asarray(vertices)-origin)@n,n)
    t=v[faces];good=np.linalg.norm(np.cross(t[:,1]-t[:,0],t[:,2]-t[:,0]),axis=1)>1e-10
    return v,faces[good]


def measure_against_display_opening(measurement, opening_vertices):
    """Compare unchanged observed extents to the actual displayed opening plane."""
    from itertools import product
    from geometry_engine.comparison.opening_comparison import measure_observation
    axes=np.array([measurement[k] for k in ('u_axis','v_axis','normal_axis')])
    origin=np.array(measurement['origin_ifc_world_m'])
    local=(np.asarray(opening_vertices)-origin)@axes.T
    bounds=np.array(measurement['observed_bounds_local_m'])
    points=np.array(list(product(*zip(*bounds))))@axes+origin
    ref=dict(origin_m=origin,u_axis=axes[0],v_axis=axes[1],normal_axis=axes[2],
             bounds_local_m=[local.min(0),local.max(0)])
    result=measure_observation(points,ref)
    result['basis']='observed_region_vs_ifc_opening_at_interior_wall'
    return result
