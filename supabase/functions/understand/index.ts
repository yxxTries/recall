// Understands queued raw episodes, one Groq call each: structured memory, an embedding and a thread.
// Called every minute by pg_cron with the secret key. A job that fails stays in the queue and comes
// back after its visibility timeout; after MAX_ATTEMPTS the episode keeps a rules-only memory.
import { withSupabase } from 'npm:@supabase/server@^1.8.0'
import type { SupabaseClient } from 'npm:@supabase/supabase-js@^2.117.0'
import { embed, vector } from '../_shared/embed.ts'
import { complete, LlmError, providers } from '../_shared/llm.ts'
import {
  type Candidate, DIGEST_SCHEMA, digestPrompt, EPISODE_SCHEMA, fallback, parse, prompt, type Span, SYSTEM, type Understood,
} from '../_shared/understanding.ts'

const MAX_ATTEMPTS = 4
const BUDGET_MS = 50_000
const MIN_TOKENS_LEFT = 5_000 // Groq's free tier allows 8K tokens per minute; one episode uses up to ~5K
const THREAD_WINDOW_DAYS = 14

type Job = { user_id: string; episode_id: string }

async function understand(db: SupabaseClient, job: Job, attempt: number) {
  const { user_id: user, episode_id: id } = job
  const { data: raw } = await db.from('raw_episodes').select('*').eq('user_id', user).eq('episode_id', id).maybeSingle()
  if (!raw) return { status: 'gone', remaining: null }
  const { data: spanRows } = await db.from('timeline_spans').select('app, title, url, started, ended')
    .eq('user_id', user).eq('episode_id', id).order('started')
  const spans = (spanRows ?? []) as Span[]
  const { data: previous } = await db.from('episodes').select('worked_on, context, ended, thread_id')
    .eq('user_id', user).lt('started', raw.started).order('started', { ascending: false }).limit(1).maybeSingle()

  const draft = await embed(`${spans.map((s) => s.title).join('. ')}\n${raw.text}`)
  const since = new Date(Date.parse(raw.started) - THREAD_WINDOW_DAYS * 86_400_000).toISOString()
  const { data: candidateRows } = await db.rpc('similar_threads', { owner: user, query: vector(draft), since, k: 3 })
  const candidates = (candidateRows ?? []) as Candidate[]

  let memory: Understood
  let provider = 'rules'
  let remaining: number | null = null
  if (attempt > MAX_ATTEMPTS) {
    memory = fallback(spans)
  } else {
    const messages = [{ role: 'system', content: SYSTEM }, { role: 'user', content: prompt(raw, spans, previous, candidates) }]
    let reply = await complete(providers(), messages, EPISODE_SCHEMA)
    try {
      memory = parse(reply.content, candidates)
    } catch (e) { // one retry, telling the model what was wrong
      messages.push({ role: 'assistant', content: reply.content },
        { role: 'user', content: `That was not valid (${(e as Error).message}). Answer again with JSON matching the schema.` })
      reply = await complete(providers(), messages, EPISODE_SCHEMA)
      memory = parse(reply.content, candidates)
    }
    provider = reply.provider
    remaining = reply.remainingTokens
  }

  const embedding = await embed(`${memory.worked_on} ${memory.context} ${memory.topics.join(' ')}`)
  const threadId = await joinThread(db, user, raw, memory, embedding, previous?.thread_id ?? null)
  const { error } = await db.from('episodes').upsert({
    user_id: user, episode_id: id, device_id: raw.device_id, started: raw.started, ended: raw.ended,
    apps: [...new Set(spans.map((s) => s.app))], worked_on: memory.worked_on, context: memory.context,
    actions: memory.actions, important: memory.important, topics: memory.topics, people: memory.people,
    importance: memory.importance, continues_previous: memory.continues_previous, evidence: memory.evidence,
    thread_id: threadId, embedding: vector(embedding),
  })
  if (error) throw error
  await db.from('raw_episodes').update({ understood: new Date().toISOString() }).eq('user_id', user).eq('episode_id', id)
  return { status: 'understood', provider, remaining, worked_on: memory.worked_on }
}

