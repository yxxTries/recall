// Answers a question about the user's memory in plain words, citing the episodes it used (the dashboard's Ask box).
// Retrieval runs on the caller's RLS-scoped client, so only their own episodes can reach the model.
import { withSupabase } from 'npm:@supabase/server@^1.8.0'
import { ANSWER_SCHEMA, ASK_SYSTEM, askPrompt, type Found, parseAnswer, timeRange } from '../_shared/ask.ts'
import { complete, LlmError, providers } from '../_shared/llm.ts'
import { search } from '../_shared/memory.ts'

const K = 8

export default {
  fetch: withSupabase({ auth: 'user' }, async (req, { supabase }) => {
    const { question, since, until, utc_offset_minutes } = await req.json().catch(() => ({}))
    if (typeof question !== 'string' || !question.trim() || question.length > 500) {
      return Response.json({ error: 'Ask a question of up to 500 characters' }, { status: 400 })
    }
    const offset = Math.round(Number(utc_offset_minutes) || 0)
    // A range picked on the dashboard wins; otherwise "today", "this afternoon"... in the question set it.
    const asked = since || until ? { since, until } : timeRange(question, new Date(), offset)
    const episodes = (await search(supabase, question, asked?.since, asked?.until, K)) as Found[]
    if (!episodes.length) {
      const answer = asked ? 'Nothing in your memory from that time matches.' : 'Nothing in your memory matches that yet.'
      return Response.json({ answer, cited: [], episodes: [] })
    }
    const messages = [
      { role: 'system', content: ASK_SYSTEM },
      { role: 'user', content: askPrompt(question.trim(), episodes, new Date().toISOString(), offset, asked) },
    ]
    try {
      const reply = await complete(providers(), messages, ANSWER_SCHEMA)
      const { answer, cited } = parseAnswer(reply.content, episodes)
      return Response.json({ answer, cited, episodes })
    } catch (e) {
      const busy = e instanceof LlmError && e.retryable
      console.error('ask failed', e)
      return Response.json(
        { error: busy ? 'The answer service is busy. Try again in a minute.' : 'Could not answer that question.', episodes },
        { status: busy ? 503 : 502 },
      )
    }
  }),
}
