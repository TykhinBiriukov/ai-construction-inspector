"""Independent CPU RGB-D reconstruction using Open3D's sparse TSDF fusion.

The original capture is read-only. No mobile mesh, SAM, network or GPU is needed.
Algorithm reference: https://www.open3d.org/docs/latest/tutorial/t_reconstruction_system/integration.html
"""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time

import numpy as np
from scipy.ndimage import maximum_filter, minimum_filter

from .mesh import write_mesh
from .session_layout import prepare_session, raw_original_directory, raw_session
from .raw_capture import load_raw_capture, load_rgbd


@dataclass(frozen=True)
class ReconstructionSettings:
    voxel_size_m: float = .02
    sdf_trunc_m: float = .08
    min_depth_m: float = .15
    max_depth_m: float = 5.
    min_confidence: int = 1
    min_valid_fraction: float = .05
    depth_abs_tolerance_m: float = .05
    depth_relative_tolerance: float = .02
    reject_conflicting_frames: bool = True
    consistency_neighbors: int = 8
    excluded_frame_ids: tuple[str, ...] = ()
    create_preview: bool = True

    def __post_init__(self):
        for name in ('voxel_size_m', 'sdf_trunc_m', 'min_depth_m', 'max_depth_m',
                     'depth_abs_tolerance_m', 'depth_relative_tolerance'):
            value = getattr(self, name)
            if isinstance(value, bool) or not np.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be positive and finite')
        if self.voxel_size_m < .005 or self.sdf_trunc_m < 2*self.voxel_size_m:
            raise ValueError('Use voxel_size_m >= 0.005 and sdf_trunc_m >= twice voxel_size_m')
        if self.min_depth_m >= self.max_depth_m:
            raise ValueError('min_depth_m must be below max_depth_m')
        if type(self.min_confidence) is not int or self.min_confidence not in (0,1,2):
            raise ValueError('min_confidence must be 0, 1 or 2')
        if not np.isfinite(self.min_valid_fraction) or not 0 < self.min_valid_fraction <= 1:
            raise ValueError('min_valid_fraction must be in (0,1]')
        if type(self.consistency_neighbors) is not int or not 1 <= self.consistency_neighbors <= 32:
            raise ValueError('consistency_neighbors must be in [1,32]')


def _json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)+'\n', encoding='utf-8')


def _surface(frame, depth):
    """Small samples for diagnostics only; full resolution is integrated into TSDF."""
    mask = (depth > 0) & (maximum_filter(depth, 3)-minimum_filter(depth, 3) < .15)
    v,u = np.nonzero(mask)
    v,u = v[::2],u[::2]
    z = depth[v,u]
    K,T = frame.K_depth,frame.T_world_from_camera_cv
    xyz = np.column_stack(((u-K[0,2])*z/K[0,0],(v-K[1,2])*z/K[1,1],z))
    return xyz @ T[:3,:3].T + T[:3,3],mask


def _depth_comparison(world, target, depth, mask, settings):
    """Reproject with recorded poses, without ICP. Occluded points aren't conflicts."""
    T,K = target.T_world_from_camera_cv,target.K_depth
    q = (world-T[:3,3]) @ T[:3,:3]
    q = q[q[:,2] > settings.min_depth_m]
    uv = q @ K.T
    uv = np.rint(uv[:,:2]/uv[:,2,None]).astype(np.int64)
    inside = (uv[:,0]>=0)&(uv[:,0]<target.width)&(uv[:,1]>=0)&(uv[:,1]<target.height)
    q,uv = q[inside],uv[inside]
    z = np.full(depth.size,np.inf)
    np.minimum.at(z,uv[:,1]*target.width+uv[:,0],q[:,2])
    z=z.reshape(depth.shape)
    overlap=np.isfinite(z)&mask
    count=int(overlap.sum())
    fraction=count/max(1,int(mask.sum()))
    if count < 100 or fraction < .15:
        return None
    residual=z[overlap]-depth[overlap]
    tolerance=settings.depth_abs_tolerance_m+settings.depth_relative_tolerance*depth[overlap]
    return dict(compared_pixels=count,overlap_fraction=fraction,
                agreement_fraction=float(np.mean(np.abs(residual)<=tolerance)),
                foreground_conflict_fraction=float(np.mean(residual < -tolerance)),
                possible_occlusion_fraction=float(np.mean(residual > tolerance)),
                median_abs_depth_difference_m=float(np.median(np.abs(residual))))


