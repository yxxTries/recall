# Recall — Hackathon Build Plan

Sep 27, 2026 · @Amil

## What Recall is

A Windows tray app that remembers what you were doing in the apps you choose. The device captures text (and later audio) with no models, splits it into episodes, redacts secrets and uploads it. In the cloud, a free LLM on Groq turns each episode into memory: what you worked on, what you did and what mattered. You find it by meaning from Recall's search window on any device, or an AI agent asks for it over MCP.

**Demo:** work in VS Code and read an article, press the hotkey, search a paraphrase and get the episode back. Finale: Claude Code, connected over MCP, answers "what was I working on this afternoon?" from your timeline.

## Core objectives

| Objective | Status |
| --- | --- |
| Capture only tracked apps; untracked apps cost nothing | Done |
| Text from tracked apps (UIA, VS Code extension) searchable on the device within 5 s | Done (1.4–1.9 s live in Chrome, VS Code and a native editor; streaming pages under 4 s) |
| Secrets and PII redacted on the device before upload | Done |
| Episodes understood in the cloud within 2 min | Done (6–58 s; under 5 s with the send hotkey) |
| Search by meaning across devices | Done (golden queries 20/20 in top 3) |
| AI agents read your memory over MCP; other users see nothing | Done (Claude Code on your account, Sep 27) |
| Speech from tracked apps searchable within 30 s | Not started (Phase 5, after the MVP) |
| Near-zero idle CPU; capture never lags the tracked app | Live multi-app run: at most 0.7% of one core (0.04% of the PC), 56 MB, no extra load on Chrome; 30-min run while working still to do |
| Demo runs end to end, twice in a row | Automated run (Sep 27): a minute of work, send, understood 4.3 s later, found by search and MCP 2 s after that; live rehearsal still to do |

## How it works

```
DEVICE (one low-priority process, no models)
[Foreground watcher] -> [Text: UIA + VS Code extension] -> [Activity records] -> [Local keyword search, offline]
                                                         -> [Episodes (CUSUM)] -> [Dedupe] -> [Redact] -> [gzip outbox, every 60 s]
CLOUD (Supabase, private per user)
[ingest] -> [raw episodes, kept 24 h] -> [understand: Groq + gte-small] -> [episodes, timeline, threads, digests]
                                                                         -> [search]  -> Recall "All devices"
                                                                         -> [mcp]     -> AI agents (OAuth + consent on localhost:8766)
                                                                         -> [ask]     -> Dashboard on Vercel (any device)
```

**Key decisions**

- Python 3.10, one process; no models on the device.
- Cloud: Supabase (Postgres + pgvector, pgmq, pg_cron, Edge Functions, Auth as the OAuth server).
- Understanding: Groq free tier (`openai/gpt-oss-120b`), Cerebras as fallback. Embeddings: Supabase's gte-small.
- MCP: read-only tools; agents register themselves and you approve each one on Recall's consent page.
- Privacy: allowlist only, no keystroke logging, redaction before upload, raw text deleted 24 h after it's understood (to confirm), no raw audio or screenshots stored.

## Remaining work

**1. Close Gate M7 (cloud + MCP)**

