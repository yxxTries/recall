// Chat completions with JSON-schema output from any OpenAI-compatible provider (base URL + key).
// Groq's free tier first; Cerebras's free tier when Groq is rate-limited or down.

export type Provider = { name: string; baseUrl: string; key: string; model: string }

export function providers(env: (name: string) => string | undefined = (n) => Deno.env.get(n)): Provider[] {
  const all = [
    { name: 'groq', baseUrl: 'https://api.groq.com/openai/v1', key: env('GROQ_API_KEY'), model: 'openai/gpt-oss-120b' },
    { name: 'cerebras', baseUrl: 'https://api.cerebras.ai/v1', key: env('CEREBRAS_API_KEY'), model: 'gpt-oss-120b' },
  ]
  return all.filter((p): p is Provider => !!p.key)
}

export type Completion = { content: string; provider: string; remainingTokens: number | null; usage: unknown }

export class LlmError extends Error {
  constructor(message: string, readonly retryable: boolean) {
    super(message)
  }
}

const MAX_WAIT_S = 12 // a per-minute token budget refills within seconds: worth waiting for once

// How long a 429 says to wait, in seconds: Retry-After, else Groq's reset time ("7.66s", "1m2.5s", "450ms").
export function waitFor(res: Response): number | null {
  const after = Number(res.headers.get('retry-after'))
  if (after > 0) return after
  const reset = res.headers.get('x-ratelimit-reset-tokens') ?? res.headers.get('x-ratelimit-reset-requests')
  const parts = reset?.match(/^(?:(\d+)m)?(?:([\d.]+)s)?(?:([\d.]+)ms)?$/)
  if (!reset || !parts || !parts[0]) return null
  return Number(parts[1] ?? 0) * 60 + Number(parts[2] ?? 0) + Number(parts[3] ?? 0) / 1000
}

export async function complete(
  list: Provider[],
  messages: { role: string; content: string }[],
  schema: Record<string, unknown>,
  fetcher: typeof fetch = fetch,
  sleep = (ms: number) => new Promise((r) => setTimeout(r, ms)),
): Promise<Completion> {
  let last: LlmError = new LlmError('no LLM provider configured', false)
  for (const p of list) {
    for (let attempt = 0; attempt < 2; attempt++) {
      const reply = await ask(p, messages, schema, fetcher)
      if (!(reply instanceof Response)) return reply
      const wait = reply.status === 429 && attempt === 0 ? waitFor(reply) : null
      if (wait !== null && wait <= MAX_WAIT_S) { // rate-limited for a few seconds: wait, then the same provider again
        await reply.body?.cancel()
        await sleep(wait * 1000 + 250)
        continue
      }
      const text = await reply.text()
      last = new LlmError(`${p.name} ${reply.status}: ${text.slice(0, 300)}`, reply.status === 429 || reply.status >= 500)
      break
    }
    if (!last.retryable) break // a bad request fails the same way everywhere
  }
  throw last
}

// One call: the completion, or the failed response.
async function ask(
  p: Provider, messages: { role: string; content: string }[], schema: Record<string, unknown>, fetcher: typeof fetch,
): Promise<Completion | Response> {
  const res = await fetcher(`${p.baseUrl}/chat/completions`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${p.key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: p.model,
      messages,
      temperature: 0.2,
      reasoning_effort: 'low',
      max_completion_tokens: 2000,
      response_format: { type: 'json_schema', json_schema: { name: 'memory', strict: true, schema } },
    }),
  }).catch((e) => new Response(String(e), { status: 599 }))
  if (res.ok) {
    const body = await res.json()
    const remaining = res.headers.get('x-ratelimit-remaining-tokens')
    return {
      content: body.choices[0].message.content,
      provider: p.name,
      remainingTokens: remaining === null ? null : Number(remaining),
      usage: body.usage,
    }
  }
  return res
}
