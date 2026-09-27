# Recall — Hackathon Build Plan

Sep 27, 2026 · @Amil

## What Recall is

A Windows tray app that remembers what you were doing in the apps you choose. The device captures text (and later audio) with no models, splits it into episodes, redacts secrets and uploads it. In the cloud, a free LLM on Groq turns each episode into memory: what you worked on, what you did and what mattered. You find it by meaning from Recall's search window on any device, or an AI agent asks for it over MCP.

**Demo:** work in VS Code and read an article, press the hotkey, search a paraphrase and get the episode back. Finale: Claude Code, connected over MCP, answers "what was I working on this afternoon?" from your timeline.

## Core objectives

| Objective | Status |
| --- | --- |
| Capture only tracked apps; untracked apps cost nothing | Done |
| Text from tracked apps (UIA, VS Code extension) searchable on the device within 5 s | Done (1.5 s) |
| Secrets and PII redacted on the device before upload | Done |
| Episodes understood in the cloud within 2 min | Done (6–58 s) |
| Search by meaning across devices | Done (golden queries 20/20 in top 3) |
| AI agents read your memory over MCP; other users see nothing | Done in tests; to verify with Claude Code on your account |
| Speech from tracked apps searchable within 30 s | Not started (Phase 5, after the MVP) |
| Near-zero idle CPU; capture never lags the tracked app | Measured 0.03% CPU; 30-min perf run still to do |
| Demo runs end to end, twice in a row | Not started (Phase 8) |

## How it works

```
DEVICE (one low-priority process, no models)
[Foreground watcher] -> [Text: UIA + VS Code extension] -> [Activity records] -> [Local keyword search, offline]
                                                         -> [Episodes (CUSUM)] -> [Dedupe] -> [Redact] -> [gzip outbox, every 60 s]
CLOUD (Supabase, private per user)
[ingest] -> [raw episodes, kept 24 h] -> [understand: Groq + gte-small] -> [episodes, timeline, threads, digests]
                                                                         -> [search]  -> Recall "All devices"
                                                                         -> [mcp]     -> AI agents (OAuth + consent on localhost:8766)
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
- [ ] Claude Code answers "what was I working on between X and Y?" with evidence
- [ ] Push and tag `m7-cloud`

**2. Finish Gate M4 (MVP checks)**

- [ ] 5 minutes of real use, then find it
- [ ] 30-minute perf run within budget
- [ ] 60-second backup demo video

**3. Phase 8 · Demo (MVP on text capture)**

- [ ] Seed the demo dataset; rehearse the script 3 times
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
3. Hotkey, search a paraphrase, open the episode and its evidence. (40 s)
4. Perf monitor: CPU near 0% while idle. (20 s)
5. Claude Code over MCP: "what was I working on this afternoon?" (40 s)
6. Privacy close: allowlist, no keystrokes, redaction on the device, memory private to you. (20 s)

**If time runs short, cut in this order:** audio, digests and threads, OAuth for MCP (use one demo token). Never cut: the allowlist, text capture, redaction, cloud episodes with search and MCP, idle efficiency.