- [x] Sign up and log in: `python -m recall.sync.cloud signup`, then `login`; restart Recall
- [x] Connect Claude Code: `claude mcp add --transport http recall https://mcrzydsnovhpevzmcchr.supabase.co/functions/v1/mcp`, authenticate, approve
- [x] Claude Code answers "what was I working on between X and Y?" with evidence (01:29 episode, via `get_timeline`)
- [x] Send hotkey for the demo: `Ctrl+Alt+Esc` (Ctrl+Shift+Esc is Task Manager's) closes the open episode and uploads it; the upload starts understanding at once. Key press to understood episode: under 5 s
- [x] Push and tag `m7-cloud`
- [x] Dashboard at https://recall-memory-yxxtries.vercel.app (`web/`, deployed with the Vercel CLI): sign in with your Recall account from any device, see everything stored, and **Ask** in plain words (`ask` function: meaning search, then Groq answers citing the episodes)
- [x] Seamless cloud lookup (Sep 27): sign in, create an account or sign out from the tray (Recall's page on localhost:8766, no terminal or restart); a revoked session keeps the outbox instead of dropping it; MCP gives agents local times, the user's timezone and "today"/"yesterday" in queries ("today" includes work since midnight); `pytest --cloud` 6/6, Deno 17/17

**2. Finish Gate M4 (MVP checks)**

- [ ] 5 minutes of real use, then find it
- [ ] 30-minute perf run within budget: Sep 27 run averaged 0.01% CPU (max 0.59%), RAM max 138 MB, but capture was paused after 4 minutes, so the active-capture budget (under 3%) still needs a run while working (scripted multi-app capture stayed at 0.7% of one core: see Capture speed and overhead)
- [ ] 60-second backup demo video

**Memory accuracy** (`python scripts/eval_memory.py`: 12 realistic capture streams with known answers, through the real pipeline; `--local` checks the device side for free)

- [x] Segmentation: search-then-answer stays one episode (title words count; a lead-in under 2 min joins the next task); reading or watching with no new text isn't idle while the window stays in front; no empty episodes
- [x] Episode text: lines that differ only in numbers or ids keep their first and last; prose outranks commands and JSON when over budget (a Claude Code chat went from 11,216 to 1,340 characters with every prose line kept)
- [x] Chats: the first read of a chat window sends only its latest 80 lines (history isn't what you did just now)
- [x] Understanding: local times in the prompt; evidence grounded to the captured text word for word (a paraphrase becomes the line it paraphrases); people must be named in the text, and AI assistants aren't people
- [x] Ask understands time: "today", "this afternoon", "yesterday", "tonight", "last week" become a local-time range (a day runs from 5 am, so "tonight" at 2 am is the evening before), and the answer is told that range
- [x] Attribution: "You" lines are the user's; any other named author or speaker is someone else, even on a page about their work (a PR review had credited the author's reply to the user)
- [x] 3 more scenarios (docs and code back and forth, a GitHub PR review, a Slack follow-up): 3/3 on every check
- Result (Sep 27): segmentation 12/12, facts 24/24, forbidden claims 7/7, importance 14/14, secrets 2/2, thread linking 1/1, Ask answers 8/8, citations 7/7; evidence 36/38 and people 7/9 before the last two fixes, 12/12 evidence on the re-run

**Capture speed and overhead** (live runs with real Chrome, VS Code and a native editor in a throwaway Recall; each timed line carries its own timestamp)

- [x] What you did just before switching away (a message sent, then alt-tab) is read at once; it waited for your next visit (54–154 s), or was lost if the window closed. Windows only alt-tabbed through still aren't read
- [x] A window that never goes quiet (a streaming answer, a busy chat) is read every 4 s, not 10: median 1.85 s, max 3.8 s
- [x] VS Code chat panels are read from their end. Past 200K characters (a few hours of Claude Code) the newest messages were never captured, and each read blocked VS Code for 107–270 ms; now the newest ~10K characters in calls of a few ms (~265 ms in all)
- [x] A local file in a browser (`C:/Users/me/lease.pdf`) is its own subject; every local page and PDF had merged into one activity called "c"
- [ ] Ask right after a send can answer "busy": understanding and the answer share Groq's 8K tokens a minute, and a 429 isn't retried
- Result (Sep 27): line on screen to captured 1.53 s (Chrome chat), 1.41 s (native editor), 1.61 s (VS Code); Recall at most 0.7% of one core and 56 MB; unit and integration suites pass. Demo path on a throwaway account: send → uploaded 1.5 s → understood 2.8 s later → paraphrase search and MCP `search_memory` hit within 2 s; about 65 s including a minute of work

**3. Phase 8 · Demo (MVP on text capture)**

- [ ] Seed the demo dataset (`scripts/seed_demo.py --cloud` queues it for the account Recall is signed in to: use a separate demo account); rehearse the script 3 times
- [x] README: setup, architecture, privacy, MCP setup
- [ ] Final backup video
- **Gate M8:** the demo runs twice in a row with no restarts; tag `v1.0-demo`

**4. Phase 5 · Audio (last, after the MVP demo)**

- [ ] Per-process loopback for the tracked app (1-hour spike; fallback: system loopback gated on the app's audio session)
- [ ] Loudness gate; speech segments to Groq `whisper-large-v3-turbo`; audio discarded after
- [ ] Transcripts join the pipeline as `source=audio`
- **Gate M5:** 8 of 10 key phrases from a 2-min clip searchable within 30 s; silence costs under 1% CPU

## Testing

- Every commit: `pytest -q` (about 7 s).
- At gates: `pytest -q --integration` (real windows and Node), `pytest -q --cloud` (deployed cloud, throwaway users, a few Groq calls), `npx deno test supabase/functions/tests`, then push and tag.
- Deploy the cloud: `python scripts/deploy_cloud.py` (idempotent).

**Budgets:** idle CPU under 0.5%, active text capture under 3%, RAM under 600 MB, text searchable under 5 s, speech under 30 s, episode understood under 2 min.

## Demo script (3 min)

1. Tray and app picker: Edge, VS Code and Teams tracked, Spotify not. (20 s)
2. Read an article in Edge, edit a file in VS Code (play a talk clip if audio is done). (40 s)
3. `Ctrl+Alt+Esc` to send, then the search hotkey: search a paraphrase on All devices, open the episode and its evidence. (40 s)
4. Perf monitor: CPU near 0% while idle. (20 s)
5. Claude Code over MCP: "what was I working on this afternoon?" (40 s)
6. Privacy close: allowlist, no keystrokes, redaction on the device, memory private to you. (20 s)

**If time runs short, cut in this order:** audio, digests and threads, OAuth for MCP (use one demo token). Never cut: the allowlist, text capture, redaction, cloud episodes with search and MCP, idle efficiency.