// The model picks a candidate thread (found by similarity) or continues the previous episode's; otherwise a new one.
async function joinThread(
  db: SupabaseClient, user: string, raw: { started: string; ended: string }, memory: Understood,
  embedding: number[], previousThread: number | null,
): Promise<number> {
  const id = memory.thread || (memory.continues_previous ? previousThread : null)
  if (id) {
    const { data: thread } = await db.from('threads').select('id, started, ended, episodes, embedding')
      .eq('user_id', user).eq('id', id).maybeSingle()
    if (thread) {
      const old: number[] = thread.embedding ? JSON.parse(thread.embedding) : embedding
      const mean = old.map((x, i) => x * thread.episodes + embedding[i])
      const norm = Math.hypot(...mean) || 1
      await db.from('threads').update({
        started: raw.started < thread.started ? raw.started : thread.started,
        ended: raw.ended > thread.ended ? raw.ended : thread.ended,
        episodes: thread.episodes + 1,
        embedding: vector(mean.map((x) => x / norm)),
      }).eq('id', id)
      return id
    }
  }
  const { data, error } = await db.from('threads').insert({
    user_id: user, title: memory.thread_title, summary: memory.worked_on, started: raw.started, ended: raw.ended,
    embedding: vector(embedding),
  }).select('id').single()
  if (error) throw error
  return data.id
}

// Yesterday's digest for users whose day is over (after 3 am their time); called hourly.
async function digests(db: SupabaseClient) {
  const { data: due, error } = await db.rpc('digests_due', { k: 3 })
  if (error) throw error
  const results = []
  for (const { user_id, day, utc_offset_minutes, day_start } of due ?? []) {
    const until = new Date(Date.parse(day_start) + 86_400_000).toISOString()
    const { data: episodes } = await db.from('episodes').select('started, ended, worked_on, important, importance')
      .eq('user_id', user_id).gte('started', day_start).lt('started', until).order('started').limit(80)
    const reply = await complete(providers(), [{ role: 'system', content: SYSTEM },
      { role: 'user', content: digestPrompt(day, episodes ?? [], utc_offset_minutes) }], DIGEST_SCHEMA)
    const digest = JSON.parse(reply.content)
    const { error: saveError } = await db.from('digests').upsert({
      user_id, day, summary: String(digest.summary), highlights: (digest.highlights ?? []).slice(0, 6).map(String),
    })
    if (saveError) throw saveError
    results.push({ day, episodes: episodes?.length ?? 0 })
  }
  return results
}

export default {
  fetch: withSupabase({ auth: 'secret' }, async (req, { supabaseAdmin: db }) => {
    const { task } = await req.json().catch(() => ({}))
    if (task === 'digests') return Response.json({ digests: await digests(db) })
    const start = Date.now()
    const results: unknown[] = []
    while (Date.now() - start < BUDGET_MS) {
      const { data: jobs, error } = await db.rpc('understand_next', { visibility_seconds: 120 })
      if (error) throw error
      if (!jobs?.length) break
      const { msg_id, read_ct, message } = jobs[0]
      try {
        const result = await understand(db, message as Job, read_ct)
        await db.rpc('understand_done', { id: msg_id })
        results.push({ episode_id: message.episode_id, ...result })
        if (result.remaining !== null && result.remaining < MIN_TOKENS_LEFT) break
      } catch (e) {
        console.error('understanding failed', message.episode_id, `attempt ${read_ct}`, e)
        results.push({ episode_id: message.episode_id, status: 'retry later', error: String(e) })
        if (e instanceof LlmError && e.retryable) break // rate-limited: wait for the next minute
      }
    }
    return Response.json({ results })
  }),
}
