// Unit tests for the dashboard's Ask: npx deno test supabase/functions/tests
import { assertEquals, assertStringIncludes, assertThrows } from 'jsr:@std/assert@1'
import { askPrompt, type Found, parseAnswer, timeRange } from '../_shared/ask.ts'

const found: Found[] = [{
  episode_id: 'e1', started: '2026-09-27T17:05:00Z', ended: '2026-09-27T17:40:00Z', apps: ['ms-teams.exe'],
  worked_on: 'Picked a hackathon sponsor', context: 'Sponsor sync with Priya and Marco', important: ['Contoso chosen'],
  people: ['Priya', 'Marco'], evidence: ['Marco: then we go with Contoso', 'b', 'c', 'd'],
}]

Deno.test('the prompt shows local times, the episode id and at most 3 evidence lines', () => {
  const text = askPrompt('Which sponsor did we pick?', found, '2026-09-27T18:00:00Z', -180)
  assertStringIncludes(text, 'Now (local time): 2026-09-27 15:00')
  assertStringIncludes(text, 'episode_id: e1')
  assertStringIncludes(text, 'when: 2026-09-27 14:05 to 14:40 (ms-teams.exe)')
  assertStringIncludes(text, 'evidence: Marco: then we go with Contoso | b | c\n')
  assertStringIncludes(text, 'Question: Which sponsor did we pick?')
})

Deno.test('citations of episodes the model was not shown are dropped', () => {
  const reply = parseAnswer(JSON.stringify({ answer: ' Contoso. ', cited: ['e1', 'e9', 'e1'] }), found)
  assertEquals(reply, { answer: 'Contoso.', cited: ['e1'] })
  assertThrows(() => parseAnswer('{"answer": 3}', found))
})

Deno.test('time words in a question become a local-time range (a day runs from 5 am)', () => {
  const now = new Date('2026-09-27T18:30:00Z') // 3:30 pm at UTC-3
  const at = (q: string) => timeRange(q, now, -180)
  assertEquals(at('What was I working on today?'), { since: '2026-09-27T08:00:00.000Z', until: '2026-09-28T08:00:00.000Z' })
  assertEquals(at('what did I do this afternoon'), { since: '2026-09-27T15:00:00.000Z', until: '2026-09-27T21:00:00.000Z' })
  assertEquals(at('What did I read yesterday morning?'), { since: '2026-09-26T08:00:00.000Z', until: '2026-09-26T15:00:00.000Z' })
  assertEquals(at('which sponsor did we pick?'), null)
  // At 2 am, "tonight" is the evening that began yesterday: 5 pm to 5 am local.
  assertEquals(timeRange('what was I doing tonight?', new Date('2026-09-27T05:00:00Z'), -180),
    { since: '2026-09-26T20:00:00.000Z', until: '2026-09-27T08:00:00.000Z' })
})

Deno.test('the prompt names the time range retrieval used', () => {
  const text = askPrompt('What did I do yesterday afternoon?', found, '2026-09-27T05:40:00Z', -180,
    { since: '2026-09-25T15:00:00.000Z', until: '2026-09-25T21:00:00.000Z' })
  assertStringIncludes(text, 'The question is about 2026-09-25 12:00 to 2026-09-25 18:00 (local time)')
})
