"""Prepare only the assigned session's BIM at the separate localization service."""
import base64
import hashlib
from urllib.parse import urlsplit
import httpx
from .errors import APIError


def prepare_localization(service, principal, session_id):
    session=service.get_session(principal,session_id) # Enforce device and project scope first.
    if session['capture_state'] not in ('assigned','capturing'):
        raise APIError(409,'session_closed','Localization requires an active capture session')
    settings=service.settings
    url=settings.localization_base_url.rstrip('/')
    if not url or not settings.localization_service_token:
        raise APIError(503,'localization_not_configured','Localization Service is not configured',retryable=True)
    if urlsplit(url).scheme!='https':
        raise APIError(503,'localization_not_configured','Localization requires HTTPS')
    with service.database.connection() as connection:
        inspection=service._load(connection,'inspections',session['inspection_id'])
    model=inspection.get('bim_model')
    if not model:raise APIError(422,'bim_required','This capture has no BIM model')
    if session['model_version']!='upload-sha256:'+model['sha256']:
        raise APIError(409,'model_version_mismatch','Assignment BIM version changed')
    path=service.bim_store.path_for(model['sha256'])
    if not path.is_file() or path.stat().st_size>8*1024*1024:
        raise APIError(422,'localization_bim_unavailable','Localization requires IFC of at most8MiB')
    content=path.read_bytes()
    if hashlib.sha256(content).hexdigest()!=model['sha256']:
        raise APIError(503,'bim_integrity_error','Stored BIM hash mismatch')
    body={k:session[k] for k in ('session_id','project_id','room_id','model_version')}
    body['ifc_base64']=base64.b64encode(content).decode()
    try:
        with httpx.Client(transport=getattr(service,'localization_transport',None),timeout=25,follow_redirects=False) as client:
            response=client.post(url+'/internal/localization/sessions',json=body,
                headers={'Authorization':'Bearer '+settings.localization_service_token})
        if response.status_code==422:
            raise APIError(422,'unsupported_localization_bim','Localization currently supports one rectangular room, four walls and one entrance on min-X, in metres')
        if response.status_code!=200:raise ValueError('upstream status')
        result=response.json()
        if result['session_id']!=session_id or result['model_version']!=session['model_version'] or len(result['session_token'])!=64:
            raise ValueError('upstream binding')
        return {**{k:result[k] for k in ('session_id','model_version','session_token')},'endpoint':url}
    except (httpx.HTTPError,ValueError,KeyError,TypeError):
        raise APIError(503,'localization_unavailable','Localization Service did not confirm this session; retry',retryable=True) from None
