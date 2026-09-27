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

export async function complete(
  list: Provider[],
  messages: { role: string; content: string }[],
  schema: Record<string, unknown>,
  fetcher: typeof fetch = fetch,
): Promise<Completion> {
  let last: LlmError = new LlmError('no LLM provider configured', false)
  for (const p of list) {
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
    const text = await res.text()
    last = new LlmError(`${p.name} ${res.status}: ${text.slice(0, 300)}`, res.status === 429 || res.status >= 500)
    if (!last.retryable) break // a bad request fails the same way everywhere
  }
  throw last
}
