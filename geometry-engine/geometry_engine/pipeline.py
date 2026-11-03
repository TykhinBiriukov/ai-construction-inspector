"""Sequential raw-capture pipeline. Each stage publishes its own artifacts."""

import json
import hashlib
import os
import shutil
import numpy as np
from pathlib import Path
from tempfile import TemporaryDirectory

from geometry_engine.capture.raw_capture import load_raw_capture, frame_metadata
from geometry_engine.reconstruction.service import ReconstructionService
from geometry_engine.capture.session_layout import prepare_session
from geometry_engine.capture.validation import SessionValidationService
from geometry_engine.reconstruction.preprocessing import preprocess_mesh
from geometry_engine.segmentation.frame_filter import FrameFilterService
from geometry_engine.segmentation.mesh_segmentation import MeshSegmentationService
from geometry_engine.segmentation.projection_alignment import ProjectionAlignmentService
from geometry_engine.segmentation.mask_projection import ProjectionSettings
from geometry_engine.segmentation.service import _rename


def save_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def link_or_copy(source, destination):
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def prepare_input(session, *, output_session=None, reconstruction_dir=None):
    """Flatten multipart metadata and bind the reconstructed mesh, preserving raw files."""
    frames = load_raw_capture(session)
    session = Path(session).resolve()
    reconstruction_dir = Path(reconstruction_dir).resolve() if reconstruction_dir is not None else session / 'reconstruction'
    output_session = Path(output_session).resolve() if output_session is not None else session
    output_session.mkdir(parents=True, exist_ok=True)
    reconstruction = json.loads((reconstruction_dir / 'report.json').read_text(encoding='utf-8'))
    cameras_path = reconstruction_dir / 'cameras.json'
    cameras = {}
    if cameras_path.is_file():
        camera_hash = hashlib.sha256(cameras_path.read_bytes()).hexdigest()
        expected_hash = reconstruction.get('sha256', {}).get('cameras')
        if ((expected_hash is not None and camera_hash != expected_hash)
                or (reconstruction.get('pose_source') == 'refined' and expected_hash is None)):
            raise ValueError('Reconstruction camera fingerprint is missing or changed')
        camera_rows = json.loads(cameras_path.read_text(encoding='utf-8'))['frames']
        cameras = {row['frame_id']: row for row in camera_rows}
        if len(cameras) != len(camera_rows):
            raise ValueError('Duplicate reconstruction camera IDs')
    elif reconstruction.get('pose_source') == 'refined':
        raise ValueError('Refined reconstruction requires its saved cameras')
    integrated = {row['frame_id'] for row in reconstruction['frames'] if row['status'] == 'integrated'}
    frames = [frame for frame in frames if frame.frame_id in integrated]
    if not frames:
        raise ValueError('Reconstruction has no integrated frames')
    destination = output_session / 'input'
    if destination.exists():
        raise FileExistsError('Derived input already exists')
    with TemporaryDirectory(prefix='.pipeline-input-', dir=output_session) as temporary:
        staging = Path(temporary) / 'input'
        staging.mkdir()
        for frame in frames:
            folder = staging / 'frames' / frame.frame_id
            folder.mkdir(parents=True)
            metadata = frame_metadata(frame.metadata_path)
            if cameras_path.is_file():
                if frame.frame_id not in cameras:
                    raise ValueError('Reconstruction camera missing: ' + frame.frame_id)
                if cameras[frame.frame_id].get('status', 'integrated') != 'integrated':
                    raise ValueError('Camera status differs from reconstruction report')
                camera = np.asarray(cameras[frame.frame_id]['T_world_from_camera_cv'], dtype=float)
                if (camera.shape != (4, 4) or not np.isfinite(camera).all()
                        or not np.allclose(camera[3], [0, 0, 0, 1], atol=1e-6)
                        or not np.allclose(camera[:3, :3].T @ camera[:3, :3], np.eye(3), atol=1e-5)
                        or not np.isclose(np.linalg.det(camera[:3, :3]), 1, atol=1e-5)):
                    raise ValueError('Invalid reconstructed camera pose')
                metadata['pose']['T_world_from_camera'] = (camera @ np.diag([1., -1., -1., 1.])).tolist()
            metadata['pose']['T_world_from_camera'][3] = [0, 0, 0, 1]
            for name, source in (('rgb', frame.rgb_path), ('depth', frame.depth_path),
                                 ('confidence', frame.confidence_path)):
                if source is not None:
                    filename = name + source.suffix
                    link_or_copy(source, folder / filename)
                    metadata[name]['path'] = filename
            save_json(folder / 'metadata.json', metadata)
        link_or_copy(reconstruction_dir / 'mesh.ply', staging / 'mesh.ply')
        save_json(staging / 'capture.json', {
            'schema_version': '1.0', 'session_id': frames[0].session_id,
            'coordinate_system': 'arkit', 'length_unit': 'meter', 'matrix_storage': 'rows',
            'frames_directory': 'frames', 'frame_count': len(frames), 'mesh': 'mesh.ply',
        })
        _rename(staging, destination)
    return {'frame_count': len(frames), 'mesh': 'reconstruction/mesh.ply'}


