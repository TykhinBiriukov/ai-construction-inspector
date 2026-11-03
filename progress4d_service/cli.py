"""Inspector-compatible local client: upload a survey, poll or open its report."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID,uuid5
from .client import Progress4DClient


def submit_survey(client,project,ifc,manifest_path):
    manifest_path=Path(manifest_path).resolve();root=manifest_path.parent
    manifest=json.loads(manifest_path.read_text())
    ifc_sha=client.upload(project,Path(ifc).read_bytes());photos=[]
    for photo in manifest['photos']:
        path=(root/photo['file']).resolve()
        if not path.is_relative_to(root):raise ValueError('Photo path escapes survey folder')
        data=path.read_bytes();sha=hashlib.sha256(data).hexdigest()
        if photo.get('sha256') and photo['sha256']!=sha:raise ValueError('Photo hash mismatch')
        client.upload(project,data)
        extension=path.suffix.lower()
        if extension not in ('.jpg','.jpeg','.png'):raise ValueError('Unsupported photo format')
        photos.append(dict(sha256=sha,format='png' if extension=='.png' else 'jpg'))
    survey=str(uuid5(project,manifest['zone_id']+':'+manifest['captured_at']))
    identity=hashlib.sha256(json.dumps([ifc_sha,photos,survey],sort_keys=True).encode()).hexdigest()
    body=dict(request_id=str(uuid5(project,identity)),survey_id=survey,ifc_sha256=ifc_sha,
              zone_id=manifest['zone_id'],captured_at=manifest['captured_at'],photos=photos,
              source_kind='synthetic' if manifest.get('synthetic') is True else manifest.get('source_kind','unspecified'))
    return client.submit(project,body)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base-url',default='http://127.0.0.1:8791');parser.add_argument('--project',type=UUID,required=True)
    commands=parser.add_subparsers(dest='command',required=True)
    upload=commands.add_parser('submit');upload.add_argument('--ifc',required=True);upload.add_argument('--manifest',required=True)
    status=commands.add_parser('status');status.add_argument('--job',type=UUID,required=True)
    commands.add_parser('timeline');commands.add_parser('viewer')
    args=parser.parse_args()
    with Progress4DClient(args.base_url,os.environ.get('PROGRESS4D_SERVICE_TOKEN','')) as client:
        if args.command=='submit':result=submit_survey(client,args.project,args.ifc,args.manifest)
        elif args.command=='status':result=client.result(args.project,args.job)
        elif args.command=='timeline':result=client.timeline(args.project)
        else:result={'url':client.viewer(args.project)}
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
