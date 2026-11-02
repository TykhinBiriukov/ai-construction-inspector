// The public Worker only serves assets and forwards API/WSS to the Python
// backend. All persistent app data stays in Supabase.
const SECURITY_HEADERS = {
  'Cache-Control': 'private, no-store',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'Content-Security-Policy': "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
};
export default {
  async scheduled(controller, env, ctx) {
    if (env.INSPECTOR_BACKEND_ORIGIN) return; // Native workers own the queue.
    await env.INSPECTOR.getByName('main-device-cancel-v2').process();
  },
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === '/' || url.pathname === '/demo' || url.pathname.startsWith('/ui/')) {
      if (url.pathname.startsWith('/ui/')) url.pathname = url.pathname.slice(3);
      const asset = await env.ASSETS.fetch(new Request(url, request));
      const response = new Response(asset.body, asset);
      for (const [name, value] of Object.entries(SECURITY_HEADERS)) response.headers.set(name, value);
      return response;
    }
    if (env.INSPECTOR_BACKEND_ORIGIN) {
      const upstream = new URL(env.INSPECTOR_BACKEND_ORIGIN);
      if (upstream.protocol !== 'https:' || !env.INSPECTOR_GATEWAY_TOKEN) {
        return new Response('Backend configuration unavailable', {status: 503});
      }
      upstream.pathname = upstream.pathname.replace(/\/$/, '') + url.pathname;
      upstream.search = url.search;
      const forwarded = new Request(upstream, request);
      forwarded.headers.set('X-Inspector-Gateway-Token', env.INSPECTOR_GATEWAY_TOKEN);
      return fetch(forwarded, {redirect: 'manual'});
    }
    const inspector = env.INSPECTOR.getByName('main-device-cancel-v2');
    const response = await inspector.fetch(request);
    // Authenticated chat polling also advances due work; persisted leases prevent
    // duplicate execution. Cron remains the fallback when the browser is closed.
    const chatPoll = request.method === 'GET' && /^\/v1\/chats\/[^/]+$/.test(url.pathname);
    const captureComplete = request.method === 'POST' && /^\/v1\/sessions\/[^/]+\/complete$/.test(url.pathname);
    if (response.ok && (chatPoll || captureComplete)) {
      ctx.waitUntil(inspector.process());
    }
    return response;
  },
};
