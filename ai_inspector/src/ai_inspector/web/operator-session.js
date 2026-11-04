/* One operator session for Control Room and the document/chat workspace.
 * Credentials stay in sessionStorage; same-origin tabs share them in memory.
 */
'use strict';
(() => {
  const key = 'inspector-operator-session';
  let token = '';
  try { token = sessionStorage.getItem(key) || ''; } catch {}
  if (token.startsWith('demo.')) token = '';
  const listeners = new Set();
  let channel;
  try { channel = new BroadcastChannel('inspector-operator-session-v1'); } catch {}
  const requestId = crypto.randomUUID();
  let awaitingReply = !token;
  let resolveReady;
  const ready = new Promise(resolve => { resolveReady = resolve; });
  function store(value, notify = false) {
    if (typeof value !== 'string' || value.startsWith('demo.')) return;
    const changed = token !== value;
    token = value;
    try { if (token) sessionStorage.setItem(key, token); else sessionStorage.removeItem(key); } catch {}
    if (changed && notify) for (const listener of listeners) listener(token);
  }
  if (channel) {
    channel.onmessage = ({data}) => {
      if (!data || typeof data !== 'object') return;
      if (data.type === 'request' && token) {
        channel.postMessage({type: 'reply', requestId: data.requestId, token});
      } else if (data.type === 'reply' && data.requestId === requestId && awaitingReply) {
        awaitingReply = false;
        store(data.token, true);
        resolveReady();
      } else if (data.type === 'change') {
        awaitingReply = false;
        store(data.token, true);
        resolveReady();
      }
    };
    channel.postMessage({type: 'request', requestId});
  }
  if (token || !channel) resolveReady();
  else setTimeout(resolveReady, 300);
  window.InspectorOperatorSession = {
    ready,
    get: () => token,
    set(value) {
      if (typeof value !== 'string' || value.startsWith('demo.')) return;
      awaitingReply = false;
      const changed = token !== value;
      store(value);
      if (changed) channel?.postMessage({type: 'change', token});
    },
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); }
  };
})();
