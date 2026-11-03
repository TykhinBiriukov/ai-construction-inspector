"""Viewer preserves registration coordinates and cannot present rejected deviations."""
import base64
import json
from pathlib import Path
import re
import tempfile
import unittest

import numpy as np

from geometry_engine.visualization.scan_bim_viewer import export_scan_bim_viewer


class ScanBimViewerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.output = Path(temp.name)/'viewer.html'
        self.vertices = np.array([[10., 3., 1.], [12., 3., 1.], [12., 3., 4.], [10., 3., 4.]])
        self.faces = np.array([[0, 1, 2], [0, 2, 3]])
        self.reference = self.vertices + [0, .05, 0]

    def export(self, **updates):
        args = dict(output_path=self.output, vertices=self.vertices, faces=self.faces,
                    deviations_m=np.array([-.05, np.nan]), compared_mask=np.array([True, False]),
                    reference_vertices=self.reference, reference_faces=self.faces,
                    report={'status':'completed'})
        args.update(updates)
        return export_scan_bim_viewer(**args)

    def payload(self):
        text = self.output.read_text(encoding='utf-8')
        data = json.loads(re.search(r'<script id="scan-bim-data" type="application/json">(.*?)</script>',text,re.S)[1])
        return text,data

    def test_shared_normalization_keeps_actual_surface_offset(self):
        original = self.vertices.copy()
        self.export()
        _,data = self.payload()
        radius,center = data['normalization']['radius_m'],data['normalization']['center_world']
        scan = np.frombuffer(base64.b64decode(data['positions']),dtype='<f4').reshape(-1,3)*radius+center
        ref = np.frombuffer(base64.b64decode(data['reference_positions']),dtype='<f4').reshape(-1,3)*radius+center
        np.testing.assert_allclose(scan,self.vertices,atol=1e-6)
        np.testing.assert_allclose(ref-scan,np.tile([0,.05,0],(4,1)),atol=1e-6)
        np.testing.assert_array_equal(self.vertices,original)
        self.assertEqual(data['up_axis'],'z')

    def test_unknown_is_separate_from_zero_deviation(self):
        self.export(deviations_m=np.array([0.,np.nan]))
        _,data = self.payload()
        np.testing.assert_array_equal(np.frombuffer(base64.b64decode(data['compared_mask']),dtype='u1'),[1,0])
        self.assertEqual(data['compared_face_count'],1)
        np.testing.assert_array_equal(np.frombuffer(base64.b64decode(data['deviations_m']),dtype='<f4'),[0,0])

    def test_registration_rejection_disables_all_deviations_even_if_supplied(self):
        self.export(deviations_m=np.array([.01,-.02]),compared_mask=np.array([True,True]),
                    report={'status':'registration_rejected','reason':'Ambiguous pose'})
        text,data = self.payload()
        self.assertTrue(data['blocked'])
        self.assertEqual(data['compared_face_count'],0)
        self.assertFalse(np.frombuffer(base64.b64decode(data['compared_mask']),dtype='u1').any())
        self.assertFalse(np.frombuffer(base64.b64decode(data['deviations_m']),dtype='<f4').any())
        self.assertIn('ALIGNMENT UNVERIFIED',text)
        self.assertIn("el('deviation').disabled=true",text)

    def test_report_cannot_inject_script_and_viewer_is_offline(self):
        attack='</script><script>alert(1)</script>'
        self.export(report={'status':'completed','note':attack})
        text,data=self.payload()
        self.assertNotIn(attack,text)
        self.assertEqual(data['report']['note'],attack)
        self.assertNotRegex(text,r'<script[^>]+src=')
        self.assertNotIn('fetch(',text)
        self.assertNotIn('https://',text)

    def test_invalid_input_never_writes_file(self):
        invalid=[{'faces':np.array([[0,1,99]])}, {'deviations_m':np.array([np.nan,0])},
                 {'compared_mask':np.array([1,0])}, {'report':{'status':'running'}},
                 {'reference_vertices':np.array([[np.nan,0,0]])},
                 {'deviations_m':np.array([.1,np.inf])}]
        for update in invalid:
            with self.subTest(update=update),self.assertRaises(ValueError):
                self.export(**update)
            self.assertFalse(self.output.exists())

    def test_existing_file_is_protected(self):
        self.output.write_text('keep',encoding='utf-8')
        with self.assertRaises(FileExistsError):self.export()
        self.assertEqual(self.output.read_text(),'keep')


if __name__=='__main__':unittest.main()