class PipelineService:
    def __init__(self, segmentation, boundary_factory=None):
        self.segmentation = segmentation
        self.boundary_factory = boundary_factory

    def run(self, directory, *, measure_openings=False):
        if measure_openings and self.boundary_factory is None:
            raise ValueError('Opening boundary service is not configured')
        session = prepare_session(directory)
        # Exclusive creation also rejects concurrent requests and accidental reruns.
        report_path = session / 'pipeline.json'
        report = {'status': 'running', 'current_stage': None, 'completed_stages': []}
        with report_path.open('x', encoding='utf-8') as stream:
            json.dump(report, stream)

        def validate():
            result = SessionValidationService().validate(session)
            if result['status'] == 'invalid':
                raise ValueError('Session validation failed: ' + '; '.join(result['errors']))
            return result

        stages = [
            ('reconstruction', lambda: ReconstructionService().reconstruct(session)),
            ('input', lambda: prepare_input(session)),
            ('validation', validate),
            ('mesh_preprocessing', lambda: preprocess_mesh(session / 'input/mesh.ply', session / 'mesh_preprocessing')),
            ('selected_frames', lambda: FrameFilterService().filter(session)),
            ('segmentation', lambda: self.segmentation.segment(session)),
            ('mesh_segmentation', lambda: MeshSegmentationService().segment(session)),
        ]
        if measure_openings:
            # Keep metric reconstruction tied to raw camera poses. Alignment only
            # corrects mask projection against the fixed mesh, never its vertices.
            idx = next(i for i,(name,_) in enumerate(stages) if name == 'mesh_segmentation')
            stages[idx:idx+1] = [
                ('projection_alignment', lambda: ProjectionAlignmentService().align(session)),
                ('mesh_segmentation', lambda: MeshSegmentationService(
                    projection_settings=ProjectionSettings(min_comparable_depth_agreement=.6)).segment(
                        session, alignment_dir=session/'projection_alignment'))]
            report['geometry_policy'] = 'raw_pose_mesh_with_projection_only_alignment'
            stages.append(('mesh_boundaries', lambda: self.boundary_factory().run(session)))
        try:
            occupied = [name for name, _ in stages if (session / name).exists()]
            if occupied or (session / 'mask_projection').exists():
                raise FileExistsError('Stage outputs already exist; use a fresh session or individual stage CLI')
            for name, execute in stages:
                report['current_stage'] = name
                save_json(report_path, report)
                execute()
                report['completed_stages'].append(name)
                save_json(report_path, report)
            report.update(status='completed', current_stage=None)
            save_json(report_path, report)
            return report
        except Exception as error:
            # Do not persist provider messages, URLs or credentials.
            report.update(status='failed', error_type=type(error).__name__)
            save_json(report_path, report)
            raise
