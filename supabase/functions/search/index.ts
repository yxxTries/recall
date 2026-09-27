// Search by meaning across all of the user's devices (used by Recall's search window).
// Time words in the query ("yesterday", "this afternoon"...) limit it when no range is given.
import { withSupabase } from 'npm:@supabase/server@^1.8.0'
import { timeRange } from '../_shared/ask.ts'
import { search, utcOffset } from '../_shared/memory.ts'

export default {
  fetch: withSupabase({ auth: 'user' }, async (req, { supabase }) => {
    const { query, since, until, k } = await req.json().catch(() => ({}))
    if (typeof query !== 'string' || !query.trim()) return Response.json({ error: 'query is required' }, { status: 400 })
    const period = since || until ? { since, until } : timeRange(query, new Date(), await utcOffset(supabase))
    return Response.json({ results: await search(supabase, query, period?.since, period?.until, Math.min(Number(k) || 10, 50)) })
  }),
}
