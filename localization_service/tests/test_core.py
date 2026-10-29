import unittest
from uuid import uuid4
from localization_service.core import Localization, rigid

IDENTITY = [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
class FixtureSolver:
    def solve(self, context, observations):
        return {'status':'localized', 'T_bim_arkit':[[1,0,0,10],[0,1,0,0],[0,0,1,0],[0,0,0,1]],
                'analysis_origin':'synthetic', 'evidence_observation_ids':[o['observation_id'] for o in observations]}

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.sid, self.seg = str(uuid4()), str(uuid4())
        self.context = {'model_version':'v1','room_id':'room','entry_element_id':'door','bim_frame_id':'local-m'}
        self.evidence = [{'observation_id':str(uuid4()), 'source_verified':True, 'files':[{'sha256':'fixture'}]}]
    def localized(self):
        s = Localization(self.sid, self.context, FixtureSolver()); s.start(self.seg)
        s.observe(str(uuid4()), self.seg, self.evidence)
        return s
    def test_no_solver_does_not_invent_success(self):
        s = Localization(self.sid, self.context); s.start(self.seg)
        result = s.observe(str(uuid4()), self.seg, self.evidence)
        self.assertEqual(result['guidance']['phase'], 'blocked')
        self.assertEqual(result['alignments'], [])
    def test_missing_bim_skips_invalid_bim_blocks(self):
        self.assertEqual(Localization(self.sid,None).start(self.seg)['guidance']['owner'],'inspector')
        self.assertEqual(Localization(self.sid,{}).start(self.seg)['guidance']['phase'],'blocked')
    def test_pose_direction_loss_and_staleness(self):
        s = self.localized()
        p = {'session_id':self.sid,'segment_id':self.seg,'sequence':1,'tracking_state':'normal','T_arkit_camera':IDENTITY}
        self.assertEqual(s.telemetry(p,100)['T_bim_camera'][0][3],10)
        self.assertIsNone(s.snapshot(103)['latest_pose'])
        with self.assertRaises(ValueError): s.telemetry(p,101)
        p.update(sequence=2, tracking_state='not_available'); s.telemetry(p,102)
        self.assertEqual(s.snapshot()['guidance']['phase'],'relocalizing')
        p.update(sequence=3, tracking_state='normal')
        self.assertIsNone(s.telemetry(p,103)) # normal tracking alone cannot restore alignment
    def test_inspection_photos_do_not_rerun_initialization(self):
        s = self.localized()
        alignment = s.alignment.copy()
        class MustNotRun:
            def solve(self, *args):
                raise AssertionError('Initialization ran after alignment')
        s.solver = MustNotRun()
        observation = {'observation_id':str(uuid4()), 'source_verified':True, 'files':[{'sha256':'wall-only'}]}
        s.observe(str(uuid4()), self.seg, [observation])
        self.assertEqual(s.alignment, alignment)
        self.assertEqual(s.phase, 'inspecting')
        self.assertIn(observation['observation_id'], s.evidence)
        self.assertEqual(len(s.evidence), 2)
        payload={'session_id':self.sid,'segment_id':self.seg,'sequence':1,'tracking_state':'normal','T_arkit_camera':IDENTITY}
        self.assertEqual(s.telemetry(payload,100)['T_bim_camera'][0][3],10)
        payload.update(sequence=2,tracking_state='not_available')
        s.telemetry(payload,101)
        self.assertIsNone(s.alignment)
        self.assertEqual(s.phase,'relocalizing')

    def test_transient_limited_hides_pose_without_reset(self):
        s = self.localized()
        alignment = s.alignment.copy()
        p = {'session_id':self.sid,'segment_id':self.seg,'sequence':1,
             'tracking_state':'limited','T_arkit_camera':IDENTITY}
        self.assertIsNone(s.telemetry(p,100))
        self.assertIsNone(s.snapshot(100)['latest_pose'])
        self.assertEqual(s.alignment, alignment)
        p.update(sequence=2, tracking_state='normal')
        self.assertIsNotNone(s.telemetry(p,101))
        self.assertEqual(s.alignment, alignment)
        self.assertEqual(len(s.evidence), 1)
        p.update(sequence=3, tracking_state='limited'); s.telemetry(p,110)
        p.update(sequence=4); s.telemetry(p,170)
        self.assertIsNone(s.snapshot(170)['latest_pose'])
        self.assertEqual(s.alignment, alignment)
        self.assertEqual(len(s.evidence), 1)
        p.update(sequence=5, tracking_state='normal')
        self.assertIsNotNone(s.telemetry(p,171))
        self.assertEqual(s.alignment, alignment)
        p.update(sequence=6, tracking_state='not_available'); s.telemetry(p,172)
        self.assertIsNone(s.alignment)
        self.assertEqual(s.tracking_events[-1]['event'], 'alignment_invalidated')

    def test_reset_and_late_results(self):
        s = self.localized(); s.start(str(uuid4()))
        self.assertEqual(s.snapshot()['alignments'],[])
        with self.assertRaises(ValueError): s.start(self.seg)
        with self.assertRaises(ValueError): s.observe(str(uuid4()),self.seg,self.evidence)
        s.finish()
        with self.assertRaises(ValueError): s.start(str(uuid4()))
    def test_idempotency_and_conflicts(self):
        s=Localization(self.sid,self.context,FixtureSolver());s.start(self.seg); batch=str(uuid4())
        before=s.observe(batch,self.seg,self.evidence)
        self.assertEqual(before,s.observe(batch,self.seg,self.evidence))
        with self.assertRaises(ValueError):s.observe(batch,self.seg,[])
    def test_invalid_geometry_rejected(self):
        for m in ([[1,0,0,0],[0,1,0,0],[0,0,-1,0],[0,0,0,1]],
                  [[1,0,0,float('nan')],[0,1,0,0],[0,0,1,0],[0,0,0,1]]):
            with self.assertRaises(ValueError):rigid(m)
    def test_unverified_input_rejected(self):
        s=Localization(self.sid,self.context);s.start(self.seg)
        self.evidence[0]['source_verified']=False
        with self.assertRaises(ValueError):s.observe(str(uuid4()),self.seg,self.evidence)

    def test_refinement_updates_identity_clears_stale_pose_and_is_idempotent(self):
        s = self.localized()
        old_id = s.alignment['alignment_id']
        s.pose = {'T_bim_camera': IDENTITY}
        class Refiner:
            calls = 0
            def refine(self, context, alignment, observations):
                self.calls += 1
                return dict(alignment, T_bim_arkit=IDENTITY,
                            evidence_observation_ids=[o['observation_id'] for o in observations])
        refiner = Refiner(); s.solver = refiner
        observation = {'observation_id':str(uuid4()), 'source_verified':True,
                       'files':[{'sha256':'refine-fixture'}]}
        batch = str(uuid4())
        s.observe(batch, self.seg, [observation])
        self.assertNotEqual(s.alignment['alignment_id'], old_id)
        self.assertIsNone(s.pose)
        self.assertEqual(s.alignment['T_bim_arkit'], IDENTITY)
        s.observe(batch, self.seg, [observation])
        self.assertEqual(refiner.calls, 1)

class HTTPTests(unittest.TestCase):
    def test_auth_persistence_and_old_segment(self):
        import io, json, tempfile
        from pathlib import Path
        from localization_service.server import App
        with tempfile.TemporaryDirectory() as tmp:
            token='test-only-service-token-000000'
            path=Path(tmp)/'state.db'; app=App(path,token)
            sid,segment=str(uuid4()),str(uuid4())
            def call(app,route,body=None,auth=True):
                payload=b'' if body is None else json.dumps(body).encode()
                status=[]
                result=app({'REQUEST_METHOD':'GET' if body is None else 'POST','PATH_INFO':route,
                    'CONTENT_LENGTH':str(len(payload)),'wsgi.input':io.BytesIO(payload),
                    'HTTP_AUTHORIZATION':'Bearer '+token if auth else ''},lambda s,h:status.append(s))
                return status[0], json.loads(b''.join(result))
            route='/internal/sessions/'+sid
            self.assertTrue(call(app,route,auth=False)[0].startswith('401'))
            self.assertTrue(call(app,route,{'context':None})[0].startswith('200'))
            call(app,route+'/start',{'segment_id':segment})
            restored=App(path,token)
            self.assertEqual(call(restored,route)[1]['guidance']['phase'],'inspecting')
            call(restored,route+'/finish',{})
            self.assertTrue(call(restored,route+'/start',{'segment_id':str(uuid4())})[0].startswith('422'))

if __name__ == '__main__': unittest.main()
