"""Isolated, bounded reconstruction process; API shutdown stops its own child."""
import json
import os
import subprocess
import signal
import sys
import time
from pathlib import Path


class Interrupted(Exception):pass


def reconstruct_process(images,output,stop,ifc=None):
    timeout=int(os.environ.get('PROGRESS4D_RECONSTRUCTION_TIMEOUT_SECONDS','3600'))
    if not 60<=timeout<=7200:raise ValueError('Invalid reconstruction time budget')
    env=dict(os.environ)
    env.pop('PROGRESS4D_SERVICE_TOKEN',None)
    env.update(PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='2',OMP_NUM_THREADS='2')
    log=Path(output).with_suffix('.log')
    with log.open('wb') as stream:
        command=([sys.executable,'-m','progress4d_service.pipeline',str(images),str(output),'--ifc',str(ifc)] if ifc else
                 [sys.executable,'-m','progress4d_service.reconstruct',str(images),str(output),'--threads','2'])
        child=subprocess.Popen(command,
            cwd=Path(__file__).resolve().parent.parent,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        deadline=time.monotonic()+timeout
        try:
            while child.poll() is None:
                if stop.wait(.5):raise Interrupted('Service stopping; job will resume after restart')
                if time.monotonic()>deadline:raise TimeoutError('Reconstruction exceeded time budget')
            if child.returncode:raise RuntimeError('Reconstruction process failed')
        finally:
            if child.poll() is None:
                os.killpg(child.pid,signal.SIGTERM)
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
    result=json.loads((Path(output)/'report.json').read_text())
    if not isinstance(result,dict) or result.get('status') not in ('sparse_reconstructed','insufficient_reconstruction','complete','inconclusive'):
        raise ValueError('Invalid reconstruction report')
    return result
