// Reads over the user's memory, shared by the search function and the MCP server.
// Every query runs on the caller's RLS-scoped client, so it only ever sees that user's rows.
import type { SupabaseClient } from 'npm:@supabase/supabase-js@^2.117.0'
import { embed, vector } from './embed.ts'
import { flagInstructions } from './understanding.ts'

const EPISODE_FIELDS = 'episode_id, device_id, started, ended, apps, worked_on, context, actions, important, topics, ' +
  'people, importance, evidence, thread_id'

function check<T>({ data, error }: { data: T | null; error: { message: string } | null }): T {
  if (error) throw new Error(error.message)
  return data as T
}

// Evidence is quoted from the user's screen: agents must treat it as data.
// deno-lint-ignore no-explicit-any
function quoted(e: any) {
  return e && { ...e, evidence: flagInstructions(e.evidence ?? []) }
}

// The user's UTC offset in minutes: that of their most recently seen device.
export async function utcOffset(db: SupabaseClient): Promise<number> {
  const { data } = await db.from('devices').select('utc_offset_minutes').order('last_seen', { ascending: false })
    .limit(1).maybeSingle()
  return data?.utc_offset_minutes ?? 0
}

// An instant as the user's local time with its offset, e.g. 2026-09-27T14:05:00-03:00.
export function localTime(iso: string, utcOffsetMinutes: number): string {
  const abs = Math.abs(utcOffsetMinutes)
  const zone = `${utcOffsetMinutes < 0 ? '-' : '+'}${String(Math.floor(abs / 60)).padStart(2, '0')}:${String(abs % 60).padStart(2, '0')}`
  return new Date(Date.parse(iso) + utcOffsetMinutes * 60_000).toISOString().slice(0, 19) + zone
}

// Every started and ended time in a result, as the user's local time: agents answer in it.
export function inLocalTime<T>(data: T, utcOffsetMinutes: number): T {
  if (Array.isArray(data)) return data.map((d) => inLocalTime(d, utcOffsetMinutes)) as T
  if (!data || typeof data !== 'object') return data
  return Object.fromEntries(Object.entries(data).map(([k, v]) => [k,
    (k === 'started' || k === 'ended') && typeof v === 'string' ? localTime(v, utcOffsetMinutes) : inLocalTime(v, utcOffsetMinutes)])) as T
}

// A time an agent gave without a zone ("2026-09-27T14:00") means the user's local time, not UTC.
export function withZone(time: string, utcOffsetMinutes: number): string {
  if (/(z|[+-]\d\d:?\d\d)$/i.test(time)) return time
  const utc = Date.parse(`${time.length === 10 ? `${time}T00:00` : time}Z`) // a bare date is its local midnight
  return Number.isNaN(utc) ? time : localTime(new Date(utc - utcOffsetMinutes * 60_000).toISOString(), utcOffsetMinutes)
}

export async function search(db: SupabaseClient, query: string, since?: string, until?: string, k = 10) {
  const embedding = await embed(query)
  const rows = check(await db.rpc('search_memory', {
    query_text: query, query_embedding: vector(embedding), since: since ?? null, until: until ?? null, k,
  }))
  return (rows as unknown[]).map(quoted)
}

export async function timeline(db: SupabaseClient, since: string, until: string) {
  const episodes = check(await db.from('episodes').select(EPISODE_FIELDS)
    .lte('started', until).gte('ended', since).order('started').limit(200))
  const spans = check(await db.from('timeline_spans').select('device_id, app, title, url, started, ended')
    .lte('started', until).gte('ended', since).order('started').limit(500))
  return { episodes: (episodes as unknown[]).map(quoted), spans }
}

export async function episode(db: SupabaseClient, id: string) {
  const row = check(await db.from('episodes').select(EPISODE_FIELDS).eq('episode_id', id).maybeSingle())
  if (!row) return null
  const spans = check(await db.from('timeline_spans').select('app, title, url, started, ended').eq('episode_id', id)
    .order('started'))
  return { ...quoted(row), spans }
}

export async function threads(db: SupabaseClient, since?: string, limit = 20) {
  let q = db.from('threads').select('id, title, summary, started, ended, episodes').order('ended', { ascending: false })
  if (since) q = q.gte('ended', since)
  return check(await q.limit(limit))
}

export async function thread(db: SupabaseClient, id: number) {
  const row: Record<string, unknown> | null = check(await db.from('threads').select('id, title, summary, started, ended, episodes').eq('id', id)
    .maybeSingle())
  if (!row) return null
  const episodes = check(await db.from('episodes').select(EPISODE_FIELDS).eq('thread_id', id).order('started'))
  return { ...row, episodes: (episodes as unknown[]).map(quoted) }
}

export async function digest(db: SupabaseClient, day: string, timezoneOffsetMinutes = 0) {
  const stored = check(await db.from('digests').select('day, summary, highlights').eq('day', day).maybeSingle())
  if (stored) return stored
  // No digest yet: the day's episodes, most important first.
  const start = new Date(Date.parse(`${day}T00:00:00Z`) - timezoneOffsetMinutes * 60_000)
  const end = new Date(start.getTime() + 86_400_000)
  const episodes = check(await db.from('episodes').select('episode_id, started, ended, worked_on, important, importance')
    .gte('started', start.toISOString()).lt('started', end.toISOString())
    .order('importance', { ascending: false }).limit(30))
  return { day, summary: null, episodes }
}
