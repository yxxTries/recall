// Unit tests for the dashboard's Ask: npx deno test supabase/functions/tests
import { assertEquals, assertStringIncludes, assertThrows } from 'jsr:@std/assert@1'
import { askPrompt, type Found, parseAnswer } from '../_shared/ask.ts'

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
