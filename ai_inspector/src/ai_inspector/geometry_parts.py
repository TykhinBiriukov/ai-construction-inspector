"""Receive bounded, independent Geometry ZIP parts. No Geometry invocation here."""
import hashlib
import io
import json
import math
import re
import stat
import tempfile
import zipfile
import zlib
from pathlib import Path
from uuid import UUID
from .errors import APIError


def invalid(message):
    return APIError(422, 'geometry_parts_invalid', message)


def check_zip(data, part, session, settings):
    try:
        with zipfile.ZipFile(data) as archive:
            infos=archive.infolist();names=[i.filename for i in infos]
            if len(infos)>settings.max_archive_entries or len(set(n.casefold() for n in names))!=len(names):raise invalid('Duplicate or excessive ZIP entries')
            if sum(i.file_size for i in infos)>settings.max_unpacked_archive_bytes:raise invalid('Unpacked part is too large')
            for i in infos:
                if i.flag_bits & 1 or i.compress_type not in (0,8) or stat.S_ISLNK(i.external_attr>>16):raise invalid('Unsupported ZIP member')
            def document(name):
                if archive.getinfo(name).file_size>settings.max_manifest_bytes:raise invalid('Metadata is too large')
                return json.loads(archive.read(name))
            capture=document('capture.json')
            required={'schema_version':'1.0','session_id':session['session_id'],'project_id':session['project_id'],
                      'length_unit':'meter','coordinate_system':'arkit','matrix_storage':'rows','frames_directory':'frames','frame_count':len(part['frame_ids'])}
            if any(capture.get(k)!=v for k,v in required.items()):raise invalid('Capture context differs from its session')
            expected={'capture.json'};depth_ids=[]
            if capture.get('mesh')=='mesh.ply':
                expected.add('mesh.ply')
                with archive.open('mesh.ply') as stream:
                    if not stream.read(64).startswith(b'ply\nformat binary_little_endian 1.0\n'):raise invalid('Invalid mesh encoding')
            elif capture.get('mesh') is not None:raise invalid('Invalid mesh reference')
            for fid in part['frame_ids']:
                prefix=f'frames/{fid}/';expected|={prefix+'rgb.jpg',prefix+'metadata.json'}
                meta=document(prefix+'metadata.json')
                if meta.get('schema_version')!='1.0' or meta.get('frame_id')!=fid or meta.get('tracking_state')!='normal':raise invalid('Invalid frame identity/tracking')
                if not isinstance(meta.get('timestamp_s'),(float,int)) or not math.isfinite(meta['timestamp_s']):raise invalid('Invalid timestamp')
                rgb=meta['rgb'];intr=meta['intrinsics'];pose=meta['pose']['T_world_from_camera'];k=intr['K']
                if rgb['path']!='rgb.jpg' or any(type(rgb[n])!=int or rgb[n]<=0 or intr[n]!=rgb[n] for n in ('width','height')):raise invalid('RGB/intrinsics dimensions differ')
                for matrix,n in ((k,3),(pose,4)):
                    if len(matrix)!=n or any(len(row)!=n or any(type(v) not in (float,int) or not math.isfinite(v) for v in row) for row in matrix):raise invalid('Invalid matrix')
                if k[0][0]<=0 or k[1][1]<=0 or any(abs(pose[3][i]-[0,0,0,1][i])>1e-5 for i in range(4)):raise invalid('Invalid camera calibration/pose')
                depth=meta.get('depth');conf=meta.get('confidence')
                if depth is None:
                    if conf is not None:raise invalid('Confidence without depth')
                else:
                    if capture.get('mesh')!='mesh.ply':raise invalid('LiDAR capture requires mesh')
                    if any(depth.get(a)!=b for a,b in {'path':'depth.bin','dtype':'float32','byte_order':'little','unit':'meter','source':'sceneDepth'}.items()):raise invalid('Invalid depth encoding')
                    w,h=depth['width'],depth['height']
                    if type(w)!=int or type(h)!=int or min(w,h)<=0 or w*h>1000000:raise invalid('Invalid depth dimensions')
                    expected.add(prefix+'depth.bin')
                    if archive.getinfo(prefix+'depth.bin').file_size!=w*h*4:raise invalid('Depth buffer length differs')
                    if conf is not None:
                        if conf!={'path':'confidence.bin','width':w,'height':h,'dtype':'uint8'}:raise invalid('Invalid confidence encoding')
                        expected.add(prefix+'confidence.bin')
                        if archive.getinfo(prefix+'confidence.bin').file_size!=w*h:raise invalid('Confidence buffer length differs')
                        depth_ids.append(fid)
            if set(names)!=expected or depth_ids!=part['depth_frame_ids']:raise invalid('ZIP inventory differs from index')
            # Check CRCs without keeping all members in memory.
            if archive.testzip() is not None:raise invalid('Corrupt ZIP member')
    except APIError:raise
    except (ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile, RuntimeError, EOFError, IndexError, zlib.error):raise invalid('Malformed Geometry ZIP') from None


