// The public Worker only serves assets and forwards API/WSS to the Python
// Durable Object (30s CPU budget). All persistent app data stays in Supabase.
const SECURITY_HEADERS = {
  'Cache-Control': 'private, no-store',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
};
export default {
  async scheduled(controller, env, ctx) {
    await env.INSPECTOR.getByName('main').process();
  },
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === '/' || url.pathname.startsWith('/ui/')) {
      if (url.pathname.startsWith('/ui/')) url.pathname = url.pathname.slice(3);
      const asset = await env.ASSETS.fetch(new Request(url, request));
      const response = new Response(asset.body, asset);
      for (const [name, value] of Object.entries(SECURITY_HEADERS)) response.headers.set(name, value);
      return response;
    }
    const inspector = env.INSPECTOR.getByName('main');
    const response = await inspector.fetch(request);
    if (request.method === 'POST' && response.ok && /^\/v1\/sessions\/[^/]+\/complete$/.test(url.pathname)) {
      ctx.waitUntil(inspector.process());
    }
    return response;
  },
};
