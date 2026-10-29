"""Sequential raw-capture pipeline. Each stage publishes its own artifacts."""

import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from .raw_capture import load_raw_capture
from .reconstruction import ReconstructionService
from .session_layout import prepare_session
from .validation import SessionValidationService
from .preprocessing import preprocess_mesh
from .frame_filter import FrameFilterService
from .mesh_segmentation import MeshSegmentationService
from .wall_planes import WallPlaneService


def save_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def link_or_copy(source, destination):
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def prepare_input(session):
    """Flatten multipart metadata and bind the reconstructed mesh, preserving raw files."""
    frames = load_raw_capture(session)
    reconstruction = json.loads((session / 'reconstruction/report.json').read_text(encoding='utf-8'))
    integrated = {row['frame_id'] for row in reconstruction['frames'] if row['status'] == 'integrated'}
    frames = [frame for frame in frames if frame.frame_id in integrated]
    if not frames:
        raise ValueError('Reconstruction has no integrated frames')
    destination = session / 'input'
    if destination.exists():
        raise FileExistsError('Derived input already exists')
    with TemporaryDirectory(prefix='.pipeline-input-', dir=session) as temporary:
        staging = Path(temporary) / 'input'
        staging.mkdir()
        for frame in frames:
            folder = staging / 'frames' / frame.frame_id
            folder.mkdir(parents=True)
            metadata = json.loads(frame.metadata_path.read_text(encoding='utf-8'))
            metadata['pose']['T_world_from_camera'][3] = [0, 0, 0, 1]
            for name, source in (('rgb', frame.rgb_path), ('depth', frame.depth_path),
                                 ('confidence', frame.confidence_path)):
                if source is not None:
                    filename = name + source.suffix
                    link_or_copy(source, folder / filename)
                    metadata[name]['path'] = filename
            save_json(folder / 'metadata.json', metadata)
        link_or_copy(session / 'reconstruction' / 'mesh.ply', staging / 'mesh.ply')
        save_json(staging / 'capture.json', {
            'schema_version': '1.0', 'session_id': frames[0].session_id,
            'coordinate_system': 'arkit', 'length_unit': 'meter', 'matrix_storage': 'rows',
            'frames_directory': 'frames', 'frame_count': len(frames), 'mesh': 'mesh.ply',
        })
        staging.rename(destination)
    return {'frame_count': len(frames), 'mesh': 'reconstruction/mesh.ply'}


class PipelineService:
    def __init__(self, segmentation):
        self.segmentation = segmentation

    def run(self, directory):
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
            ('wall_planes', lambda: WallPlaneService().detect(session)),
        ]
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
