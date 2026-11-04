/* Both interfaces use the same persistent HttpOnly browser session. */
'use strict';
(() => {
  const endpoint = '/v1/operator/browser-session';
  const marker = 'cookie-session';
  const migratedKey = 'inspector-browser-session-migrated';
  const listeners = new Set();
  let token = '', mode = '';
  const publicPage = location.pathname === '/demo' || location.pathname === '/ui/demo.html';
  function storeState(authenticated) {
    token = authenticated ? marker : '';
    try {
      if (token) sessionStorage.setItem('inspector-operator-session', token);
      else sessionStorage.removeItem('inspector-operator-session');
      sessionStorage.removeItem('inspector-demo-session');
      sessionStorage.removeItem('inspector-demo-api-prefix');
    } catch {}
  }
  function markMigrated() { try { localStorage.setItem(migratedKey, '1'); } catch {} }
  async function request(method, credential) {
    const response = await fetch(endpoint, {method, credentials:'same-origin',
      headers:credential ? {Authorization:`Bearer ${credential}`} : {}, signal:AbortSignal.timeout(15000)});
    if (!response.ok) throw Error(response.status===401 ? 'Operator access was not accepted.' : 'Inspector could not be reached. Try again.');
    return response.json();
  }
  let channel;
  try { channel = new BroadcastChannel('inspector-browser-session-v1'); } catch {}
  if(channel)channel.onmessage=({data})=>{if(data?.type==='changed')location.reload();};
  window.addEventListener('storage', event => { if(!channel&&event.key==='inspector-browser-session-change')location.reload(); });
  function broadcast() {
    channel?.postMessage({type:'changed'});
    try { localStorage.setItem('inspector-browser-session-change', crypto.randomUUID()); } catch {}
  }
  const ready = (async()=>{
    let legacy='', migrated=false;
    try { legacy=sessionStorage.getItem('inspector-operator-session')||''; migrated=localStorage.getItem(migratedKey)==='1'; } catch {}
    // Remove old guest state regardless of authentication. Never create a guest project.
    storeState(false);
    const response=await fetch(endpoint,{credentials:'same-origin',signal:AbortSignal.timeout(15000)});
    if(response.ok){const session=await response.json();mode=session.mode||'operator';storeState(mode==='operator'||publicPage);if(mode==='operator')markMigrated();return;}
    if(response.status===401&&!migrated&&legacy&&legacy!==marker&&!legacy.startsWith('demo.')){
      await request('POST',legacy);mode='operator';storeState(true);markMigrated();broadcast();return;
    }
    if(publicPage&&response.status===401){
      const opened=await fetch('/v1/demo/session',{method:'POST',credentials:'same-origin',signal:AbortSignal.timeout(15000)});
      if(!opened.ok)throw Error('Inspector is temporarily unavailable.');
      mode='public';storeState(true);
    }
  })().catch(()=>{ /* A transient outage preserves the server cookie; login can retry. */ });
  window.InspectorOperatorSession={
    ready,
    get:()=>token,
    mode:()=>mode,
    headers:value=>value&&value!==marker?{Authorization:`Bearer ${value}`}:{},
    async set(value){
      if(value===marker)return;
      if(value&&value.startsWith('demo.'))throw Error('Sign in with operator access.');
      await request(value?'POST':'DELETE',value||undefined);
      mode=value?'operator':'';storeState(!!value);markMigrated();broadcast();
    },
    subscribe(listener){listeners.add(listener);return()=>listeners.delete(listener);}
  };
})();
