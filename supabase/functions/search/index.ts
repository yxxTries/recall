// Search by meaning across all of the user's devices (used by Recall's search window).
import { withSupabase } from 'npm:@supabase/server@^1.8.0'
import { search } from '../_shared/memory.ts'

export default {
  fetch: withSupabase({ auth: 'user' }, async (req, { supabase }) => {
    const { query, since, until, k } = await req.json().catch(() => ({}))
    if (typeof query !== 'string' || !query.trim()) return Response.json({ error: 'query is required' }, { status: 400 })
    return Response.json({ results: await search(supabase, query, since, until, Math.min(Number(k) || 10, 50)) })
  }),
}
