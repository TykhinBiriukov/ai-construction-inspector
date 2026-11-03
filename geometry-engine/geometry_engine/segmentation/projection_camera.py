"""Resolve the camera actually used by semantic projection, without pose chaining."""
import json
from pathlib import Path

import numpy as np

from geometry_engine.segmentation.mask_projection import camera_parameters, file_hash
from geometry_engine.capture.validation import session_file


class ProjectionCameras:
    def __init__(self, session, mesh_segmentation_dir=None):
        self.session = Path(session).resolve()
        semantic = Path(mesh_segmentation_dir).resolve() if mesh_segmentation_dir else self.session/'mesh_segmentation'
        self.fingerprints = {}
        self.rows = None
        self.projection = None
        semantic_report = semantic/'report.json'
        linked = json.loads(semantic_report.read_text(encoding='utf-8')) if semantic_report.exists() else {}
        projection = (self.session/linked['projection']).resolve() if linked.get('projection') else self.session/'mask_projection'
        if not projection.is_relative_to(self.session):
            raise ValueError('Projection camera evidence outside session')
        report_path = projection/'report.json'
        if not report_path.exists():
            if linked.get('projection'):
                raise ValueError('Linked projection camera evidence missing')
            return
        report = json.loads(report_path.read_text(encoding='utf-8'))
        self.projection = projection
        for name, expected in linked.get('projection_fingerprints', {}).items():
            if file_hash(session_file(projection, projection, name)) != expected:
                raise ValueError('Projection camera evidence changed since segmentation')
        # Old unadjusted stages retain their metadata convention.
        if not report.get('projection_alignment'):
            observations = projection/'observations.json'
            if observations.exists():
                rows = json.loads(observations.read_text(encoding='utf-8')).get('frames', [])
                if any(row.get('camera_status', 'original') != 'original' for row in rows):
                    raise ValueError('Adjusted projection is missing its camera provenance')
            return
        observations_path = projection/'observations.json'
        expected = report.get('artifact_fingerprints', {}).get('observations.json')
        if not expected or file_hash(observations_path) != expected:
            raise ValueError('Projection camera observations changed')
        from geometry_engine.segmentation.projection_alignment import load_projection_cameras
        mesh_path = session_file(self.session, self.session, report['mesh'])
        alignment = (self.session/report['projection_alignment']).resolve()
        cameras = load_projection_cameras(alignment, self.session, mesh_path)
        self.rows = {}
        for row in json.loads(observations_path.read_text(encoding='utf-8'))['frames']:
            fid = row['frame_id']
            if fid in self.rows or fid not in cameras:
                raise ValueError('Projection camera frame source differs')
            status = row.get('camera_status')
            if status != cameras[fid]['status']:
                raise ValueError('Projection camera status differs')
            if status != 'rejected':
                if not np.array_equal(row.get('T_world_from_camera_cv'), cameras[fid]['T_world_from_camera_cv']):
                    raise ValueError('Projection camera transform differs')
            self.rows[fid] = cameras[fid]
        for path in [report_path, observations_path, alignment/'report.json', alignment/'cameras.json']:
            self.fingerprints[str(path)] = file_hash(path)
        if semantic_report.exists():
            self.fingerprints[str(semantic_report)] = file_hash(semantic_report)
        for ref, digest in report.get('input_fingerprints', {}).items():
            path = session_file(self.session, self.session, ref)
            if file_hash(path) != digest:
                raise ValueError('Projection camera input changed')
            self.fingerprints[str(path)] = digest

    def parameters(self, metadata, coordinates, frame_id=None):
        K, original = camera_parameters(metadata, coordinates)
        if self.rows is None:
            return K, original
        row = self.rows.get(frame_id or metadata.get('frame_id'))
        if row is None or row['status'] == 'rejected':
            raise ValueError('Frame has no accepted projection camera; cannot measure or draw its mesh contour')
        # Stored transform is absolute camera-CV -> world, NOT a correction delta.
        return K, np.asarray(row['T_world_from_camera_cv'], dtype=float).copy()