def validate_parts(store, session, ref):
    # Existing manifest validation supplies storage scope and descriptor rules.
    prefix,descriptor=store._reference(ref | {'format':'supabase_capture_v1'},session)
    completion=descriptor['object_key'].rsplit('/',1)[1].removesuffix('.json')
    staging=Path(store.settings.data_dir)/'staging';staging.mkdir(parents=True,exist_ok=True)
    with tempfile.SpooledTemporaryFile(max_size=1024*1024,dir=staging) as temporary,store.connection() as client:
        store._stream_to(client,descriptor['object_key'],temporary,descriptor['byte_length'],descriptor['sha256']);temporary.seek(0)
        raw=temporary.read()
        try:index=json.loads(raw)
        except (ValueError,UnicodeError):raise invalid('Invalid JSON index') from None
        if not isinstance(index,dict):raise invalid('Index must be an object')
        if any(index.get(k)!=v for k,v in {'format':'geometry_capture_parts_v1','schema_version':'1.0','session_id':session['session_id'],'project_id':session['project_id'],'completion_id':completion}.items()):raise invalid('Index scope mismatch')
        parts=index.get('parts')
        if not isinstance(parts,list) or not 1<=len(parts)<=9999:raise invalid('Invalid part count')
        seen=set();files=[];total=0
        for number,part in enumerate(parts,1):
            if not isinstance(part,dict):raise invalid("Part must be an object")
            name=f'capture-{number:04d}.zip';key=prefix+f'geometry/{completion}/{name}'
            if part.get('name')!=name or part.get('object_key')!=key:raise invalid('Invalid part key')
            store._descriptor(part)
            if part['byte_length']>store.settings.max_archive_bytes:raise invalid('Part exceeds archive limit')
            total+=part['byte_length']
            if total>store.settings.max_capture_bytes:raise invalid('Capture exceeds total limit')
            try:
                UUID(part['segment_id']);ids=part['frame_ids']
                if not isinstance(ids,list) or not ids:raise ValueError()
                canonical=[str(UUID(i)) for i in ids]
                if len(set(canonical))!=len(ids) or seen.intersection(canonical):raise ValueError()
                seen.update(canonical)
            except (ValueError,KeyError,TypeError):raise invalid('Invalid or repeated frame/segment IDs') from None
            with tempfile.SpooledTemporaryFile(max_size=1024*1024,dir=staging) as archive:
                store._stream_to(client,key,archive,part['byte_length'],part['sha256']);archive.seek(0)
                check_zip(archive,part,session,store.settings)
                pinned=prefix+f"accepted/{completion}/{part['sha256']}/{name}"
                store._publish_pin(client,pinned,archive,part['byte_length'],part['sha256'])
            files.append(part|{'pinned_object_key':pinned,'observation_id':None})
        if index.get('frame_count')!=len(seen):raise invalid('Frame count mismatch')
        pinned_manifest=prefix+f"accepted/{completion}/{descriptor['sha256']}/capture-manifest.json"
        store._publish_pin(client,pinned_manifest,io.BytesIO(raw),len(raw),descriptor['sha256'])
    return {'observation_count':len(seen),'files':files,'pinned_capture_ref':ref|{'snapshot_id':completion,'pinned_manifest_key':pinned_manifest}}
