// Dedicated transport for Drawing Analysis Agent. No caller-selected upstream,
// no storage/logging, no automatic retries, no Inspector dependencies.
const UPSTREAM = 'https://api.meta.ai/v1/chat/completions';
const MAX_REQUEST = 22 * 1024 * 1024;
const MAX_RESPONSE = 256 * 1024;

function limited(stream, limit, onDone = () => {}) {
  const reader = stream.getReader();
  let size = 0;
  return new ReadableStream({
    async pull(controller) {
      try {
        const item = await reader.read();
        if (item.done) { onDone(); controller.close(); return; }
        size += item.value.byteLength;
        if (size > limit) throw new Error('body_limit');
        controller.enqueue(item.value);
      } catch (error) {
        onDone(); await reader.cancel().catch(() => {}); controller.error(error);
      }
    },
    async cancel(reason) { onDone(); await reader.cancel(reason); },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === 'GET' && url.pathname === '/healthz')
      return Response.json({service: 'drawing-analysis-meta-relay', configured: Boolean(env.RELAY_TOKEN && env.META_API_KEY)});
    if (!env.RELAY_TOKEN || !env.META_API_KEY) return new Response(null, {status: 503});
    if (request.headers.get('Authorization') !== `Bearer ${env.RELAY_TOKEN}`)
      return new Response(null, {status: 401});
    if (request.method !== 'POST' || url.pathname !== '/v1/chat/completions' || url.search)
      return new Response(null, {status: 404});
    if (!request.headers.get('Content-Type')?.toLowerCase().startsWith('application/json'))
      return new Response(null, {status: 415});
    const declared = request.headers.get('Content-Length');
    if (declared && (!/^\d+$/.test(declared) || Number(declared) > MAX_REQUEST))
      return new Response(null, {status: 413});
    if (!request.body) return new Response(null, {status: 400});
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), 65000);
    const done = () => clearTimeout(timer);
    try {
      const upstream = await fetch(UPSTREAM, {
        method: 'POST', redirect: 'manual', signal: abort.signal,
        headers: {'Authorization': `Bearer ${env.META_API_KEY}`, 'Content-Type': 'application/json'},
        body: limited(request.body, MAX_REQUEST),
      });
      if (upstream.status >= 300 && upstream.status < 400) {
        await upstream.body?.cancel(); done(); return new Response(null, {status: 502});
      }
      const headers = new Headers({'Content-Type': 'application/json', 'Cache-Control': 'no-store'});
      const id = upstream.headers.get('x-request-id');
      if (id) headers.set('x-request-id', id.slice(0, 200));
      if (!upstream.body) { done(); return new Response(null, {status: upstream.status, headers}); }
      return new Response(limited(upstream.body, MAX_RESPONSE, done), {status: upstream.status, headers});
    } catch {
      done(); return Response.json({error: {code: 'relay_transport_error'}}, {status: 502});
    }
  },
};
