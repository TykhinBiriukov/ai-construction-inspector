"""Agent-facing reports and expiring, job-bound links to checked artifacts."""
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import shutil
import threading
import time
from urllib.parse import quote, urlencode

from geometry_engine_api.schemas.inspection_results import InspectionResultResponse
from .artifacts import ArtifactError, ArtifactRef, json_bytes, safe_relative, sha_file


MAX_LINK_LIFETIME = 86400
_FILE_LOCK = threading.Lock()


def compact_object(finding, reference, photos):
    """Keep measured facts and uncertainty, without workflow or algorithm internals."""
    measurements = finding.get('measurements')
    quality = finding.get('quality') or {}
    support = quality.get('side_support') or {}
    issues = list(quality.get('reasons', []))
    labels = {'ifc_boundary_definition_requires_review': 'boundary_correspondence_unverified',
              'other_instances_near_same_reference_may_be_fragments': 'possible_duplicate_fragments'}
    for reason in finding.get('review_reasons', []):
        if reason in labels and labels[reason] not in issues:
            issues.append(labels[reason])
    status = finding.get('status')
    match_status = ('ifc_reference_unavailable' if status == 'reference_unavailable' else
                    'ifc_object_not_observed' if status == 'unobserved_reference' else
                    'ambiguous' if status == 'ambiguous_match' else
                    'matched' if finding.get('ifc_guid') else 'scan_object_not_matched')
    deltas = (measurements.get('deltas_m') or finding.get('deltas_m') or {}) if measurements else {}
    dimensions = {}
    for dimension, axis in (('width', 0), ('height', 1)):
        expected = measurements.get('ifc_' + dimension + '_m') if measurements else None
        bounds = reference.get('bounds_local_m') if reference else None
        if expected is None and bounds is not None:
            expected = float(bounds[1][axis] - bounds[0][axis])
        dimensions[dimension] = {
            'ifc': expected,
            'scan': measurements.get('observed_' + dimension + '_m') if measurements else None,
            'difference': deltas.get(dimension),
        }
    identity = finding.get('object_id')
    if identity is None and finding.get('instance_id') is not None:
        identity = str(finding['instance_id'])
    return {'id': finding['finding_id'], 'type': finding['category'], 'scan_object_id': identity,
            'ifc_guid': finding.get('ifc_guid'), 'match_status': match_status,
            'dimensions': dimensions,
            'displacement': {'along_wall': deltas.get('horizontal_center'),
                             'vertical': deltas.get('vertical_center'),
                             'normal_to_wall': deltas.get('normal_center')},
            'quality': {'boundary_supported_on_all_sides':
                        all(support.get(side, {}).get('observed') is True
                            for side in ('left', 'right', 'bottom', 'top')) if support else None,
                        'absolute_accuracy_verified': quality.get('absolute_accuracy_verified'),
                        'issues': issues},
            'photos': photos}


class InvalidResultLink(ValueError):
    pass


class ResultLinkSigner:
    def __init__(self, secret, *, clock=time.time):
        if not isinstance(secret, str) or len(secret) < 16:
            raise ValueError('A server signing secret of at least 16 characters is required')
        self.key = hmac.new(secret.encode('utf-8'), b'geometry-result-links-v1', hashlib.sha256).digest()
        self.clock = clock

    def signature(self, job, stage, path, expires):
        message = json_bytes(['result-file-v1', str(job.id), job.result_manifest_sha256,
                              stage, path, int(expires)])
        return hmac.new(self.key, message, hashlib.sha256).hexdigest()

    def verify(self, job, stage, path, expires, signature):
        if (expires <= self.clock() or expires > self.clock() + MAX_LINK_LIFETIME
                or not isinstance(signature, str)
                or not hmac.compare_digest(signature, self.signature(job, stage, path, expires))):
            raise InvalidResultLink('Artifact link is expired or invalid; request fresh result links')


