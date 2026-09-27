# How Recall works

Sep 27, 2026 · @Amil

## What Recall is

Recall remembers what you did on your devices, so you and your AI agents can ask about it later. You pick the apps; Recall reads their text on your PC (no screenshots, no keystrokes), removes secrets, and sends it to your own private cloud memory. You then ask in plain words from any device, for example "What did I do on my laptop yesterday?", or let any MCP client (Claude Code, ChatGPT, Cursor) read it.

## How it captures data

Recall does nothing until a tracked app changes; then it reads that window's text once, keeps only lines it hasn't seen, and indexes them locally. Separately, it groups activity into episodes, strips secrets, and uploads them for the cloud to understand. Only redacted text leaves your PC, and all AI runs in the cloud.

```
YOUR PC (one low-priority Python process, no AI models)
  Wait for a change  ->  Read the text        ->  Keep new lines        ->  Local search
  Windows events,        UI Automation, once      lines not seen before     SQLite + FTS5, offline,
  no polling             text is quiet 1.5 s;     in that window or file    Ctrl+Shift+Space
                         VS Code: extension               |
                                                          v
  Outbox and upload  <-  Remove secrets       <-  Cut into episodes
  gzip, every 60 s,      keys, passwords,         on a task change, 5 idle min,
  kept while offline     emails, phones           20 min cap, or Ctrl+Alt+Esc
        |
        |  redacted, gzipped
        v
YOUR PRIVATE CLOUD (Supabase, locked to your account)
  Ingest             ->  Understand           ->  Memory                ->  Read it back
  Edge Function,         Groq gpt-oss-120b,       episodes, projects,       dashboard Ask, search
  queued (pgmq)          gte-small vectors        digests; raw text 24 h    window, MCP for agents
```

- **Events, not polling.** A Windows foreground hook says when a tracked app is in front; only then does Recall hook that one app's accessibility events. Untracked apps are never read.
- **Text, not pixels.** UI Automation reads the window's text 1.5 s after it stops changing (at least every 4 s while it keeps changing). VS Code hides editor text from UI Automation, so a small extension posts the visible lines over localhost.
- **Episodes without models.** A change detector over word vectors in 30-s windows cuts an episode when the task changes for about a minute, after 5 idle minutes, at 20 minutes, or at once with Ctrl+Alt+Esc.

## Performance overhead at each stage

Recall is close to free while you work: in a 30-minute run of light use (31 text reads) the whole process averaged 0.01% of total CPU, peaked at 0.59%, and used 128–138 MB of RAM. Every stage runs only when something changes, on a below-normal-priority thread, and the heavy work (the LLM and embeddings) happens in the cloud.

| Stage | Runs when | Cost on the PC per run | Delay it adds |
| --- | --- | --- | --- |
| Wait for a change | a tracked app is in front and changes | about 0: Windows delivers the event, nothing polls | none |
| Read the text (UI Automation) | text has been quiet 1.5 s; at least every 4 s while it keeps changing | Chrome 23 ms median, 44 ms p95 (119 reads); VS Code 55 ms median, 376 ms p95 (141 reads, long chat panels) | 1.5 s, by design |
| Keep new lines, local index | every read | 1.1 ms per event (SQLite FTS5 write) | none: searchable about 1.5 s after the text appears |
| Cut into episodes | every event | 3.1 ms per 10-line event (word vectors, change detector) | ends on a task change, 5 idle min, or the send key |
| Remove secrets | once per episode | 1.5 ms for a full 12,000-character episode | none |
| gzip, outbox, upload | every 60 s | 0.2 ms to gzip an episode; 3.9x smaller on real captures | up to 60 s; 1.5 s with the send key |
| Understand and embed | per episode, in the cloud | 0 | about 2.7 s after upload |

End to end after the send key (automated demo run, Edge and VS Code): uploaded at 1.5 s, understood at 4.2 s, found by search at 5.1 s, by MCP at 6.2 s, answered by Ask at 8.3 s.

Sources: read times from Recall's own log (Sep 26–27); per-stage costs from timing Recall's code on synthetic input on this PC; CPU and RAM from `scripts/perf_monitor.py`; delays from `scripts/demo_check.py`. An earlier short capture test measured at most 0.7% of one core and 56 MB.

## Reading it back, and privacy

Your memory is a remote MCP server that any MCP client can use (Claude Code, ChatGPT, Cursor and others). Agents sign in with OAuth, you approve them on a consent page, and they get read-only tools: `get_timeline`, `search_memory`, `get_episode`, `list_threads`, `get_thread` and `daily_digest`. The dashboard and the `Ctrl+Shift+Space` search window read the same memory.

- **Allowlist only.** Apps you haven't ticked are never read.
- **No keystrokes, screenshots or audio stored.**
- **Secrets removed on your PC.** The map from placeholders back to values never leaves it.
- **Raw text is short-lived.** The cloud deletes it 24 hours after it's understood; only the memory stays.
- **Private to you.** Every table has row-level security, and agents are read-only.
