"""Explicit local stages consumed by the durable inspection worker.

The worker owns scheduling, leases, retry and artifact transfer. This adapter
only invokes the existing geometry algorithms in an isolated local workspace.
"""
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
import hashlib
import json
from pathlib import Path
import shutil

from geometry_engine.pipeline import prepare_input
from geometry_engine.capture.session_layout import processed_session
from geometry_engine.capture.validation import SessionValidationService
from geometry_engine.reconstruction.service import ReconstructionService, ReconstructionSettings
from geometry_engine.reconstruction.preprocessing import preprocess_mesh
from geometry_engine.segmentation.frame_filter import FrameFilterService, FrameFilterSettings
from geometry_engine.segmentation.projection_alignment import ProjectionAlignmentService, ProjectionAlignmentSettings
from geometry_engine.segmentation.mesh_segmentation import MeshSegmentationService
from geometry_engine.segmentation.mask_projection import ProjectionSettings
from geometry_engine.segmentation.mask_fusion import FusionSettings
from geometry_engine.segmentation.service import SegmentationSettings
from geometry_engine.openings.mesh_boundaries import MeshBoundaryService, MeshBoundarySettings
from geometry_engine.comparison.scan_vs_bim import ScanVsBimService, ScanVsBimSettings
from geometry_engine.comparison.opening_comparison import OpeningComparisonService, OpeningComparisonSettings
from geometry_engine.visualization.model_comparison_viewer import build_model_comparison


ADAPTER_VERSION = 'durable-mesh-contours-v1'
VIEWER_VERSION = 'interior-contours-rgb-v1'
STAGES = (
    'download', 'reconstruction', 'input', 'validation', 'mesh_preprocessing',
    'selected_frames', 'segmentation', 'projection_alignment', 'mesh_segmentation',
    'mesh_boundaries', 'scan_vs_bim', 'opening_comparison',
)
_DEPENDENCIES = {
    'download': (),
    'reconstruction': ('download',),
    'input': ('download', 'reconstruction'),
    'validation': ('input',),
    'mesh_preprocessing': ('input', 'validation'),
    'selected_frames': ('input', 'validation'),
    'segmentation': ('input', 'selected_frames'),
    'projection_alignment': ('input', 'mesh_preprocessing', 'segmentation'),
    'mesh_segmentation': ('input', 'mesh_preprocessing', 'segmentation', 'projection_alignment'),
    'mesh_boundaries': ('input', 'mesh_preprocessing', 'mesh_segmentation'),
    'scan_vs_bim': ('download', 'input', 'mesh_preprocessing', 'mesh_segmentation'),
    'opening_comparison': ('download', 'input', 'mesh_preprocessing', 'segmentation',
                           'projection_alignment', 'mesh_segmentation', 'mesh_boundaries', 'scan_vs_bim'),
    'viewer': ('download', 'input', 'mesh_preprocessing', 'segmentation',
               'projection_alignment', 'mesh_segmentation', 'mesh_boundaries',
               'scan_vs_bim', 'opening_comparison'),
}


class StageRejected(ValueError):
    """Valid execution produced evidence unsuitable for downstream measurement."""
    retryable = False

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def stage_names():
    return STAGES


def stage_dependencies(name):
    """Direct dependencies; recursively restore them before starting this stage."""
    return _DEPENDENCIES[name]


def stage_outputs(name):
    if name == 'download':
        return ('original', 'reference.ifc', 'download-report.json')
    if name == 'mesh_segmentation':
        # Projection is produced by this operation and must survive retries too.
        return ('mask_projection', 'mesh_segmentation')
    if name not in _DEPENDENCIES:
        raise ValueError('Unknown inspection stage: ' + str(name))
    return (name,)


def _parameters(parameters):
    if parameters is None:
        return {}
    return parameters.model_dump(mode='json') if hasattr(parameters, 'model_dump') else dict(parameters)


