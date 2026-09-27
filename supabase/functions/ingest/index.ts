// Takes a device's batch of redacted episodes (gzip JSON) as the signed-in user; row-level security applies.
// Re-sent batches are harmless: episodes and spans already stored are skipped.
import { withSupabase } from 'npm:@supabase/server@^1.8.0'

type Batch = {
  device_id: string
  device_name?: string
  episodes: { episode_id: string; started: string; ended: string; text: string; spans: Record<string, string>[] }[]
}

const MAX_EPISODES = 200

export default {
  fetch: withSupabase({ auth: 'user' }, async (req, { supabase }) => {
    if (req.method !== 'POST' || !req.body) return Response.json({ error: 'POST a batch' }, { status: 405 })
    const body = req.headers.get('x-recall-encoding') === 'gzip'
      ? req.body.pipeThrough(new DecompressionStream('gzip'))
      : req.body
    let batch: Batch
    try {
      batch = await new Response(body).json()
    } catch {
      return Response.json({ error: 'body is not JSON' }, { status: 400 })
    }
    if (typeof batch.device_id !== 'string' || !Array.isArray(batch.episodes) || batch.episodes.length > MAX_EPISODES) {
      return Response.json({ error: 'expected {device_id, episodes[]}' }, { status: 400 })
    }
    const device = batch.device_id
    const now = new Date().toISOString()
    const steps = [
      supabase.from('devices').upsert({ device_id: device, name: batch.device_name ?? '', last_seen: now },
        { onConflict: 'user_id,device_id' }),
      supabase.from('raw_episodes').upsert(
        batch.episodes.map((e) => ({ episode_id: e.episode_id, device_id: device, started: e.started, ended: e.ended, text: e.text })),
        { onConflict: 'user_id,episode_id', ignoreDuplicates: true },
      ),
      supabase.from('timeline_spans').upsert(
        batch.episodes.flatMap((e) =>
          e.spans.map((s) => ({
            episode_id: e.episode_id, device_id: device, app: s.app, title: s.title, url: s.url ?? '',
            started: s.started, ended: s.ended,
          }))
        ),
        { onConflict: 'user_id,device_id,app,title,started', ignoreDuplicates: true },
      ),
    ]
    for (const step of steps) {
      const { error } = await step
      if (error) return Response.json({ error: error.message }, { status: 400 })
    }
    return Response.json({ accepted: batch.episodes.length })
  }),
}
