"""Create a comparison-only revision from committed geometry and segmentation.

The source inspection is immutable. A completed revision becomes visible in the
database only after comparison, viewer and all Storage artifacts are verified.
No reconstruction, segmentation or external model is called.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from geometry_engine import inspection
from geometry_engine_api.database.models import InspectionJob, InspectionStage, ViewerExport
from geometry_engine_api.schemas.inspections import InspectionParameters
from .artifacts import ArtifactError, ArtifactRef, fingerprint, json_bytes, safe_relative, sha_file


def restore_saved_stages(artifacts, refs, workspace, *, local_roots=(), progress=None):
    """Use identical existing local files before downloading missing artifacts."""
    workspace = Path(workspace).resolve()
    local_files = {}
    for name in inspection.stage_names():
        if name == 'opening_comparison':
            continue
        saved = refs[name]
        ref = ArtifactRef(saved['key'], saved['sha256'])
        manifest = artifacts.read_json(ref)
        reused = 0
        for entry in manifest['files']:
            relative = safe_relative(entry['path'])
            target = workspace / relative
            if target.is_file():
                continue
            for candidate_root in local_roots:
                root = Path(candidate_root).resolve()
                candidate = (root / relative).resolve()
                if (candidate.is_relative_to(root) and candidate.is_file()
                        and candidate.stat().st_size == entry['size'] and sha_file(candidate) == entry['sha256']):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(candidate, target)
                    local_files[(entry['sha256'], entry['size'])] = target
                    reused += 1
                    break
        if progress:
            progress(f'Restoring {name}; verified local files reused: {reused}')
        artifacts.restore(ref, workspace, local_files=local_files)


class RecomparisonService:
    def __init__(self, engine, artifacts, data_dir):
        self.engine, self.artifacts, self.data = engine, artifacts, Path(data_dir).resolve()

    def run(self, source_job_id, *, progress=None):
        emit = progress or (lambda message: None)
        source_job_id = UUID(str(source_job_id))
        with Session(self.engine) as db:
            source = db.get(InspectionJob, source_job_id)
            if source is None or source.status != 'completed' or not source.result_manifest_sha256:
                raise ValueError('A completed source inspection is required')
            source_values = {key: getattr(source, key) for key in (
                'session_id', 'capture_manifest_path', 'ifc_object_key', 'ifc_sha256', 'pipeline_version')}
            parameters = InspectionParameters.model_validate({**source.parameters,
                'include_boundary_candidates': True}).model_dump(mode='json')
            source_ref = ArtifactRef(source.result_manifest_key, source.result_manifest_sha256)
        saved = self.artifacts.read_json(source_ref)
        if saved.get('job_id') != str(source_job_id) or saved.get('session_id') != source_values['session_id']:
            raise ArtifactError('Source manifest belongs to a different inspection')
        started = datetime.now(timezone.utc)
        job_id, export_id = uuid4(), uuid4()
        session_id = source_values['session_id']
        workspace = self.data / 'recomparisons' / str(job_id) / session_id
        workspace.mkdir(parents=True, exist_ok=False)
        local_roots = [self.data / 'raw' / session_id]
        local_roots.extend(sorted((self.data / 'processed').glob(session_id + '*')))
        restore_saved_stages(self.artifacts, saved['stages'], workspace,
                             local_roots=local_roots, progress=emit)
        emit('Comparing saved contours with IFC; no model inference')
        comparison = inspection.run_stage('opening_comparison', workspace, parameters, progress=emit)
        stage_refs = {name: value for name, value in saved['stages'].items() if name != 'opening_comparison'}
        stage_fingerprint = fingerprint({
            'configuration': inspection.stage_configuration('opening_comparison', parameters),
            'implementation': inspection.implementation_fingerprint(),
            'inputs': {name: stage_refs[name]['sha256'] for name in inspection.stage_dependencies('opening_comparison')},
        })
        prefix = f'{session_id}/results/{job_id}'
        ref, _ = self.artifacts.publish(workspace, ('opening_comparison',),
                                        prefix + '/opening_comparison/attempt_001')
        stage_refs['opening_comparison'] = {'key': ref.key, 'sha256': ref.sha256,
                                            'fingerprint': stage_fingerprint}
        result = {**saved, 'job_id': str(job_id), 'parameters': parameters, 'stages': stage_refs,
                  'derived_from_job_id': str(source_job_id), 'derived_from_manifest_sha256': source_ref.sha256,
                  'recomputed_stages': ['opening_comparison'],
                  'implementation': inspection.implementation_fingerprint()}
        result_ref = self.artifacts.put_json(prefix + '/result-manifests', result)
        emit('Building viewer with dimensions, offsets and quality warnings')
        inspection.build_viewer(workspace)
        viewer_ref, viewer_manifest = self.artifacts.publish(workspace, ('viewer',),
                                    prefix + f'/viewer/{export_id}/attempt_001')
        code = inspection.code_fingerprint()
        export_fingerprint = fingerprint_for_export(result_ref, code)
        finished = datetime.now(timezone.utc)
        request_hash = fingerprint({**{k: source_values[k] for k in (
            'session_id', 'capture_manifest_path', 'ifc_object_key')}, 'parameters': parameters})
        with Session(self.engine) as db, db.begin():
            db.add(InspectionJob(id=job_id, **source_values, parameters=parameters,
                request_hash=request_hash, idempotency_key=f'recomparison-{job_id}',
                status='completed', current_stage=None, result_manifest_key=result_ref.key,
                result_manifest_sha256=result_ref.sha256, created_at=started, updated_at=finished,
                started_at=started, finished_at=finished))
            db.flush()
            for name, stage_ref in stage_refs.items():
                db.add(InspectionStage(id=uuid4(), job_id=job_id, name=name, status='completed',
                    attempt=1, fingerprint=stage_ref['fingerprint'], artifact_manifest_key=stage_ref['key'],
                    artifact_sha256=stage_ref['sha256'], created_at=started,
                    started_at=started if name == 'opening_comparison' else None,
                    finished_at=finished if name == 'opening_comparison' else None))
            db.add(ViewerExport(id=export_id, job_id=job_id, fingerprint=export_fingerprint,
                source_manifest_key=result_ref.key, source_manifest_sha256=result_ref.sha256,
                implementation_sha256=code, viewer_version=inspection.VIEWER_VERSION,
                status='completed', result_manifest_key=viewer_ref.key,
                result_manifest_sha256=viewer_ref.sha256, created_at=started, updated_at=finished,
                started_at=started, finished_at=finished))
            db.flush()
            db.add(InspectionStage(id=uuid4(), viewer_export_id=export_id, name='viewer',
                status='completed', attempt=1, fingerprint=export_fingerprint,
                artifact_manifest_key=viewer_ref.key, artifact_sha256=viewer_ref.sha256,
                created_at=started, started_at=started, finished_at=finished))
        # Seed the API's disposable viewer cache from just-generated files instead
        # of downloading the same viewer back from Storage on its first GET.
        cached = self.data / 'viewer_cache' / str(export_id) / viewer_ref.sha256
        cached.mkdir(parents=True, exist_ok=False)
        for entry in viewer_manifest['files']:
            relative = safe_relative(entry['path'])
            source = workspace / relative
            if sha_file(source) != entry['sha256']:
                raise ArtifactError('Generated viewer changed before local cache publication')
            target = cached / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        (cached / '.stage-manifest.json').write_bytes(json_bytes(viewer_manifest))
        return {'job_id': str(job_id), 'source_job_id': str(source_job_id), 'export_id': str(export_id),
                'viewer_url': f'/inspections/{job_id}/viewer/assets/{export_id}/viewer.html',
                'summary': comparison['report']['summary'], 'workspace': str(workspace)}


def fingerprint_for_export(source_ref, code):
    """Match the normal viewer endpoint's idempotency fingerprint exactly."""
    import hashlib
    canonical = json.dumps({'manifest': source_ref.key, 'sha256': source_ref.sha256,
        'viewer_version': inspection.VIEWER_VERSION, 'implementation': code}, sort_keys=True)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()
