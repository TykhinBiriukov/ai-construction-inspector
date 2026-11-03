"""Durable local photo/IFC reconstruction, evidence and temporal-report API."""
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import time
import fcntl
from contextlib import asynccontextmanager,contextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI,Depends,Header,HTTPException,Request
from fastapi.responses import FileResponse,JSONResponse
from pydantic import BaseModel,ConfigDict,Field,field_validator

from .schedule import read_schedule
from .results import mint_view_token,valid_view_token,cloud_from_ply,register_artifact
from .jobs import reconstruct_process,Interrupted
from .temporal import build_report

ROOT=Path(os.environ.get('PROGRESS4D_DATA','./progress4d-data')).resolve()
TOKEN=os.environ.get('PROGRESS4D_SERVICE_TOKEN','')
MAX_ASSET=25*1024*1024


@contextmanager
def db():
    ROOT.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(ROOT/'jobs.sqlite3',timeout=30) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('CREATE TABLE IF NOT EXISTS jobs (project TEXT, id TEXT, digest TEXT, body TEXT, status TEXT, result TEXT, PRIMARY KEY(project,id))')
        conn.execute('CREATE TABLE IF NOT EXISTS artifacts (project TEXT, job TEXT, name TEXT, mime TEXT, path TEXT, sha TEXT, size INTEGER, PRIMARY KEY(project,job,name))')
        conn.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, ifc_sha TEXT, schedule TEXT)')
        yield conn


def authenticate(authorization:str=Header(default='')):
    if not TOKEN or not hmac.compare_digest(authorization,'Bearer '+TOKEN):
        raise HTTPException(401,'Service authorization required')


def local_demo_read(request:Request,project:UUID):
    # Explicitly scoped, loopback-only read access for the local demo launcher.
    allowed=os.environ.get('PROGRESS4D_LOCAL_DEMO_PROJECT','')
    return (str(project)==allowed and bool(allowed)
        and request.client is not None and request.client.host in ('127.0.0.1','::1')
        and request.url.hostname in ('127.0.0.1','localhost','::1')
        and request.headers.get('sec-fetch-site','') not in ('cross-site',)
        and request.headers.get('origin',str(request.base_url).rstrip('/'))==str(request.base_url).rstrip('/'))


def authenticate_read(request:Request,project:UUID,authorization:str=Header(default='')):
    if local_demo_read(request,project):return

    token=authorization.removeprefix('Bearer ')
    if TOKEN and (hmac.compare_digest(authorization,'Bearer '+TOKEN) or valid_view_token(TOKEN,token,project)):
        return
    raise HTTPException(401,'Project read authorization required')


def asset_path(project,sha):
    if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):
        raise HTTPException(422,'Invalid sha256')
    return ROOT/'projects'/str(project)/'assets'/sha


def current_stage(project,job_id,status):
    if status!='reconstructing':return status
    files=sorted((ROOT/'projects'/str(project)/'runs'/str(job_id)).glob('attempt-*/stage.json'))
    if not files:return 'reconstruction'
    try:return json.loads(files[-1].read_text()).get('stage','reconstruction')
    except (OSError,ValueError):return 'reconstruction'


class Photo(BaseModel):
    model_config=ConfigDict(extra='forbid')
    sha256:str=Field(pattern='^[0-9a-f]{64}$')
    format:Literal['jpg','png']


