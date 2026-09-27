// HTTP details of serving MCP to clients that differ from each other.

// Clients should accept both JSON and event streams, but some accept only JSON (or send no Accept at all):
// rather than refuse them, answer in plain JSON.
export async function lenient(req: Request, serve: (req: Request) => Promise<Response>): Promise<Response> {
  const accept = req.headers.get('accept') ?? ''
  if (req.method !== 'POST' || (accept.includes('application/json') && accept.includes('text/event-stream'))) return serve(req)
  const headers = new Headers(req.headers)
  headers.set('accept', 'application/json, text/event-stream')
  const res = await serve(new Request(req, { headers }))
  if (accept.includes('text/event-stream') || !res.headers.get('content-type')?.startsWith('text/event-stream')) return res
  const messages = (await res.text()).split(/\r?\n\r?\n/)
    .map((event) => event.split(/\r?\n/).filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trimStart()).join('\n'))
    .filter(Boolean)
  const out = new Headers(res.headers)
  out.set('content-type', 'application/json')
  out.delete('content-length')
  return new Response(messages.length === 1 ? messages[0] : `[${messages.join(',')}]`, { status: res.status, headers: out })
}