def stage_configuration(name, parameters=None, segmentation_service=None):
    """Effective algorithm defaults, suitable for the worker's stage fingerprint."""
    params = _parameters(parameters)
    tolerance = float(params.get('tolerance_m', .1))
    defaults = {
        'download': {'format': 'verified-capture-manifest', 'schema_version': 1},
        'reconstruction': asdict(ReconstructionSettings()),
        'input': {'pose_source': 'raw_arkit', 'length_unit': 'meter'},
        'validation': {'schema': 'capture-v1'},
        'mesh_preprocessing': {'unit': 'meter', 'coordinate_transform': 'identity'},
        'selected_frames': asdict(FrameFilterSettings()),
        'segmentation': asdict(segmentation_service.settings if segmentation_service else SegmentationSettings()),
        'projection_alignment': asdict(ProjectionAlignmentSettings()),
        'mesh_segmentation': {'projection': asdict(ProjectionSettings(min_comparable_depth_agreement=.6)),
                              'fusion': asdict(FusionSettings())},
        'mesh_boundaries': asdict(MeshBoundarySettings()),
        'scan_vs_bim': {**asdict(ScanVsBimSettings(tolerance_m=tolerance,
                            max_match_distance_m=max(.3, tolerance * 2))),
                       'segmented_only': True, 'interior_point': params.get('interior_point_ifc_m')},
        'opening_comparison': {**asdict(OpeningComparisonSettings(tolerance_m=tolerance)),
                               'include_boundary_candidates': params.get('include_boundary_candidates', True)},
        'viewer': {'version': VIEWER_VERSION, 'raw_panel': True},
    }
    if name not in defaults:
        raise ValueError('Unknown inspection stage: ' + str(name))
    result = {'adapter_version': ADAPTER_VERSION, 'settings': defaults[name]}
    if name == 'segmentation' and segmentation_service is not None:
        backend = segmentation_service.backend
        result['model'] = getattr(backend, 'model_info', {'name': getattr(backend, 'model', None)})
    return result


def code_fingerprint():
    """Code/template identity shared by API and worker with different extras."""
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted([*root.rglob('*.py'), *root.rglob('*.html')]):
        digest.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0')
        digest.update(path.read_bytes())
    return digest.hexdigest()


def implementation_fingerprint():
    """Worker identity: code plus the numerical/model runtime dependencies."""
    digest = hashlib.sha256(bytes.fromhex(code_fingerprint()))
    for package in ('numpy', 'scipy', 'Pillow', 'open3d', 'ifcopenshell', 'meta-sam-parser'):
        try:
            value = version(package)
        except PackageNotFoundError:
            value = 'not-installed'
        digest.update((package + '=' + value + '\n').encode('utf-8'))
    return digest.hexdigest()


def _workspace(directory):
    session = Path(directory).resolve()
    if processed_session(session) != session or session.parent.name in ('raw', 'processed'):
        raise ValueError('Use an isolated worker workspace, outside raw/ and processed/ session roots')
    if not session.is_dir():
        raise ValueError('Inspection workspace does not exist')
    return session


