# Recall

A Windows tray app that remembers what you were doing in the apps you choose.

Recall watches only the apps on your allowlist. It reads their text on the device (no models, no keystrokes, no screenshots), splits it into episodes, redacts secrets and uploads them. In the cloud, a free LLM turns each episode into memory: what you worked on, what you did and what mattered. You find it by meaning from Recall's search window, or an AI agent such as Claude Code asks for it over MCP.

## How it works

```
DEVICE (one low-priority process, no models)
[Foreground watcher] -> [Text: UI Automation + VS Code extension] -> [Activity records] -> [Local keyword search, offline]
                                                                  -> [Episodes (CUSUM)] -> [Dedupe] -> [Redact] -> [gzip outbox, every 60 s]
CLOUD (Supabase, private per user)
[ingest] -> [raw episodes, kept 24 h] -> [understand: Groq + gte-small] -> [episodes, timeline, threads, digests]
                                                                         -> [search] -> Recall "All devices"
                                                                         -> [mcp]    -> AI agents (OAuth + consent on localhost:8766)
```

- **Device** (`recall/`): Python 3.10, one process. A foreground watcher notices when a tracked app is in front. Text comes from UI Automation, or from the Recall Companion extension for VS Code. Activity lands in a local SQLite store with a keyword index, so search works offline. The same events are cut into episodes when the topic drifts (CUSUM over word and app features), deduplicated (SimHash), redacted and queued in a gzip outbox that uploads every 60 s and survives going offline.
- **Cloud** (`supabase/`): Postgres with pgvector, pgmq and pg_cron, plus four Edge Functions. `ingest` stores raw episodes. `understand` runs every minute and asks Groq (`openai/gpt-oss-120b`, Cerebras as fallback) for a summary, actions, important points and people, then embeds the result with gte-small. `search` serves Recall's "All devices" search. `mcp` is a read-only MCP server for AI agents. Row-level security keeps each user's memory private.

## Setup

Needs Windows 10 or 11 and Python 3.10.

```
py -3.10 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Create `.env` in the repo root with the Supabase project's URL and publishable key:

```
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_ANON_KEY=<publishable key>
```

Sign in once, then start Recall:

```
.venv\Scripts\python -m recall.sync.cloud signup   # once; confirm the email if asked
.venv\Scripts\python -m recall.sync.cloud login
run_recall.bat                                     # tray icon, no console
```

Without signing in, Recall still captures and searches on this device only.

**Pick your apps:** click the tray icon, open **Tracked apps** and tick the apps to remember (for example `code.exe`, `msedge.exe`). Untracked apps are ignored and cost nothing. **Pause** stops all capture.

**Send now:** episodes normally go to the cloud after 5 idle minutes or a change of task. Press `Ctrl+Alt+Esc` (or use **Send to cloud now** in the tray) to send what you're doing right away; it's understood a few seconds later and a notification confirms it. Handy for demos.

**Search:** press `Ctrl+Shift+Space` (or `Win+Alt+Space` if that's taken; the tray menu shows which one). Type what something was about, not the exact words. Choose **All devices** to search your cloud memory.

**VS Code:** install the Recall Companion extension so Recall sees the code you're viewing:

```
cd vscode-extension
npx @vscode/vsce package
code --install-extension recall-companion-0.1.0.vsix
```

It sends the visible lines of the active editor to Recall on `127.0.0.1` only, and only while `code.exe` is tracked. Files that look secret (`.env`, `*.pem`, `id_rsa`, `*secret*`) are skipped.

Logs: `%LOCALAPPDATA%\Recall\logs\recall.log`.

## Connect an AI agent (MCP)

With Recall running and signed in:

```
claude mcp add --transport http recall https://<project>.supabase.co/functions/v1/mcp
```

Then run `/mcp` in Claude Code and authenticate. Your browser opens Recall's consent page on `localhost:8766`; choose **Allow**. The agent gets read-only access to your memory, and no one else's.

| Tool | What it answers |
| --- | --- |
| `get_timeline` | What was I doing between two times? Episodes plus exact app and window spans. |
| `search_memory` | Find past work by meaning and keywords, optionally in a time range. |
| `get_episode` | One episode in full, with evidence. |
| `list_threads`, `get_thread` | Ongoing projects: episodes of the same work linked across days. |
| `daily_digest` | A summary of one day. |

Try: *"What was I working on this afternoon?"*

## Privacy

- **Allowlist only.** Nothing is read from apps you haven't ticked.
- **No keystroke logging, no screenshots, no raw audio stored.**
- **Redaction on the device.** API keys, passwords, other high-entropy tokens, emails and phone numbers become placeholders before anything leaves the machine. The map back to the real values stays on the device.
- **Raw text is short-lived.** The cloud deletes an episode's raw text 24 hours after it's understood; only the understood memory stays.
- **Private per user.** Row-level security on every table; agents only get read-only access that you approve.
- **No models on the device.** All understanding happens in the cloud.

## Development

```
.venv\Scripts\python -m pytest -q                  # unit tests, ~7 s
.venv\Scripts\python -m pytest -q --integration    # drives real windows and Node
.venv\Scripts\python -m pytest -q --cloud          # deployed cloud, throwaway users, a few Groq calls
npx deno test supabase/functions/tests             # Edge Function tests
```

Deploy the cloud to the project in `.env` (needs `SUPABASE_ACCESS_TOKEN`, `SUPABASE_SERVICE_ROLE_KEY`, `GROQ_API_KEY` and optionally `CEREBRAS_API_KEY`; safe to re-run):

```
.venv\Scripts\python scripts/deploy_cloud.py
```

Other scripts: `scripts/perf_monitor.py --minutes 30` logs Recall's CPU and RAM; `scripts/seed_demo.py` loads a demo dataset into the local store.

Budgets: idle CPU under 0.5%, active text capture under 3%, RAM under 600 MB, text searchable on the device under 5 s, episode understood in the cloud under 2 min.

The build plan and remaining work are in [docs/BUILDPLAN.md](docs/BUILDPLAN.md).