class Job(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:UUID
    survey_id:UUID
    ifc_sha256:str=Field(pattern='^[0-9a-f]{64}$')
    captured_at:datetime
    zone_id:str=Field(min_length=1,max_length=150)
    source_kind:Literal['synthetic','site_photos','recorded','unspecified']='unspecified'
    photos:list[Photo]=Field(min_length=8,max_length=100)
    @field_validator('captured_at')
    @classmethod
    def timezone_required(cls,v):
        if v.tzinfo is None: raise ValueError('Timestamp must include timezone')
        return v
    @field_validator('photos')
    @classmethod
    def unique_photos(cls,v):
        if len({p.sha256 for p in v})!=len(v): raise ValueError('Duplicate photos')
        return v


def refine_from_previous(project,job,result,output):
    """Optional visual comparison uses earlier completed surveys, never schedule truth."""
    if not result.get('perception'):return result
    with db() as conn:
        rows=conn.execute("SELECT id,body,result FROM jobs WHERE project=? AND status='complete'",(project,)).fetchall()
        prior=[]
        for row in rows:
            body=json.loads(row['body']);captured=datetime.fromisoformat(body['captured_at'])
            if captured<job.captured_at and body['zone_id']==job.zone_id:prior.append((captured,row))
        if not prior:return result
        row=max(prior,key=lambda x:x[0])[1]
        before={r['name'][:-4]:ROOT/r['path'] for r in conn.execute("SELECT name,path FROM artifacts WHERE project=? AND job=? AND name LIKE 'P%.jpg'",(project,row['id']))}
    after={p.stem:p for p in (output/'evidence').glob('P*.jpg')}
    from .contrast import refine
    result=refine(json.loads(row['result']),result,before,after,output/'temporal-evidence')
    for p in result['perception']['products']:
        if p.get('comparison_basis'):p['comparison_reference_job']=row['id']
    return result


def worker(stop):
    # Exactly one worker process per database in this prototype.
    while not stop.is_set():
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if row: conn.execute("UPDATE jobs SET status='reconstructing' WHERE project=? AND id=?",(row['project'],row['id']))
        if not row:
            stop.wait(.5); continue
        try:
            job=Job.model_validate_json(row['body'])
            base=ROOT/'projects'/row['project']/'runs'/row['id']
            images=base/'images'; images.mkdir(parents=True,exist_ok=True)
            for i,photo in enumerate(job.photos):
                target=images/f'{i:04d}.{photo.format}'
                if not target.exists(): target.hardlink_to(asset_path(row['project'],photo.sha256))
            # New attempt path preserves any interrupted partial output.
            output=base/f'attempt-{time.time_ns()}'
            result=reconstruct_process(images,output,stop,asset_path(row['project'],job.ifc_sha256))
            result=refine_from_previous(row['project'],job,result,output)
            artifacts=[]
            if (output/'surface.glb').is_file():
                with db() as conn:
                    artifacts.append(register_artifact(conn,ROOT,row['project'],row['id'],'surface.glb','model/gltf-binary',output/'surface.glb'))
            if (output/'cloud.json').is_file():
                with db() as conn:
                    artifacts.append(register_artifact(conn,ROOT,row['project'],row['id'],'cloud.json','application/json',output/'cloud.json'))
                    for evidence in (output/'evidence').glob('P*.jpg'):
                        artifacts.append(register_artifact(conn,ROOT,row['project'],row['id'],evidence.name,'image/jpeg',evidence))
            elif (output/'sparse.ply').is_file():
                cloud=cloud_from_ply(output/'sparse.ply')
                (output/'cloud.json').write_text(json.dumps(cloud,separators=(',',':')))
                with db() as conn:
                    artifacts.append(register_artifact(conn,ROOT,row['project'],row['id'],'cloud.json','application/json',output/'cloud.json'))
                    artifacts.append(register_artifact(conn,ROOT,row['project'],row['id'],'sparse.ply','application/octet-stream',output/'sparse.ply'))
            result['artifacts']=artifacts
            status=result['status']
            if status=='sparse_reconstructed':status='sparse_complete'
            elif status=='insufficient_reconstruction':status='inconclusive'
        except Interrupted:
            return  # Saved reconstructing state is recovered by the next worker owner.
        except Exception as exc:
            logging.exception('Progress4D reconstruction failed for job %s',row['id'])
            result={'error_type':type(exc).__name__,'message':'Reconstruction failed; inspect private worker diagnostics'}
            status='failed'
        with db() as conn:
            destination=ROOT/'projects'/row['project']/'runs'/row['id']/'result.json'
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_text(json.dumps(result,indent=2))
            register_artifact(conn,ROOT,row['project'],row['id'],'result.json','application/json',destination)
            conn.execute('UPDATE jobs SET status=?,result=? WHERE project=? AND id=?',
                         (status,json.dumps(result),row['project'],row['id']))


@asynccontextmanager
async def lifespan(app):
    with db():pass
    stop=threading.Event()
    threads=[]
    lock=None
    if os.environ.get('PROGRESS4D_WORKER','1')=='1':
        lock=(ROOT/'worker.lock').open('a')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError('Another worker owns this data directory; use one worker process') from None
        with db() as conn:
            conn.execute("UPDATE jobs SET status='queued' WHERE status='reconstructing'")
        concurrency=int(os.environ.get('PROGRESS4D_CONCURRENCY','1'))
        if concurrency not in (1,2):raise ValueError('Worker concurrency must be 1 or 2')
        for _ in range(concurrency):
            thread=threading.Thread(target=worker,args=(stop,),daemon=True);thread.start();threads.append(thread)
    try:yield
    finally:
        stop.set()
        for thread in threads:thread.join(timeout=15)
        if lock:lock.close()


app=FastAPI(title='Progress 4D development API',lifespan=lifespan,
            docs_url=None,redoc_url=None,openapi_url=None)


@app.get('/healthz')
def health():
    return dict(status='ok',stage='local_4d_pipeline',analysis_scope='single_room_observed_geometry_and_product_progress')


@app.put('/v1/projects/{project}/assets/{sha}',dependencies=[Depends(authenticate)])
async def upload(project:UUID,sha:str,request:Request):
    path=asset_path(project,sha)
    content=bytearray()
    async for chunk in request.stream():
        if len(content)+len(chunk)>MAX_ASSET: raise HTTPException(413,'Asset too large')
        content.extend(chunk)
    if hashlib.sha256(content).hexdigest()!=sha: raise HTTPException(422,'Content hash mismatch')
    path.parent.mkdir(parents=True,exist_ok=True)
    # O_EXCL makes concurrent identical uploads safe; content address is immutable.
    temp=path.with_name(path.name+'.'+str(time.time_ns())+'.tmp')
    try:
        with temp.open('xb') as f: f.write(content)
        try: os.link(temp,path)
        except FileExistsError: pass
    finally:
        temp.unlink(missing_ok=True)
    return dict(sha256=sha,bytes=len(content))


@app.post('/v1/projects/{project}/jobs',dependencies=[Depends(authenticate)],status_code=202)
def submit(project:UUID,job:Job):
    paths=[asset_path(project,job.ifc_sha256)]+[asset_path(project,p.sha256) for p in job.photos]
    if any(not p.is_file() for p in paths): raise HTTPException(422,'Upload all project-scoped assets first')
    try: baseline=read_schedule(paths[0])
    except Exception as exc: raise HTTPException(422,'Invalid or unsupported IFC schedule') from exc
    from PIL import Image
    try:
        dimensions=set()
        for path,photo in zip(paths[1:],job.photos):
            with Image.open(path) as image:
                if image.format!= {'jpg':'JPEG','png':'PNG'}[photo.format] or image.width*image.height>24_000_000:
                    raise ValueError('Unsupported photo')
                dimensions.add(image.size)
                image.verify()
        if len(dimensions)!=1:raise ValueError('Single-camera survey requires identical image dimensions')
    except Exception as exc: raise HTTPException(422,'Invalid photo content') from exc
    body=job.model_dump_json()
    digest=hashlib.sha256(body.encode()).hexdigest()
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        old=conn.execute('SELECT digest,status FROM jobs WHERE project=? AND id=?',(str(project),str(job.request_id))).fetchone()
        if old and old['digest']!=digest: raise HTTPException(409,'Request ID already used with different inputs')
        if not old:
            conn.execute('INSERT OR IGNORE INTO projects VALUES (?,?,?)',
                         (str(project),job.ifc_sha256,json.dumps(baseline)))
            previous=conn.execute('SELECT body FROM jobs WHERE project=?',(str(project),)).fetchall()
            for existing in previous:
                frozen=json.loads(existing['body'])
                if frozen['ifc_sha256']!=job.ifc_sha256:
                    raise HTTPException(409,'Project baseline is immutable; create a new project revision')
                if frozen['survey_id']==str(job.survey_id):
                    raise HTTPException(409,'Survey already submitted; reuse its original request ID')
                if frozen['zone_id']!=job.zone_id:
                    raise HTTPException(409,'Initial single-room service requires one zone per project')
                if datetime.fromisoformat(frozen['captured_at'])==job.captured_at:
                    raise HTTPException(409,'A survey already exists at this timestamp')
            conn.execute('INSERT INTO jobs VALUES (?,?,?,?,?,NULL)',(str(project),str(job.request_id),digest,body,'queued'))
    return dict(job_id=str(job.request_id),status=old['status'] if old else 'queued')


@app.get('/v1/projects/{project}/jobs/{job_id}',dependencies=[Depends(authenticate_read)])
def result(project:UUID,job_id:UUID):
    with db() as conn:
        row=conn.execute('SELECT status,result FROM jobs WHERE project=? AND id=?',(str(project),str(job_id))).fetchone()
    if not row: raise HTTPException(404,'Job not found')
    return dict(job_id=str(job_id),status=row['status'],stage=current_stage(project,job_id,row['status']),result=json.loads(row['result']) if row['result'] else None)


@app.get('/v1/projects/{project}/timeline',dependencies=[Depends(authenticate_read)])
def timeline(project:UUID):
    with db() as conn:
        rows=conn.execute('SELECT id,body,status,result FROM jobs WHERE project=?',(str(project),)).fetchall()
        baseline=conn.execute('SELECT schedule FROM projects WHERE id=?',(str(project),)).fetchone()
    surveys=[]
    for row in rows:
        job=Job.model_validate_json(row['body'])
        surveys.append(dict(job_id=row['id'],survey_id=str(job.survey_id),zone_id=job.zone_id,
            source_kind=job.source_kind,
            captured_at=job.captured_at.isoformat(),ifc_sha256=job.ifc_sha256,
            photo_count=len(job.photos),status=row['status'],
            stage=current_stage(project,row['id'],row['status']),
            result=json.loads(row['result']) if row['result'] else None))
    surveys.sort(key=lambda s:(datetime.fromisoformat(s['captured_at']),s['survey_id']))
    schedule=json.loads(baseline['schedule']) if baseline else None
    return dict(project_id=str(project),surveys=surveys,schedule=schedule,
                report=build_report(schedule,[s for s in surveys if s['result'] is not None and s['status'] in ('complete','inconclusive','sparse_complete')]) if schedule else None,
                coordinate_alignment='per_survey_quality_gate',
                processing_complete=bool(surveys) and all(s['status'] in ('complete','inconclusive','failed','cancelled') for s in surveys))


@app.post('/v1/projects/{project}/view-session',dependencies=[Depends(authenticate)])
def view_session(project:UUID):
    with db() as conn:
        exists=conn.execute('SELECT 1 FROM jobs WHERE project=? LIMIT 1',(str(project),)).fetchone()
    if not exists:raise HTTPException(404,'Project not found')
    ttl=int(os.environ.get('PROGRESS4D_VIEW_TTL_SECONDS','900'))
    if not 60<=ttl<=7200:raise HTTPException(503,'Invalid viewer lifetime configuration')
    token=mint_view_token(TOKEN,project,ttl=ttl)
    return dict(expires_in=ttl,viewer_path=f'/viewer/?project={project}#access={token}')


@app.get('/v1/projects/{project}/report',dependencies=[Depends(authenticate_read)])
def project_report(project:UUID):
    value=timeline(project)
    return JSONResponse(value,headers={'Content-Disposition':f'attachment; filename="progress-{project}.json"','Cache-Control':'no-store'})


@app.get('/contracts',dependencies=[Depends(authenticate)])
def contracts():
    return dict(version='local-4d-v1',job=Job.model_json_schema(),
                capture_requirements=['8–100 ordered overlapping upright photographs from one camera with fixed zoom and equal dimensions.',
                                      'One rectangular axis-aligned IFC room per project; explicit schedule and assigned products.',
                                      'Photo-only geometry cannot recover hidden surfaces; unsupported/ambiguous inputs abstain.'],
                endpoints=['PUT /v1/projects/{project}/assets/{sha256}','POST /v1/projects/{project}/jobs',
                           'GET /v1/projects/{project}/jobs/{job}','GET /v1/projects/{project}/timeline',
                           'GET /v1/projects/{project}/report','POST /v1/projects/{project}/view-session'])


@app.get('/v1/projects/{project}/jobs/{job_id}/artifacts/{name}',dependencies=[Depends(authenticate_read)])
def artifact(project:UUID,job_id:UUID,name:str):
    with db() as conn:
        row=conn.execute('SELECT * FROM artifacts WHERE project=? AND job=? AND name=?',
                         (str(project),str(job_id),name)).fetchone()
    if not row:raise HTTPException(404,'Artifact not found')
    path=(ROOT/row['path']).resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():raise HTTPException(404,'Artifact unavailable')
    return FileResponse(path,media_type=row['mime'],filename=name,content_disposition_type='inline',
                        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','ETag':'"'+row['sha']+'"'})


@app.get('/viewer/')
def viewer():
    return FileResponse(Path(__file__).with_name('web')/'viewer.html',headers={
        'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
        'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self' blob:; img-src 'self' blob:; frame-ancestors 'self' https://inspector.campuscompile.eu"})


@app.get('/viewer/{name}')
def viewer_asset(name:str):
    if name not in ('viewer.js','viewer.css','surface.bundle.js','surface.bundle.js.LEGAL.txt','three-LICENSE.txt'):raise HTTPException(404)
    return FileResponse(Path(__file__).with_name('web')/name,headers={'Cache-Control':'no-store'})
