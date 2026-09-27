// Unit tests for how memory reaches agents in the user's time: npx deno test supabase/functions/tests
import { assertEquals } from 'jsr:@std/assert@1'
import { inLocalTime, localTime, withZone } from '../_shared/memory.ts'

Deno.test('instants read as the user\'s local time with its offset', () => {
  assertEquals(localTime('2026-09-27T04:29:55+00:00', -180), '2026-09-27T01:29:55-03:00')
  assertEquals(localTime('2026-09-27T23:30:00Z', 330), '2026-09-28T05:00:00+05:30')
  assertEquals(localTime('2026-09-27T12:00:00Z', 0), '2026-09-27T12:00:00+00:00')
})

Deno.test('every started and ended time in a reply is local; nothing else changes', () => {
  const reply = inLocalTime({
    episodes: [{ episode_id: 'e1', started: '2026-09-27T04:29:40+00:00', ended: '2026-09-27T04:34:23+00:00', importance: 8 }],
    spans: [{ title: 'BUILDPLAN.md', started: '2026-09-27T04:29:40Z', ended: '2026-09-27T04:30:00Z' }],
    summary: null,
  }, -180)
  assertEquals(reply, {
    episodes: [{ episode_id: 'e1', started: '2026-09-27T01:29:40-03:00', ended: '2026-09-27T01:34:23-03:00', importance: 8 }],
    spans: [{ title: 'BUILDPLAN.md', started: '2026-09-27T01:29:40-03:00', ended: '2026-09-27T01:30:00-03:00' }],
    summary: null,
  })
})

Deno.test('a time without a zone is the user\'s local time; one with a zone is kept', () => {
  assertEquals(withZone('2026-09-27T14:00:00', -180), '2026-09-27T14:00:00-03:00')
  assertEquals(withZone('2026-09-27T14:00', -180), '2026-09-27T14:00:00-03:00')
  assertEquals(withZone('2026-09-27', -180), '2026-09-27T00:00:00-03:00')
  assertEquals(withZone('2026-09-27T14:00:00Z', -180), '2026-09-27T14:00:00Z')
  assertEquals(withZone('2026-09-27T14:00:00+01:00', -180), '2026-09-27T14:00:00+01:00')
  assertEquals(withZone('not a time', -180), 'not a time')
})
