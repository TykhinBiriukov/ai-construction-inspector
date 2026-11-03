"""Small immutable result artifacts and project-scoped expiring read grants."""
import base64
import hashlib
import hmac
import json
import math
import time
import struct
from pathlib import Path


def mint_view_token(secret,project,ttl=900):
    payload=base64.urlsafe_b64encode(json.dumps(
        {'project':str(project),'expires':int(time.time())+ttl,'scope':'read'},
        separators=(',',':')).encode()).decode().rstrip('=')
    signature=hmac.new(secret.encode(),('progress4d-view:'+payload).encode(),hashlib.sha256).hexdigest()
    return payload+'.'+signature


def valid_view_token(secret,token,project):
    if not secret or len(token)>1024:return False
    try:
        payload,signature=token.split('.')
        expected=hmac.new(secret.encode(),('progress4d-view:'+payload).encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):return False
        claims=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
        return claims['scope']=='read' and claims['project']==str(project) and claims['expires']>time.time()
    except (ValueError,KeyError,TypeError):return False


def cloud_from_ply(path,limit=30000):
    """Only our generated XYZ/RGB PLY layout; never parse arbitrary uploads."""
    with Path(path).open('rb') as stream:
        if stream.readline().strip()!=b'ply':
            raise ValueError('Unsupported generated PLY')
        format_line=stream.readline().strip()
        if format_line not in (b'format ascii 1.0',b'format binary_little_endian 1.0'):
            raise ValueError('Unsupported generated PLY format')
        count=None
        properties=[]
        for _ in range(40):
            line=stream.readline().decode('ascii').strip()
            if line.startswith('element vertex '):count=int(line.split()[-1])
            if line.startswith('property '):properties.append(line)
            if line=='end_header':break
        else:raise ValueError('Invalid PLY header')
        if properties!=['property float x','property float y','property float z',
                        'property uchar red','property uchar green','property uchar blue']:
            raise ValueError('Unexpected PLY properties')
        if count is None or not 0<=count<=5_000_000:raise ValueError('Invalid vertex count')
        stride=max(1,math.ceil(count/limit))
        points=[]
        for index in range(count):
            values=(struct.unpack('<fffBBB',stream.read(15)) if format_line==b'format binary_little_endian 1.0'
                    else stream.readline().split())
            if len(values)!=6:raise ValueError('Unexpected PLY vertex layout')
            if index%stride:continue
            xyz=[float(v) for v in values[:3]]
            rgb=[int(v) for v in values[3:]]
            if not all(math.isfinite(v) for v in xyz) or not all(0<=v<=255 for v in rgb):
                raise ValueError('Invalid point')
            points.append(xyz+rgb)
    return dict(kind='sparse_photogrammetry',coordinate_system='unregistered_sfm',
                metric_scale_known=False,total_points=count,displayed_points=len(points),points=points)


def register_artifact(conn,root,project,job_id,name,mime,path):
    path=Path(path).resolve()
    relative=path.relative_to(root.resolve()).as_posix()
    content=path.read_bytes()
    sha=hashlib.sha256(content).hexdigest()
    conn.execute('INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?,?,?,?)',
                 (str(project),str(job_id),name,mime,relative,sha,len(content)))
    return dict(name=name,mime_type=mime,sha256=sha,bytes=len(content))
