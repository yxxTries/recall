// Unit tests for how the MCP server meets clients that differ: npx deno test supabase/functions/tests
import { assertEquals } from 'jsr:@std/assert@1'
import { lenient } from '../_shared/transport.ts'

const sse = (...messages: object[]) =>
  new Response(messages.map((m) => `event: message\ndata: ${JSON.stringify(m)}\n\n`).join(''),
    { headers: { 'content-type': 'text/event-stream' } })
const post = (accept?: string) =>
  new Request('https://x/mcp', { method: 'POST', body: '{}', headers: accept ? { accept } : {} })

Deno.test('a client that takes only JSON (or says nothing) gets plain JSON, not a 406', async () => {
  for (const accept of ['application/json', undefined]) {
    const seen: string[] = []
    const res = await lenient(post(accept), (req) => {
      seen.push(req.headers.get('accept')!)
      return Promise.resolve(sse({ jsonrpc: '2.0', id: 1, result: { tools: [] } }))
    })
    assertEquals(seen, ['application/json, text/event-stream']) // what the SDK insists on
    assertEquals(res.headers.get('content-type'), 'application/json')
    assertEquals(await res.json(), { jsonrpc: '2.0', id: 1, result: { tools: [] } })
  }
})

Deno.test('a batch answered as several events becomes one JSON array', async () => {
  const res = await lenient(post('application/json'), () => Promise.resolve(sse({ id: 1 }, { id: 2 })))
  assertEquals(await res.json(), [{ id: 1 }, { id: 2 }])
})

Deno.test('a client that accepts event streams is served untouched', async () => {
  const original = sse({ id: 1 })
  const res = await lenient(post('application/json, text/event-stream'), () => Promise.resolve(original))
  assertEquals(res, original)
})
