import base64
import hashlib
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
import numpy as np
from ifc_fixture import make
from test_cloud_store import StoreDouble
from localization_service.cloud_server import CloudRegistry
from localization_service.room_model import load_room,load_display_meshes
from localization_service.openings import select_entry,aperture_points,tile_intersects_opening,reframe_meshes

class OpeningTests(unittest.TestCase):
 def test_all_walls_windows_and_no_reflection(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'room.ifc';ids=make(path);room=load_room(path);meshes=load_display_meshes(path,room)
   self.assertFalse(room['entry_confirmed']);self.assertEqual(len(room['doors']),4)
   self.assertEqual(len(room['openings']),5);self.assertEqual(sum(m['kind']=='IfcWindow' for m in meshes),1)
   for door_id in ids:
    selected=select_entry(room,door_id);matrix=np.array(selected['T_bim_ifc'])
    self.assertAlmostEqual(np.linalg.det(matrix[:3,:3]),1.)
    door=next(d for d in selected['doors'] if d['element_id']==door_id)
    self.assertEqual((door['axis'],door['plane']),(0,0.))
    self.assertAlmostEqual(selected['door_width'],1.)
    projected=reframe_meshes(meshes,room,selected)
    expected=load_display_meshes(path,selected)
    for actual,want in zip(projected,expected):np.testing.assert_allclose(actual['vertices'],want['vertices'],atol=1e-8)
    for opening in selected['openings']:
     center=(np.array(opening['low'])+opening['high'])/2;axis=opening['axis'];center[axis]=opening['plane']
     inward=1 if axis==0 and opening['plane']==0 or axis==2 and opening['plane']<0 else -1
     behind=center.copy();behind[axis]-=inward*.5
     front=center.copy();front[axis]+=inward*.5
     self.assertEqual(aperture_points(np.array([center,behind,front]),selected).tolist(),[True,True,False])
     self.assertTrue(tile_intersects_opening(selected,axis,opening['plane'],center-.1,center+.1))
 def test_selection_is_persisted_and_locked_after_start(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);path=root/'room.ifc';ids=make(path);content=path.read_bytes();digest=hashlib.sha256(content).hexdigest()
   store=StoreDouble(root/'db');registry=CloudRegistry(store,root/'cache','secret'*8)
   for door in ids:
    sid=str(uuid4());body=dict(session_id=sid,project_id='test',room_id='test',model_version='upload-sha256:'+digest,ifc_base64=base64.b64encode(content).decode())
    registry.provision(body);route=['v1','localization','sessions',sid]
    with self.assertRaises(ValueError):registry.request('POST',route+['start'],{'segment_id':str(uuid4())})
    config=registry.request('POST',route+['entry'],{'entry_element_id':door})
    self.assertFalse(config['entry_selection_required']);self.assertEqual(config['room']['entry_element_id'],door)
    registry.provision(body)
    registry=CloudRegistry(store,root/'restart',registry.token)
    self.assertEqual(registry.request('GET',route+['config'],{})['room'],config['room'])
    registry.request('POST',route+['start'],{'segment_id':str(uuid4())})
    with self.assertRaises(ValueError):registry.request('POST',route+['entry'],{'entry_element_id':next(i for i in ids if i!=door)})
