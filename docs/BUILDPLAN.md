# Recall — Build plan

Sep 27, 2026 · @Amil

## What Recall is

A Windows tray app that remembers what you do in the apps you choose, so you and your AI agents can ask about it later. Text is read on the PC with no AI models, cleaned of secrets, and understood in the cloud by a free LLM. How to set it up and use it: [README](../README.md).

## Status

| Goal | Status |
| --- | --- |
| Read only the apps you choose; the rest cost nothing | Done |
| Text searchable on the PC within 5 s | Done: about 1.5 s in Chrome, VS Code and a native editor |
| Secrets removed on the PC before upload | Done |
| Episodes understood in the cloud within 2 min | Done: 6–58 s on the timer, about 4 s with the send key |
| Search by meaning across devices | Done: 20/20 paraphrased questions in the top 3 |
| Ask anything across devices, with follow-ups | Done: 14/14 on the Ask eval |
| AI agents read your memory over MCP, and no one else's | Done: any MCP client with OAuth (tested with Claude Code and the official Python SDK), or the `mcp-remote` bridge |
| Near-zero overhead | So far at most 0.7% of one core and 56 MB while capturing; a 30-minute run while working is still to do |
| Demo runs end to end, twice in a row | The automated run passes; live rehearsal still to do |
| Speech from tracked apps searchable within 30 s | Not started (after the demo) |

## Still to do

1. A 30-minute performance run while working (budget: under 3% CPU while capturing).
2. Five minutes of real use, then find it.
3. Rehearse the demo three times on the demo account; record a 60-second backup video.
4. **Gate M8:** the demo runs twice in a row with no restarts; tag `v1.0-demo`.
5. After the demo, audio: per-app sound capture, speech to text in the cloud. Gate: 8 of 10 key phrases from a 2-minute clip searchable within 30 s, and silence costs under 1% CPU.

## Key decisions

- One low-priority Python process on the PC, with no AI models: capture, rules and a local keyword index only.
- Cloud: Supabase (Postgres with vector search, queues, scheduled jobs, Edge Functions, and Auth as the OAuth server for agents).
- Understanding: Groq's free tier (`openai/gpt-oss-120b`), Cerebras as a fallback. Embeddings: Supabase's built-in gte-small.
- Agents: read-only MCP tools. Each agent registers itself and you approve it on Recall's consent page. Clients without OAuth use the `mcp-remote` bridge; there are no API keys.
- Privacy: allowlist only, no keystrokes or screenshots, secrets removed before upload, raw text deleted 24 hours after it's understood.

## Results (Sep 27)

- **Capture:** a line on screen is captured in 1.4–1.6 s. What you did just before switching away is read at once. Windows that never go quiet are read every 4 s. Long VS Code chats are read from their end.
- **Memory accuracy** (`scripts/eval_memory.py`, 12 realistic sessions through the real pipeline): segmentation 12/12, facts 24/24, false claims avoided 7/7, importance 14/14, secrets kept out 2/2, Ask 8/8, citations 7/7, evidence 12/12.
- **Ask** (`scripts/eval_ask.py`, 14 questions over a two-device memory): 14/14. For "today" or "this week" it reads everything in the period, for "how long" the time in each app, for "on my laptop" only that device, for "what should I follow up on" the recorded deadlines, and for a follow-up the conversation so far. The old Ask failed "What did I do on my laptop yesterday?" by listing desktop work.
- **Cloud and agents:** sign in from the tray, no terminal or restart. A revoked session keeps unsent memory instead of dropping it. Agents get local times and the user's timezone. MCP works for any client: browser clients, JSON-only clients, and protocol versions 2024-11-05 to 2025-11-25. A rate limit that clears in seconds is waited out instead of failing.
- **Dashboard:** Ask is a conversation. Every memory shows its device, and devices can be renamed. Projects open to their memories. Checked in light, dark and phone layouts: 9/9 flows.

## Demo script (2 min)

**Before:** Recall signed in to the demo account and not paused, with Edge and VS Code tracked. The article is open in Edge and a small project in VS Code. Claude Code is connected over MCP and the dashboard is signed in. A couple of minutes before you start, press send once so the demo's episode holds only the demo. Keep the Claude Code panel closed until step 5: Recall reads that chat too, and a long one would take over the summary.

1. Tray and app picker: Edge and VS Code are tracked, Spotify isn't. Untracked apps are never read. (10 s)
2. Read the article in Edge, then write a few lines in VS Code. Each line is in Recall's memory on the PC about 1.5 s after it appears. (35 s)
3. Press `Ctrl+Alt+Esc`: "Sent 1 episode to the cloud". The cloud has understood it about 4 s later. (10 s)
4. Press the search key, then search a paraphrase ("how do I stop handling the same event twice"): the episode, with its evidence on the dashboard. Or ask on the dashboard and get an answer citing the episode in about 2 s. (25 s)
5. Claude Code over MCP: "What was I working on in the last 10 minutes?" (25 s)
6. Close: allowlist, no keystrokes or screenshots, secrets removed on the PC, memory private to you. Recall stays under 1% of one CPU core while it captures. (15 s)

Measured (automated, Edge and VS Code, throwaway account): send, uploaded 1.5 s later, understood at 4.2 s, found by search at 5.1 s, by MCP at 6.2 s, and answered by Ask at 8.3 s; 62 s including a minute of work.

**On stage:** press send once. If Ask ever says it's busy, ask again after 10 seconds.

**If time runs short, cut in this order:** Ask (keep search), then projects and digests. Never cut the allowlist, text capture, secret removal, search, or MCP.

## Testing

- Every commit: `pytest -q` (about 8 s) and `npx deno test supabase/functions/tests`.
- Before a demo or release: `pytest -q --integration`, `pytest -q --cloud` and `scripts/eval_ask.py`, then push and tag.
- Deploy the cloud with `python scripts/deploy_cloud.py` (safe to re-run), and the dashboard with `vercel deploy --prod` in `web/`.
- Budgets: idle CPU under 0.5%, capture under 3%, RAM under 600 MB, text searchable under 5 s, episode understood under 2 min.
