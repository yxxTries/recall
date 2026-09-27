// Answers a question about the user's memory in plain words, citing the episodes it used (the dashboard's Ask box).
// Retrieval runs on the caller's RLS-scoped client, so only their own episodes can reach the model.
// It reads memory the way the question needs: the most relevant episodes; for a period ("today", "this week")
// everything in it and the time spent in each app; decisions and deadlines recorded; the user's devices by name;
// their ongoing projects; and the
// conversation so far, so a follow-up ("who was in that call?") keeps its context.
import { withSupabase } from 'npm:@supabase/server@^1.8.0'
import { ANSWER_SCHEMA, ASK_SYSTEM, askPrompt, type Found, mentionedDevice, parseAnswer, timeRange } from '../_shared/ask.ts'
import { complete, LlmError, providers } from '../_shared/llm.ts'
import { deviceNames, importantPoints, periodEpisodes, search, threads, timeByApp, utcOffset } from '../_shared/memory.ts'

const K = 8
const WEEK = 7 * 86_400_000

type Turn = { question: string; answer: string }

export default {
  fetch: withSupabase({ auth: 'user' }, async (req, { supabase }) => {
    const { question, since, until, utc_offset_minutes, history } = await req.json().catch(() => ({}))
    if (typeof question !== 'string' || !question.trim() || question.length > 500) {
      return Response.json({ error: 'Ask a question of up to 500 characters' }, { status: 400 })
    }
    const turns: Turn[] = (Array.isArray(history) ? history : [])
      .filter((t) => typeof t?.question === 'string' && typeof t?.answer === 'string').slice(-3)
      .map((t) => ({ question: t.question.slice(0, 500), answer: t.answer.slice(0, 1500) }))
    const previous = turns.at(-1)?.question
    const [devices, offset] = await Promise.all([
      deviceNames(supabase),
      utc_offset_minutes === undefined ? utcOffset(supabase) : Math.round(Number(utc_offset_minutes) || 0),
    ])
    const now = new Date()
    // A range picked on the dashboard wins; otherwise "today", "this afternoon"... in the question (or the one before).
    const asked = since || until
      ? { since, until: until ?? now.toISOString() }
      : timeRange(question, now, offset) ?? (previous ? timeRange(previous, now, offset) : null)
    const device = mentionedDevice(question, devices) ?? (previous ? mentionedDevice(previous, devices) : null)
    const query = previous ? `${previous} ${question}` : question
    const whole = asked?.since && asked?.until
    const [found, overview, appTime, points, recent] = await Promise.all([
      search(supabase, query, asked?.since, asked?.until, device ? 30 : K) as Promise<Found[]>,
      whole ? periodEpisodes(supabase, asked.since, asked.until, device) : [],
      whole ? timeByApp(supabase, asked.since, asked.until, device) : [],
      importantPoints(supabase, asked?.since ?? new Date(now.getTime() - WEEK).toISOString(), asked?.until, device),
      threads(supabase, undefined, 8),
    ])
    const episodes = (device ? found.filter((e) => e.device_id === device) : found).slice(0, K)
    const period = asked ? { ...asked, device: device ? devices[device] : null } : device ? { device: devices[device] } : null
    if (!episodes.length && !overview.length) {
      const answer = asked ? 'Nothing in your memory from that time matches.' : 'Nothing in your memory matches that yet.'
      return Response.json({ answer, cited: [], episodes: [], period })
    }
    const seen = new Set<string>() // every episode the model sees, once: the answer may cite any of them
    const shown = [...episodes, ...(overview as { episode_id: string }[]), ...points]
      .filter((e) => !seen.has(e.episode_id) && seen.add(e.episode_id))
    const messages = [
      { role: 'system', content: ASK_SYSTEM },
      {
        role: 'user',
        content: askPrompt(question.trim(), episodes, now.toISOString(), offset, asked,
          { devices, device, overview: overview as never, appTime, points, threads: recent as never, history: turns }),
      },
    ]
    try {
      const reply = await complete(providers(), messages, ANSWER_SCHEMA)
      const { answer, cited } = parseAnswer(reply.content, shown)
      return Response.json({ answer, cited, episodes: shown, period })
    } catch (e) {
      const busy = e instanceof LlmError && e.retryable
      console.error('ask failed', e)
      return Response.json(
        { error: busy ? 'The answer service is busy. Try again in a minute.' : 'Could not answer that question.', episodes: shown, period },
        { status: busy ? 503 : 502 },
      )
    }
  }),
}
