"""Server-side Inspector client. Endpoint and service token are trusted config."""
import hashlib
from urllib.parse import urlsplit
from uuid import UUID
import httpx


class Progress4DError(Exception):
    def __init__(self,status,retryable):
        self.status,self.retryable=status,retryable
        super().__init__(f'Progress4D request failed ({status}); retryable={retryable}')


class Progress4DClient:
    def __init__(self,base_url,token,*,transport=None):
        parsed=urlsplit(base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Invalid server-owned endpoint')
        if parsed.scheme!='https' and not(parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','localhost','::1')):
            raise ValueError('HTTPS required outside local development')
        if not token:raise ValueError('Dedicated service token required')
        self.base_url=base_url.rstrip('/')
        self.client=httpx.Client(headers={'Authorization':'Bearer '+token},timeout=20,
                                 follow_redirects=False,transport=transport)
    def close(self):self.client.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def request(self,method,path,**kwargs):
        try:
            with self.client.stream(method,self.base_url+path,**kwargs) as response:
                if response.status_code not in (200,202):
                    raise Progress4DError(response.status_code,response.status_code in (408,429) or response.status_code>=500)
                data=bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data)>16_000_000:raise Progress4DError(502,False)
            import json
            value=json.loads(data)
            if not isinstance(value,dict):raise ValueError
            return value
        except httpx.HTTPError:raise Progress4DError(503,True) from None
        except (ValueError,TypeError):raise Progress4DError(502,False) from None
    def upload(self,project,data):
        if len(data)>25*1024*1024:raise ValueError('Asset too large')
        sha=hashlib.sha256(data).hexdigest()
        result=self.request('PUT',f'/v1/projects/{UUID(str(project))}/assets/{sha}',content=data)
        if result.get('sha256')!=sha or result.get('bytes')!=len(data):raise Progress4DError(502,False)
        return sha
    def submit(self,project,frozen_body):
        identity=str(UUID(frozen_body['request_id']))
        result=self.request('POST',f'/v1/projects/{UUID(str(project))}/jobs',json=frozen_body)
        if result.get('job_id')!=identity:raise Progress4DError(502,False)
        return result
    def result(self,project,job):
        identity=str(UUID(str(job)))
        result=self.request('GET',f'/v1/projects/{UUID(str(project))}/jobs/{identity}')
        if result.get('job_id')!=identity or result.get('status') not in ('queued','reconstructing','sparse_complete','complete','inconclusive','failed','cancelled'):
            raise Progress4DError(502,False)
        return result
    def timeline(self,project):
        identity=str(UUID(str(project)))
        result=self.request('GET',f'/v1/projects/{identity}/timeline')
        if result.get('project_id')!=identity:raise Progress4DError(502,False)
        return result
    def viewer(self,project):
        identity=str(UUID(str(project)))
        result=self.request('POST',f'/v1/projects/{identity}/view-session')
        path=result.get('viewer_path','')
        if not path.startswith(f'/viewer/?project={identity}#access='):raise Progress4DError(502,False)
        return self.base_url+path