class InspectionResults:
    def __init__(self, artifacts, data_dir, signer):
        self.artifacts, self.data, self.signer = artifacts, Path(data_dir).resolve(), signer

    def _manifest(self, job):
        manifest = self.artifacts.read_json(ArtifactRef(job.result_manifest_key, job.result_manifest_sha256))
        if (manifest.get('job_id') != str(job.id) or manifest.get('session_id') != job.session_id
                or not isinstance(manifest.get('stages'), dict)):
            raise ArtifactError('Result manifest belongs to a different inspection')
        return manifest

    def _index(self, manifest, stage, indexes):
        if stage not in indexes:
            safe_relative(stage)
            if '/' in stage or stage not in manifest['stages']:
                raise FileNotFoundError('Result stage not found')
            saved = manifest['stages'][stage]
            if not isinstance(saved, dict):
                raise ArtifactError('Invalid result stage reference')
            ref = ArtifactRef(saved['key'], saved['sha256'])
            stage_manifest = self.artifacts.read_json(ref)
            if stage_manifest.get('schema_version') != 1 or not isinstance(stage_manifest.get('files'), list):
                raise ArtifactError('Invalid result stage manifest')
            files = {}
            for entry in stage_manifest['files']:
                if not isinstance(entry, dict):
                    raise ArtifactError('Invalid result file entry')
                path = safe_relative(entry['path'])
                if path.casefold() in files:
                    raise ArtifactError('Duplicate result artifact')
                files[path.casefold()] = entry
            indexes[stage] = (ref, files)
        return indexes[stage]

    def _find(self, manifest, path, indexes):
        safe_relative(path)
        prefix = path.split('/', 1)[0]
        stage = ('download' if prefix in ('original', 'reference.ifc') else
                 'mesh_segmentation' if prefix == 'mask_projection' else prefix)
        ref, files = self._index(manifest, stage, indexes)
        entry = files.get(path.casefold())
        if entry is None or entry['path'] != path:
            raise FileNotFoundError('Result artifact not found')
        return stage, ref, entry

    def _cache_file(self, job, ref, entry):
        relative = safe_relative(entry['path'])
        root = self.data / 'result_cache' / str(job.id) / job.result_manifest_sha256
        root.mkdir(parents=True, exist_ok=True)
        target = root / relative
        if not target.resolve().is_relative_to(root):
            raise ArtifactError('Result file leaves cache root')
        with _FILE_LOCK:
            if target.is_file() and target.stat().st_size == entry['size'] and sha_file(target) == entry['sha256']:
                return target
            # Comparison-only revisions already have verified local artifacts.
            local_roots = [self.data / 'recomparisons' / str(job.id) / job.session_id,
                           self.data / 'processed' / job.session_id,
                           self.data / 'raw' / job.session_id]
            for local in local_roots:
                source = (local / relative).resolve()
                if (source.is_relative_to(local.resolve()) and source.is_file()
                        and source.stat().st_size == entry['size'] and sha_file(source) == entry['sha256']):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    if target.stat().st_size != entry['size'] or sha_file(target) != entry['sha256']:
                        raise ArtifactError('Local result copy failed its integrity check')
                    return target
            self.artifacts.restore(ref, root, select_paths={relative})
            if not target.is_file():
                raise ArtifactError('Committed result file is missing')
            return target

    def file(self, job, stage, path):
        manifest, indexes = self._manifest(job), {}
        ref, files = self._index(manifest, stage, indexes)
        entry = files.get(path.casefold())
        if entry is None or safe_relative(path) != entry['path']:
            raise FileNotFoundError('Result artifact not found')
        return self._cache_file(job, ref, entry)

    def get(self, job, base_url, *, expires_in=3600):
        if not 60 <= expires_in <= MAX_LINK_LIFETIME:
            raise ValueError('Result link lifetime must be between 60 and 86400 seconds')
        try:
            result = self._build_result(job, base_url, expires_in)
            # Validate before crossing the adapter boundary: malformed stored
            # fields must be integrity failures, not FastAPI serialization errors.
            InspectionResultResponse.model_validate(result)
            return result
        except ArtifactError:
            raise
        except (KeyError, TypeError, AttributeError, IndexError, ValueError, OverflowError):
            raise ArtifactError('Invalid committed report structure') from None

    def _build_result(self, job, base_url, expires_in):
        manifest, indexes = self._manifest(job), {}
        expires = int(self.signer.clock()) + expires_in
        base_url = base_url.rstrip('/')

        def link(path):
            stage, _, _ = self._find(manifest, path, indexes)
            query = urlencode({'expires': expires,
                                'signature': self.signer.signature(job, stage, path, expires)})
            url = (f'{base_url}/inspections/{job.id}/result/files/{quote(stage, safe="")}/'
                   f'{quote(path, safe="/")}?{query}')
            return url

        def document(path, expected_type=dict):
            _, ref, entry = self._find(manifest, path, indexes)
            if entry['size'] > 32 * 1024**2:
                raise ArtifactError('Agent report JSON exceeds the size limit')
            try:
                result = json.loads(self._cache_file(job, ref, entry).read_bytes())
            except (ValueError, UnicodeError):
                raise ArtifactError('Invalid committed report JSON') from None
            if not isinstance(result, expected_type):
                raise ArtifactError('Invalid committed report structure')
            return result

        report = document('opening_comparison/report.json')
        findings = document('opening_comparison/findings.json')['findings']
        references = document('opening_comparison/reference_objects.json')['objects']
        evidence_index = document('opening_comparison/evidence_index.json', list)
        surface = document('scan_vs_bim/report.json')
        for rows in (findings, references, evidence_index, surface.get('elements', [])):
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ArtifactError('Invalid committed report entries')
        overlays = {(row['finding_id'], row['frame_id']): row for row in evidence_index}
        reference_by_guid = {row['guid']: row for row in references}
        objects = []
        for finding in findings:
            photos = []
            for evidence in finding.get('evidence', []):
                overlay = overlays.get((finding['finding_id'], evidence['frame_id']), {})
                annotated = overlay.get('mask_thumbnail') or overlay.get('rgb_thumbnail')
                photos.append({'frame_id': evidence['frame_id'],
                    'rgb_url': link(evidence['rgb']) if evidence.get('rgb') else None,
                    'mask_url': link(evidence['mask']) if evidence.get('mask') else None,
                    'overlay_url': link('opening_comparison/' + safe_relative(annotated)) if annotated else None})
            objects.append(compact_object(finding, reference_by_guid.get(finding.get('ifc_guid')), photos))
        paths = {'point_cloud': 'reconstruction/point_cloud.ply', 'mesh': 'reconstruction/mesh.ply',
                 'segmented_mesh': 'mesh_segmentation/mesh.ply', 'ifc': 'reference.ifc'}
        surfaces = []
        for element in surface.get('elements', []):
            if not element.get('compare'):
                continue
            statistics = element.get('statistics') or {}
            surfaces.append({'ifc_guid': element['guid'], 'type': element['category'],
                'status': element['status'], 'compared_area_m2': element.get('observed_scan_area_m2'),
                'p95_absolute_deviation': statistics.get('p95_absolute_m'),
                'area_fraction_outside_tolerance': statistics.get('area_fraction_outside_tolerance'),
                'whole_element_verified': element.get('whole_element_verified', False)})
        return {'schema_version': '2.0', 'job_id': str(job.id), 'session_id': job.session_id,
                'units': 'meter', 'difference_convention': 'scan_minus_ifc',
                'tolerance': report.get('parameters', {}).get('tolerance_m', job.parameters.get('tolerance_m', .1)),
                'links_expires_at': datetime.fromtimestamp(expires, timezone.utc),
                'summary': {'objects': len(objects),
                    'matched': sum(obj['match_status'] == 'matched' for obj in objects),
                    'unmatched_scan': sum(obj['match_status'] == 'scan_object_not_matched' for obj in objects),
                    'unobserved_ifc': sum(obj['match_status'] == 'ifc_object_not_observed' for obj in objects),
                    'unavailable_ifc': sum(obj['match_status'] == 'ifc_reference_unavailable' for obj in objects),
                    'ambiguous': sum(obj['match_status'] == 'ambiguous' for obj in objects)},
                'objects': objects, 'surfaces': surfaces,
                'artifacts': {name: link(path) for name, path in paths.items()}}
