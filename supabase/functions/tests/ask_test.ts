// Unit tests for the dashboard's Ask: npx deno test supabase/functions/tests
import { assertEquals, assertStringIncludes, assertThrows } from 'jsr:@std/assert@1'
import { askPrompt, type Found, mentionedDevice, parseAnswer, timeRange } from '../_shared/ask.ts'

const found: Found[] = [{
  episode_id: 'e1', started: '2026-09-27T17:05:00Z', ended: '2026-09-27T17:40:00Z', apps: ['ms-teams.exe'],
  worked_on: 'Picked a hackathon sponsor', context: 'Sponsor sync with Priya and Marco', important: ['Contoso chosen'],
  people: ['Priya', 'Marco'], evidence: ['Marco: then we go with Contoso', 'b', 'c', 'd'],
}]

Deno.test('the prompt shows local times, the episode id and at most 3 evidence lines', () => {
  const text = askPrompt('Which sponsor did we pick?', found, '2026-09-27T18:00:00Z', -180)
  assertStringIncludes(text, 'Now (local time): Sun 2026-09-27 15:00')
  assertStringIncludes(text, 'episode_id: e1')
  assertStringIncludes(text, 'when: Sun 2026-09-27 14:05 to 14:40 (Teams)')
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
  assertEquals(at('What was I working on today?'), { since: '2026-09-27T03:00:00.000Z', until: '2026-09-28T08:00:00.000Z' })
  // At 5:42 am after a late night, today still has the 1 am work; at 2 am, today began yesterday at 5 am.
  assertEquals(timeRange('what was I working on today', new Date('2026-09-27T08:42:00Z'), -180),
    { since: '2026-09-27T03:00:00.000Z', until: '2026-09-28T08:00:00.000Z' })
  // ...and then yesterday ends at midnight rather than counting the small hours twice.
  assertEquals(timeRange('what did I do yesterday', new Date('2026-09-27T08:42:00Z'), -180),
    { since: '2026-09-26T08:00:00.000Z', until: '2026-09-27T03:00:00.000Z' })
  assertEquals(timeRange('what did I do today', new Date('2026-09-27T05:00:00Z'), -180),
    { since: '2026-09-26T08:00:00.000Z', until: '2026-09-27T08:00:00.000Z' })
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

Deno.test('a question that names one device is about that device only', () => {
  const devices = { d1: 'DESKTOP-4F7Q', l1: 'LAPTOP-9XK2', w1: 'Work PC' }
  assertEquals(mentionedDevice('What did I do on my laptop yesterday?', devices), 'l1')
  assertEquals(mentionedDevice('what was I doing on the desktop this afternoon', devices), 'd1')
  assertEquals(mentionedDevice('anything from my work pc?', devices), 'w1')
  assertEquals(mentionedDevice('What did I work on today?', devices), null) // "work" alone isn't a device
  assertEquals(mentionedDevice('what did I do on LAPTOP-9XK2', devices), 'l1')
  assertEquals(mentionedDevice('Which sponsor did we pick?', devices), null)
})

Deno.test('the prompt carries devices, the whole period, time by app, projects and the conversation', () => {
  const text = askPrompt('Who was in that call?', [{ ...found[0], device_id: 'l1' }], '2026-09-27T18:00:00Z', -180,
    { since: '2026-09-27T03:00:00.000Z', until: '2026-09-28T08:00:00.000Z' }, {
      devices: { d1: 'DESKTOP-4F7Q', l1: 'LAPTOP-9XK2' }, device: 'l1',
      overview: [{ episode_id: 'e0', device_id: 'd1', started: '2026-09-27T12:00:00Z', ended: '2026-09-27T12:30:00Z',
        apps: ['code.exe'], worked_on: 'Fixed the tray' }],
      appTime: [{ app: 'code.exe', minutes: 95 }],
      threads: [{ title: 'Hackathon sponsors', episodes: 2, started: '2026-09-26T14:00:00Z', ended: '2026-09-27T17:40:00Z' }],
      history: [{ question: 'What did we decide in the sponsor sync?', answer: 'Contoso.' }],
    })
  assertStringIncludes(text, "The user's devices: DESKTOP-4F7Q, LAPTOP-9XK2. The question is about LAPTOP-9XK2 only.")
  assertStringIncludes(text, 'when: Sun 2026-09-27 14:05 to 14:40 on LAPTOP-9XK2 (Teams)')
  assertStringIncludes(text, '- Sun 2026-09-27 09:00 to 09:30 on DESKTOP-4F7Q (VS Code): Fixed the tray [e0]')
  assertStringIncludes(text, 'Time by app in that period (window in front): VS Code 1 h 35 min')
  assertStringIncludes(text, '- Hackathon sponsors: 2 episodes, Sat 2026-09-26 11:00 to Sun 2026-09-27 14:40')
  assertStringIncludes(text, 'Q: What did we decide in the sponsor sync?\nA: Contoso.\n\nQuestion: Who was in that call?')
})

Deno.test('episode ids the model writes into an answer are taken out; the citations stay', () => {
  const shown = [{ episode_id: 't3' }, { episode_id: 'd1a' }, { episode_id: 'e1' }]
  const reply = parseAnswer(JSON.stringify({
    answer: '- The demo is Sunday at 2:30 pm (t3).\n- Send Dana the forecast by Friday – episode d1a\n' +
      '- Recall work [t3, d1a] and more (e.g., e1)\nThe t3 slot is fixed.',
    cited: ['t3', 'd1a'],
  }), shown)
  assertEquals(reply.answer, '- The demo is Sunday at 2:30 pm.\n- Send Dana the forecast by Friday\n' +
    '- Recall work and more\nThe t3 slot is fixed.')
  assertEquals(reply.cited, ['t3', 'd1a'])
})