class ReconstructionService:
    def __init__(self, settings=None):
        self.settings = settings or ReconstructionSettings()

    def reconstruct(self, session_dir, output_dir=None, *, progress=None):
        """Read a multipart/flat raw capture and publish SESSION/reconstruction.

        Existing outputs are not overwritten. The result remains in original ARKit
        world coordinates (metres, Y up), so original camera poses remain usable.
        """
        try:
            import open3d as o3d
        except ImportError as exc:
            raise RuntimeError('Install reconstruction dependencies: python -m pip install -r requirements-reconstruction.txt') from exc
        start=time.monotonic()
        root=prepare_session(session_dir)
        output=Path(output_dir).resolve() if output_dir is not None else root/'reconstruction'
        if output == root or not output.is_relative_to(root) or output.is_relative_to(raw_original_directory(root)) or output.is_relative_to(root/'input'):
            raise ValueError('Output must be a new folder inside the session, outside original')
        if output.exists():
            raise FileExistsError(f'Output already exists: {output}; use another --output folder')
        frames=load_raw_capture(root)
        unknown=set(self.settings.excluded_frame_ids)-{f.frame_id for f in frames}
        if unknown:
            raise ValueError('Unknown excluded frame IDs: '+', '.join(sorted(unknown)))
        output.parent.mkdir(parents=True,exist_ok=True)
        lock=output.with_name(output.name+'.lock')
        with lock.open('x',encoding='utf-8') as handle:
            handle.write('Reconstruction in progress\n')
        try:
            with TemporaryDirectory(prefix='.reconstruction-',dir=output.parent) as temporary:
                staging=Path(temporary)/'result'
                staging.mkdir()
                report=self._build(root,frames,staging,o3d,progress)
                report['elapsed_seconds']=round(time.monotonic()-start,3)
                _json(staging/'report.json',report)
                staging.rename(output)
            return report
        finally:
            lock.unlink(missing_ok=True)

    def _build(self, root, frames, output, o3d, progress):
        s=self.settings
        volume=o3d.t.geometry.VoxelBlockGrid(
            attr_names=('tsdf','weight','color'),
            attr_dtypes=(o3d.core.float32,o3d.core.float32,o3d.core.float32),
            attr_channels=((1,),(1,),(3,)),voxel_size=s.voxel_size_m,
            block_resolution=16,block_count=1000,device=o3d.core.Device('CPU:0'))
        records=[]; references=[]; positions=[]; warnings=[]
        for index,frame in enumerate(frames):
            rgb,depth,stats=load_rgbd(frame,min_confidence=s.min_confidence,
                min_depth_m=s.min_depth_m,max_depth_m=s.max_depth_m)
            record=dict(frame_id=frame.frame_id,timestamp_s=frame.timestamp_s,
                metadata_path='original/'+frame.metadata_path.relative_to(raw_original_directory(root)).as_posix(),
                pixels=stats,status='integrated',consistency='unverified',comparisons=[])
            if frame.frame_id in s.excluded_frame_ids:
                record.update(status='skipped',reason='explicit_exclusion')
            elif frame.tracking_state not in ('normal','unknown'):
                record.update(status='skipped',reason='tracking_not_normal')
            elif stats['valid_fraction'] < s.min_valid_fraction:
                record.update(status='skipped',reason='insufficient_valid_depth')
            if record['status']=='integrated':
                world,mask=_surface(frame,depth)
                # Compare with nearby accepted views; never reject for elapsed time.
                nearest=sorted(references,key=lambda ref:np.linalg.norm(
                    frame.T_world_from_camera_cv[:3,3]-ref[0].T_world_from_camera_cv[:3,3]))[:s.consistency_neighbors]
                conflicts=0;support=0
                for ref,rd,rw,rm in nearest:
                    forward=_depth_comparison(rw,frame,depth,mask,s)
                    backward=_depth_comparison(world,ref,rd,rm,s)
                    if forward is None or backward is None:
                        continue
                    record['comparisons'].append(dict(reference_frame_id=ref.frame_id,
                                                      reference_to_current=forward,current_to_reference=backward))
                    if min(forward['agreement_fraction'],backward['agreement_fraction']) >= .7:
                        support+=1
                    elif (max(forward['agreement_fraction'],backward['agreement_fraction']) < .35
                          and max(forward['foreground_conflict_fraction'],backward['foreground_conflict_fraction']) > .5):
                        conflicts+=1
                if support:
                    record['consistency']='supported'
                elif conflicts:
                    record['consistency']='needs_review'
                if conflicts >= 2 and not support and s.reject_conflicting_frames:
                    record.update(status='skipped',reason='conflict_with_multiple_views')
                else:
                    intrinsic=o3d.core.Tensor(frame.K_depth,dtype=o3d.core.float64)
                    extrinsic=o3d.core.Tensor(np.linalg.inv(frame.T_world_from_camera_cv),dtype=o3d.core.float64)
                    depth_image=o3d.t.geometry.Image(np.ascontiguousarray(depth))
                    color_image=o3d.t.geometry.Image(np.ascontiguousarray(rgb,dtype=np.float32))
                    trunc=s.sdf_trunc_m/s.voxel_size_m
                    blocks=volume.compute_unique_block_coordinates(depth_image,intrinsic,extrinsic,1.,s.max_depth_m,trunc)
                    volume.integrate(blocks,depth_image,color_image,intrinsic,extrinsic,1.,s.max_depth_m,trunc)
                    references.append((frame,depth,world,mask))
                    # Bound diagnostics memory; TSDF still accumulates all accepted frames.
                    if len(references)>64: references.pop(0)
                    positions.append(frame.T_world_from_camera_cv[:3,3].tolist())
            records.append(record)
            if progress: progress(f"Frame {index+1}/{len(frames)}: {record['status']} ({record['consistency']})")
        if not positions:
            raise ValueError('No usable frames; check depth, confidence and tracking state')
        if progress: progress('Extracting point cloud and triangle mesh from TSDF...')
        # threshold=0 keeps singly observed surfaces, useful for sparse mobile captures.
        # Float32 color integration uses 0..255; legacy exports expect 0..1.
        tensor_mesh=volume.extract_triangle_mesh(weight_threshold=0.)
        if len(tensor_mesh.vertex.positions)==0:
            raise ValueError('TSDF produced no mesh; check depth and frame coverage')
        tensor_mesh.vertex.colors=tensor_mesh.vertex.colors/255.
        mesh=tensor_mesh.to_legacy()
        tensor_cloud=volume.extract_point_cloud(weight_threshold=0.)
        cloud_kind='surface points extracted from fused TSDF (not concatenated raw samples)'
        if 'positions' in tensor_cloud.point and len(tensor_cloud.point.positions):
            tensor_cloud.point.colors=tensor_cloud.point.colors/255.
            cloud=tensor_cloud.to_legacy()
        else:
            # Open3D point extraction may miss zero crossings exactly on a voxel plane.
            cloud=o3d.geometry.PointCloud(mesh.vertices)
            cloud.colors=mesh.vertex_colors
            cloud_kind='TSDF mesh vertices (point extraction returned no zero crossings)'
        # Floating-point averaging may exceed 1 by a few ULPs; clamp for PLY RGB.
        cloud.colors=o3d.utility.Vector3dVector(np.clip(np.asarray(cloud.colors),0.,1.))
        mesh.remove_degenerate_triangles().remove_duplicated_triangles().remove_unreferenced_vertices()
        vertices=np.asarray(mesh.vertices);faces=np.asarray(mesh.triangles)
        colors=np.asarray(mesh.vertex_colors)
        if not len(cloud.points) or not len(faces) or not np.isfinite(vertices).all():
            raise ValueError('TSDF produced no valid surface; check frame coverage/depth and voxel size')
        rgba=np.column_stack((np.clip(np.rint(colors*255),0,255).astype(np.uint8),np.full(len(vertices),255,dtype=np.uint8)))
        # Existing Geometry Engine PLY reader supports xyz+RGBA, but not normal fields.
        write_mesh(output/'mesh.ply',vertices,rgba,faces)
        if not o3d.io.write_point_cloud(str(output/'point_cloud.ply'),cloud,write_ascii=False):
            raise OSError('Failed to write point_cloud.ply')
        _json(output/'cameras.json',dict(coordinate_system='arkit',unit='meter',frames=[
            dict(frame_id=f.frame_id,timestamp_s=f.timestamp_s,
                 K_depth=f.K_depth.tolist(),width=f.width,height=f.height,
                 T_world_from_camera_cv=f.T_world_from_camera_cv.tolist(),
                 status=r['status'],metadata_path=r['metadata_path']) for f,r in zip(frames,records)]))
        outputs=dict(mesh='mesh.ply',point_cloud='point_cloud.ply',cameras='cameras.json',report='report.json')
        if s.create_preview:
            from .reconstruction_preview import write_reconstruction_preview
            outputs.update(write_reconstruction_preview(output,vertices,colors,faces,np.asarray(positions)))
        if any(r['status']=='skipped' for r in records): warnings.append('Some frames were excluded; see per-frame reasons.')
        if any(r['consistency']!='supported' and r['status']=='integrated' for r in records):
            warnings.append('Some integrated frames lack multi-view confirmation. This does not prove they are incorrect.')
        warnings.append('Reconstruction is not a guarantee of complete surfaces or measurement accuracy; no hole filling or pose refinement was applied.')
        artifacts={name:hashlib.sha256((output/outputs[name]).read_bytes()).hexdigest() for name in ('mesh','point_cloud')}
        return dict(schema_version='1.0',status='reconstructed',session_id=frames[0].session_id,
            engine=dict(name='Open3D VoxelBlockGrid TSDF',version=o3d.__version__,device='CPU'),
            coordinate_system='arkit',unit='meter',settings=asdict(s),
            total_frames=len(frames),integrated_frames=len(positions),skipped_frames=len(frames)-len(positions),
            point_count=len(cloud.points),vertex_count=len(vertices),triangle_count=len(faces),
            bounds_m=[vertices.min(axis=0).tolist(),vertices.max(axis=0).tolist()],
            point_cloud_kind=cloud_kind,
            consistency_policy=dict(tolerance='absolute + relative * depth',min_overlap_fraction=.15,
                reject='At least two conflicting accepted views, no supporting view; heuristic, not ground truth'),
            frames=records,outputs=outputs,sha256=artifacts,warnings=warnings)