def run_stage(name, workspace, parameters=None, segmentation_service=None, progress=None):
    """Run one stage; outputs must not exist and dependencies must be restored."""
    session = _workspace(workspace)
    if name not in STAGES or name == 'download':
        raise ValueError('The local adapter cannot execute stage: ' + str(name))
    settings = stage_configuration(name, parameters, segmentation_service)['settings']
    for output in stage_outputs(name):
        if (session / output).exists():
            raise FileExistsError('Stage output already exists: ' + output)

    def emit(*items):
        if progress:
            progress(' '.join(str(item) for item in items))

    emit('Starting ' + name)
    if name == 'reconstruction':
        result = ReconstructionService().reconstruct(session, progress=emit)
    elif name == 'input':
        result = prepare_input(session)
    elif name == 'validation':
        result = SessionValidationService().validate(session)
        if result.get('status') == 'invalid':
            raise StageRejected('invalid_capture', 'Derived capture validation failed; see validation/report.json')
    elif name == 'mesh_preprocessing':
        result = preprocess_mesh(session/'input/mesh.ply', session/'mesh_preprocessing')
    elif name == 'selected_frames':
        result = FrameFilterService().filter(session)
    elif name == 'segmentation':
        if segmentation_service is None:
            raise ValueError('Segmentation service is not configured')
        result = segmentation_service.segment(session, progress=emit)
    elif name == 'projection_alignment':
        result = ProjectionAlignmentService().align(session, progress=emit)
    elif name == 'mesh_segmentation':
        result = MeshSegmentationService(projection_settings=ProjectionSettings(
            min_comparable_depth_agreement=.6)).segment(session, alignment_dir=session/'projection_alignment', progress=emit)
    elif name == 'mesh_boundaries':
        result = MeshBoundaryService().run(session, progress=emit)
    elif name == 'scan_vs_bim':
        options = {key: value for key, value in settings.items() if key not in ('segmented_only', 'interior_point')}
        result = ScanVsBimService(ScanVsBimSettings(**options)).compare(
            session, session/'reference.ifc', segmentation_dir=session/'mesh_segmentation',
            segmented_only=True, interior_point=settings['interior_point'], progress=emit)
        if result.get('status') != 'completed' or not result.get('registration', {}).get('accepted'):
            raise StageRejected('registration_rejected', 'Global scan-to-IFC alignment was not accepted')
    elif name == 'opening_comparison':
        options = {key: value for key, value in settings.items() if key != 'include_boundary_candidates'}
        result = OpeningComparisonService(OpeningComparisonSettings(**options)).compare(
            session, session/'reference.ifc', segmentation_dir=session/'mesh_segmentation',
            comparison_dir=session/'scan_vs_bim', boundaries_dir=session/'mesh_boundaries',
            include_boundary_candidates=settings['include_boundary_candidates'], progress=emit)
    else:
        raise AssertionError(name)
    for output in stage_outputs(name):
        if not (session / output).is_dir():
            raise RuntimeError('Stage did not produce its declared output: ' + output)
    emit('Completed ' + name)
    return {'stage': name, 'status': 'completed', 'outputs': list(stage_outputs(name)), 'report': result}


def build_viewer(workspace, output_name='viewer'):
    """Export saved measurements and geometry, including a portable photo review."""
    session = _workspace(workspace)
    if output_name != 'viewer':
        raise ValueError('The durable viewer output is viewer/')
    comparison = session/'opening_comparison'
    report = json.loads((comparison/'report.json').read_text(encoding='utf-8'))
    if report.get('boundary_stage') != 'mesh_boundaries':
        raise ValueError('Viewer requires the current portable semantic mesh-contour comparison')
    # The current contour exporter preserves saved numbers. Legacy non-contour
    # exports adjusted display measurements and are intentionally not used here.
    html = build_model_comparison(session, session/'reference.ifc', session/'viewer',
                                 comparison_dir=session/'scan_vs_bim', opening_dir=comparison, raw_panel=True)
    review = session/'viewer/review'
    shutil.copytree(comparison, review)
    text = html.read_text(encoding='utf-8')
    html.write_text(text.replace('../opening_comparison/review.html', 'review/review.html'), encoding='utf-8')
    findings = json.loads((comparison/'findings.json').read_text(encoding='utf-8'))['findings']
    exported = json.loads((session/'viewer/report.json').read_text(encoding='utf-8'))['findings']
    source = {row['finding_id']: row for row in findings}
    for row in exported:
        original = source[row['finding_id']]
        if row.get('measurements') != original.get('measurements') or row.get('deltas_m') != original.get('deltas_m'):
            raise RuntimeError('Viewer export changed saved measurements')
    return html
