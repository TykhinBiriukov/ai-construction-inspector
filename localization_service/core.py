"""Localization domain boundary; stdlib only. No fabricated geometric solution."""
from __future__ import annotations
import math
from copy import deepcopy
from uuid import UUID, uuid4


def uid(value):
    return str(UUID(value))


def rigid(matrix):
    if not isinstance(matrix, list) or len(matrix) != 4 or any(not isinstance(r, list) or len(r) != 4 for r in matrix):
        raise ValueError('expected 4x4 transform')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for r in matrix for v in r):
        raise ValueError('nonfinite transform')
    if any(abs(matrix[3][i] - [0, 0, 0, 1][i]) > 1e-5 for i in range(4)):
        raise ValueError('invalid homogeneous row')
    r = matrix
    for i in range(3):
        for j in range(3):
            if abs(sum(r[k][i]*r[k][j] for k in range(3)) - (i == j)) > 1e-4:
                raise ValueError('nonrigid rotation')
    det = sum(r[0][i] * (r[1][(i+1)%3]*r[2][(i+2)%3] - r[1][(i+2)%3]*r[2][(i+1)%3]) for i in range(3))
    if abs(det - 1) > 1e-4:
        raise ValueError('reflection not allowed')
    return matrix


def compose(a, b):
    rigid(a); rigid(b)
    return [[sum(a[i][k]*b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


class Localization:
    """One session. Inspector adapter owns durable persistence and authenticated scope.

    Solver is trusted server-side code, never an arbitrary phone-supplied transform.
    solve(context, verified_observations) returns a checked result or more-evidence request.
    """
    def __init__(self, session_id, context, solver=None):
        self.session_id = uid(session_id)
        self.context = deepcopy(context)
        self.solver = solver
        self.revision = 0
        self.control_revision = 0
        self.retired_segments = []
        self.segment = None
        self.alignment = None
        self.instruction = None
        self.phase = 'awaiting_camera'
        self.sequence = -1
        self.pose = None
        self.last_seen = None
        self.evidence = {}
        self.batches = {}
        self.completed = False
        self.last_result = None
        self.tracking_limited_since = None
        self.tracking_events = []
        self.tracking_state = None

    def _guide(self, phase, text=None):
        self.phase = phase
        self.revision += 1
        self.control_revision += 1
        self.instruction = None if text is None else {
            'instruction_id': str(uuid4()), 'session_id': self.session_id,
            'segment_id': self.segment, 'source': 'localization',
            'control_revision': self.control_revision, 'text': text, 'status': 'active'}

    def start(self, segment_id):
        if self.completed:
            raise ValueError('session finished')
        segment_id = uid(segment_id)
        if segment_id == self.segment:
            return self.snapshot()
        if segment_id in self.retired_segments:
            raise ValueError("retired segment cannot become current")
        recovering = self.segment is not None
        if self.segment is not None:
            self.retired_segments.append(self.segment)
        self.segment = segment_id
        self.tracking_limited_since = None
        self.tracking_state = None
        self.alignment = None
        self.last_result = None
        self.evidence = {}; self.pose = None; self.sequence = -1; self.last_seen = None
        if self.context is None:
            self._guide('inspecting')
        elif not all(self.context.get(k) for k in ('room_id', 'model_version', 'entry_element_id', 'bim_frame_id')):
            self._guide('blocked', 'BIM не подготовлена для локализации.')
        else:
            self._guide('relocalizing' if recovering else 'localizing',
                        'Наведите камеру на входную дверь. Покажите проём, пол и соседний угол.')
        return self.snapshot()

    def observe(self, batch_id, segment_id, observations):
        batch_id = uid(batch_id)
        payload = {'segment_id': uid(segment_id), 'observations': deepcopy(observations)}
        if self.completed or payload['segment_id'] != self.segment:
            raise ValueError('closed session or old segment')
        if batch_id in self.batches:
            if self.batches[batch_id] != payload:
                raise ValueError('batch ID conflict')
            return self.snapshot()
        if not observations:
            raise ValueError('empty evidence')
        if self.context is None:
            raise ValueError('no BIM localization requested')
        # Inspector adapter must verify hashes/bytes and create these references first.
        for observation in observations:
            oid = uid(observation['observation_id'])
            if observation.get('source_verified') is not True or not observation.get('files'):
                raise ValueError('unverified source')
            if oid in self.evidence and self.evidence[oid] != observation:
                raise ValueError('observation ID conflict')
        self.evidence.update({uid(o['observation_id']): deepcopy(o) for o in observations})
        # Initialization is not a continuous validator. Once registered, ARKit
        # supplies movement within this segment; inspection views only add coverage.
        # Explicit tracking loss/start() invalidates alignment independently.
        if self.alignment is not None:
            self.batches[batch_id] = payload
            self.revision += 1
            return self.snapshot()
        if self.solver is None:
            # Honest readiness: API/workflow can be exercised, but alignment is not implemented.
            self._guide('blocked', 'Алгоритм привязки ещё не подключён. Наблюдения сохранены для теста.')
        else:
            result = self.solver.solve(deepcopy(self.context), list(deepcopy(self.evidence).values()))
            self.last_result = deepcopy(result)
            if result['status'] == 'localized':
                transform = rigid(result['T_bim_arkit'])
                ids = [uid(v) for v in result['evidence_observation_ids']]
                if not ids or any(v not in self.evidence for v in ids):
                    raise ValueError('alignment lacks evidence')
                if result.get('analysis_origin') not in ('live', 'recorded', 'synthetic'):
                    raise ValueError('missing analysis provenance')
                self.alignment = dict(result, T_bim_arkit=transform, segment_id=self.segment,
                                      alignment_id=str(uuid4()), revision=self.revision+1)
                self._guide('inspecting')
            elif result['status'] == 'needs_more_evidence':
                self.alignment = None; self.pose = None
                self._guide('relocalizing' if self.phase == 'relocalizing' else 'localizing', result['instruction'])
            else:
                self.alignment = None; self.pose = None
                self._guide('blocked', result.get('message', 'Не удалось подтвердить привязку.'))
        self.batches[batch_id] = payload
        return self.snapshot()

    def telemetry(self, payload, now):
        if self.completed or uid(payload['session_id']) != self.session_id or uid(payload['segment_id']) != self.segment:
            raise ValueError('wrong telemetry scope')
        sequence = payload['sequence']
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= self.sequence:
            raise ValueError('old telemetry')
        if payload['tracking_state'] not in ('normal', 'limited', 'not_available'):
            raise ValueError('invalid tracking state')
        transform = rigid(payload['T_arkit_camera'])
        self.sequence = sequence; self.last_seen = now
        tracking = payload['tracking_state']
        if tracking != self.tracking_state:
            self.tracking_events.append({'at': now, 'state': tracking, 'sequence': sequence,
                                         'event': 'tracking_changed'})
            self.tracking_events = self.tracking_events[-100:]
            self.tracking_state = tracking
        if tracking != 'normal':
            self.pose = None
            if self.tracking_limited_since is None:
                self.tracking_limited_since = now
            # Limited means uncertain pose, not a new world coordinate system.
            # Keep registration/evidence for this segment regardless of duration;
            # publish no pose until ARKit recovers. start() handles world resets.
            lost = tracking == 'not_available'
            if self.alignment is not None and lost:
                self.tracking_events.append({'at': now, 'state': tracking, 'sequence': sequence,
                                             'event': 'alignment_invalidated'})
                self.tracking_events = self.tracking_events[-100:]
                self.alignment = None
                self.last_result = None
                self.evidence = {}  # Recovery needs fresh observations, not the original views.
                self._guide('relocalizing', 'Положение потеряно. Снова покажите дверь и соседний угол.')
        else:
            self.tracking_limited_since = None
        if tracking == 'normal' and self.alignment is not None:
            self.pose = {'sequence': sequence, 'T_bim_camera': compose(self.alignment['T_bim_arkit'], transform),
                         'received_at_s': now, 'analysis_origin': self.alignment['analysis_origin']}
        return deepcopy(self.pose)

    def finish(self):
        if self.completed:
            return
        self.completed = True
        self.pose = None
        self._guide('finished')

    def snapshot(self, now=None):
        owner = 'none' if self.phase == 'finished' else ('inspector' if self.phase in ('inspecting', 'awaiting_camera') else 'localization')
        pose = self.pose if now is not None and self.last_seen is not None and 0 <= now-self.last_seen < 2 else None
        return deepcopy({'protocol':'live_bim_v1', 'session_id':self.session_id, 'revision':self.revision,
            'context':self.context, 'alignments':[] if self.alignment is None else [self.alignment],
            'guidance':{'phase':self.phase, 'owner':owner, 'control_revision':self.control_revision,
                        'active_instruction_id':None if self.instruction is None else self.instruction['instruction_id'],
                        'paused_inspector_instruction_id':None},
            'instructions':[] if self.instruction is None else [self.instruction],
            'localization':self.last_result, 'coverage_ref':None, 'latest_pose':pose})
