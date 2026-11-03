"""Immutable, versioned SfM cache keyed by exact ordered input bytes.

Contains only completed image reconstruction, never visual judgments or evaluator
truth. An exact hit is reported as reused computation, not fresh reconstruction.
"""
import hashlib,json,os,shutil
from pathlib import Path
from .reconstruct import reconstruct

CONFIG='ordered-sfm-1000-6000-affine-dsp-sequential10-exhaustive-fallback-v2'


def key_for(images):
    images=Path(images)
    inventory=[dict(name=p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(images.iterdir()) if p.suffix.lower() in ('.jpg','.jpeg','.png')]
    key=hashlib.sha256(json.dumps([CONFIG,inventory],sort_keys=True).encode()).hexdigest()
    return key,inventory


def install(source,destination,inventory):
    source,destination=Path(source),Path(destination)
    report=json.loads((source/'report.json').read_text())
    if report['status']!='sparse_reconstructed':return
    if json.loads((source/'input-inventory.json').read_text())!=inventory:raise ValueError('Reconstruction input provenance mismatch')
    if destination.exists():return
    temp=destination.with_name(destination.name+'.tmp-'+str(os.getpid()))
    temp.mkdir(parents=True,exist_ok=False)
    try:
        for name in ['report.json','input-inventory.json','sparse.ply']:
            shutil.copy2(source/name,temp/name)
        shutil.copytree(source/'sparse',temp/'sparse')
        try:temp.rename(destination)
        except FileExistsError:pass
    finally:
        if temp.exists():shutil.rmtree(temp)


def reconstruct_cached(images,output):
    cache=os.environ.get('PROGRESS4D_RECONSTRUCTION_CACHE')
    if not cache:return reconstruct(images,output)
    root=Path(cache);root.mkdir(parents=True,exist_ok=True);key,inventory=key_for(images);entry=root/key
    # This lock covers only identical input sets, allowing unrelated surveys.
    import fcntl
    with (root/(key+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if (entry/'report.json').exists():
            if json.loads((entry/'input-inventory.json').read_text())!=inventory:raise ValueError('Cache provenance mismatch')
            shutil.copytree(entry,output)
            result=json.loads((Path(output)/'report.json').read_text());result['computation']='reused_exact_input_sfm';return result
        result=reconstruct(images,output)
        install(output,entry,inventory);result['computation']='computed';return result
