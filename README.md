# Recall

Recall remembers what you did on your computer, so you and your AI agents can ask about it later.

You pick the apps. Recall reads their text on your PC (no screenshots, no keystrokes), removes secrets and sends it to your own private cloud memory. Then you ask in plain words, from any device: *"What did I do on my laptop yesterday?"*, *"Which sponsor did we pick?"*, *"How long was I in VS Code today?"*

## What you can do

- **Ask your memory** at https://recall-memory-yxxtries.vercel.app. Answers are in plain words, cover all your devices and show the memories they came from. Follow-up questions work, the ask box stays at the top while you browse, and each cited memory opens in the timeline or its project.
- **See what it remembers.** Each memory shows its summary, its project and a **Key** badge when it matters; filter the timeline by words, period, device or app.
- **Search from anywhere** on your PC with `Ctrl+Shift+Space`.
- **Give your AI agents a memory.** Claude Code, ChatGPT, Cursor or any other MCP client can read it, read-only, once you allow it.
- **Keep it private.** Only the apps you tick are read, secrets are removed on your PC, and no one else can see your memory.

## Measured (Sep 27)

| | |
| --- | --- |
| Text on screen to searchable on your PC | about 1.5 s |
| Send to understood in the cloud | about 4 s |
| Ask to answer | about 2 s |
| Recall's own CPU and memory while capturing | under 1% of one core, 56 MB |
| Search by meaning (20 paraphrased questions) | 20/20 in the top 3 |
| Ask (14 questions over two devices) | 14/14 |
| Memory accuracy (12 realistic sessions) | facts kept 24/24, false claims avoided 7/7, secrets kept out 2/2 |

## Setup

Needs Windows 10 or 11 and Python 3.10.

1. Install:
   ```
   py -3.10 -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```
2. Create `.env` in the repo root with your Supabase project's URL and publishable key:
   ```
   SUPABASE_URL=https://<project>.supabase.co
   SUPABASE_ANON_KEY=<publishable key>
   ```
3. Start Recall with `run_recall.bat`. It lives in the tray.
4. In the tray menu, choose **Sign in to the cloud…** and sign in or create an account on the page that opens.
5. Under **Tracked apps**, tick the apps to remember, for example `code.exe` and `msedge.exe`.

Optional: the VS Code extension lets Recall see the code in your editor.

```
cd vscode-extension
npx @vscode/vsce package
code --install-extension recall-companion-0.1.0.vsix
```

## Using it

| To | Do this |
| --- | --- |
| Search your memory | `Ctrl+Shift+Space` (the tray menu shows another key if that one is taken) |
| Send what you're doing to the cloud now | `Ctrl+Alt+Esc`. Otherwise it goes after 5 idle minutes or a change of task. |
| Ask questions and see everything stored | Tray: **Open dashboard** |
| Name your devices | Dashboard: **Devices** ("Laptop", "Work PC"). Ask understands the names. |
| Connect or revoke AI agents | Dashboard: **Agents** |
| Stop capturing | Tray: **Pause**. It stays paused after a restart. |
| Sign out on this PC | Tray: **Cloud: you@…** |

Without an account, Recall still remembers and searches on this PC. If you're signed out, what it captured waits until you sign in again. Logs are in `%LOCALAPPDATA%\Recall\logs\recall.log`.

## Connect an AI agent (MCP)

Your memory is a remote MCP server that any MCP client can use: Claude Code, Claude Desktop, ChatGPT, Cursor, VS Code, Windsurf, Gemini CLI and others. The URL is on the dashboard's **Agents** tab:

```
https://<project>.supabase.co/functions/v1/mcp
```

1. Add the URL to your client as a remote (HTTP) MCP server. In Claude Code: `claude mcp add --transport http recall <URL>`, then `/mcp`.
2. Your browser opens the dashboard's consent page (sign in if asked). Choose **Allow**. This works from any device, with Recall running or not.

The agent can read your memory and nothing else. **Agents** lists every agent you allowed; revoking one stops it renewing its access, which then ends within an hour. A client that only runs local commands uses this as its command: `npx -y mcp-remote <URL>`.

| Tool | What it answers |
| --- | --- |
| `get_timeline` | What was I doing between two times? |
| `search_memory` | Find past work by meaning. Understands "today", "yesterday", "last week". |
| `get_episode` | One memory in full, with the text it came from. |
| `list_threads`, `get_thread` | Projects: the same work across days and devices. |
| `daily_digest` | A summary of one day (today if none is given). |

Times are in your timezone. Try: *"What was I working on this afternoon?"*

## Privacy

- **Allowlist only.** Nothing is read from apps you haven't ticked.
- **No keystroke logging, no screenshots, no audio stored.**
- **Secrets removed on your PC.** API keys, passwords, emails and phone numbers become placeholders before anything is sent. Only your PC can map them back.
- **Raw text is short-lived.** The cloud deletes it 24 hours after it's understood; only the memory stays.
- **Private to you.** Every table is locked to its owner, and agents get read-only access only when you allow it.
- **No AI models on your PC.** All understanding happens in the cloud.

## How it works

```
YOUR PC (one low-priority process, no AI models)
tracked apps -> read text -> local search (works offline)
                          -> episodes -> remove secrets -> upload
CLOUD (Supabase, private to you)
upload -> understand (Groq LLM) -> memory: episodes, projects, daily digests
                                -> ask    -> dashboard, from any browser
                                -> search -> Recall's search window
                                -> mcp    -> your AI agents, once you allow them
```

- **On your PC** (`recall/`): one Python process. It reads text from tracked apps (Windows UI Automation, plus the VS Code extension), keeps a local keyword index, cuts activity into episodes when the topic changes, drops duplicates, removes secrets and uploads in the background. Uploads wait out being offline.
- **In the cloud** (`supabase/`): Postgres with vector search and row-level security. A free LLM (Groq, `gpt-oss-120b`) turns each episode into memory: what you worked on, what you did, what mattered and who was there, linked to the project it belongs to. `ask` answers from that memory: for a period it reads everything in it, for a device only that device, and it keeps a conversation's context. `mcp` gives agents the same memory.
- **Dashboard** (`web/index.html`): one static page on Vercel. It holds only the project's public URL and key.

## Development

```
.venv\Scripts\python -m pytest -q                  # unit tests, ~8 s
.venv\Scripts\python -m pytest -q --integration    # real windows and Node
.venv\Scripts\python -m pytest -q --cloud          # the deployed cloud, throwaway users, a few LLM calls
npx deno test supabase/functions/tests             # cloud function tests
.venv\Scripts\python scripts/eval_ask.py           # Ask: 14 questions over a seeded two-device memory
.venv\Scripts\python scripts/eval_memory.py        # memory accuracy on 12 realistic sessions (--local: device side, free)
```

Before going on stage, run `.venv\Scripts\python scripts/demo_check.py`. It runs the demo's path for real on a throwaway account (Edge and VS Code, send now, then understood, search, MCP and Ask) and times each step. It takes over the screen for about a minute once the PC is idle, and spends 2 LLM calls (`--chrome` to use Chrome).

Deploy the cloud (safe to re-run; needs `SUPABASE_ACCESS_TOKEN`, `SUPABASE_SERVICE_ROLE_KEY` and `GROQ_API_KEY` in `.env`): `.venv\Scripts\python scripts/deploy_cloud.py`. Deploy the dashboard: `cd web`, then `vercel deploy --prod`.

The plan, results and demo script are in [docs/BUILDPLAN.md](docs/BUILDPLAN.md).
