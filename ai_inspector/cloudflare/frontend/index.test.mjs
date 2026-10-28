import { test } from 'node:test';
import assert from 'node:assert/strict';
import gateway from './index.js';

for (const [status, method, expected] of [[202,'POST',1],[401,'POST',0],[200,'GET',0]]) {
  test(`completion wakeup: ${method} ${status}`, async () => {
    let processed = 0;
    const waiting = [];
    const stub = { fetch: async () => new Response('{}',{status}), process: async () => { processed++; } };
    const env = { INSPECTOR:{getByName: () => stub} };
    const response = await gateway.fetch(new Request('https://inspector.example/v1/sessions/example/complete',{method}),env,{waitUntil:p => waiting.push(p)});
    await Promise.all(waiting);
    assert.equal(response.status,status);
    assert.equal(processed,expected);
  });
}
