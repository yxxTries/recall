# Recall — Where It Stands

Sep 27, 2026 · status after the parallel build sessions

Recall is a Windows tray app that remembers what you did in the apps you pick. The device reads their text (no models, no keystrokes, no screenshots), cuts it into episodes, redacts secrets and uploads them. In the cloud a free LLM turns each episode into memory, which you search from the tray, ask about on a web dashboard, or hand to an AI agent over MCP.

**In one line:** the text-only MVP works end to end and is deployed. What's missing is proof on real use, the demo itself, audio, and everything a stranger would need to install it.

## Gates

| Gate | What it proves | Status |
| --- | --- | --- |
| M0–M3.1 | Setup, app tracking, text capture, VS Code, local memory | Passed (tagged) |
| M7 | Cloud memory, search by meaning, MCP for agents | Passed (tagged `m7-cloud`) |
| M4 | 5 min of real use found again; 30-min perf run while working; backup video | **Open** |
| M8 | Demo runs twice in a row; tag `v1.0-demo` | **Open** (automated demo path passes; live rehearsal to do) |
| M5 | Speech from tracked apps searchable in 30 s | **Not started** |

## What's built

| Area | What works |
| --- | --- |
| **Capture** | Only allowlisted apps, from Windows events (no polling); untracked apps cost nothing. Text through UI Automation, read 1.5 s after content settles (at most every 4 s). VS Code: editor lines via the Recall Companion extension; Claude Code / Copilot chat panels read from their newest end. Browsers are read for the page only, never their own dialogs, tabs or toolbar. Skips password fields, InPrivate/Incognito windows and secret-looking files. Pause survives a restart. |
| **Local memory** | SQLite + FTS5: every activity is keyword-searchable on the device within ~1.5 s, offline. Search window on `Ctrl+Shift+Space`. |
| **Episodes** | CUSUM topic-drift segmentation (alt-tabs don't split; short lead-ins join the next task), SimHash dedupe, word rarity, a 12K-char budget that keeps prose over logs and JSON. |
| **Redaction** | Known key formats, `password=` values, emails, phones and high-entropy tokens become placeholders on the device; the map back stays local. |
| **Sync** | gzip outbox in SQLite, uploads every 60 s, survives offline and restarts, backs off to 10 min. `Ctrl+Alt+Esc` sends now (understood in under 5 s). |
| **Cloud** (Supabase) | Row-level security on every table. Queue + cron: Groq `gpt-oss-120b` (Cerebras fallback) writes summary, actions, important points, people, importance and a thread; evidence is checked word for word against the captured text; a rules-only fallback when the LLM keeps failing. gte-small embeddings, hybrid keyword + meaning search, daily digests, raw text deleted 24 h after it's understood. |
| **Ways in** | Search window "All devices"; dashboard on Vercel with **Ask**: answers cite episodes, understand time words ("yesterday afternoon"), give a whole-period rundown with time per app for "today" or "this week", know your devices by name ("my laptop"), and keep context across follow-ups. MCP server with 6 read-only tools for any client, OAuth with an Allow/Deny page on `localhost:8766`; sign in / sign up / sign out from the tray. |
| **Quality** | 71 Python tests, 24 Deno tests, VS Code extension test. Memory eval on 15 realistic scenarios: segmentation 12/12, facts 24/24, forbidden claims 7/7, Ask 8/8, citations 7/7 (latest cloud run). Ask eval over a two-device memory: 14/14. Golden paraphrase search 20/20 in the top 3. |
| **Performance** | A line on screen is captured in 1.4–1.9 s (Chrome, VS Code, a native editor); a streaming window at most every 4 s. Live multi-app run: at most 0.7% of one core, 56 MB, no extra load on Chrome. A 30-min mostly-idle run averaged 0.01% CPU, 138 MB max. Budgets: 3% active, 0.5% idle, 600 MB. |
| **Demo** | A 2-minute script with measured timings. Automated run on a throwaway account: send → uploaded 1.5 s → understood 4.2 s → search 5.1 s → MCP 6.2 s → Ask answered 8.3 s; 62 s including a minute of work. |

## What isn't built

- **Gate M4:** a real 5-minute use test, a 30-minute perf run *while actively capturing* (the 30-min run was paused after 4 minutes; only a shorter scripted run measured active capture), a backup video.
- **Demo (Phase 8):** seed a separate demo account, rehearse 3 times, final video, tag `v1.0-demo`.
- **Audio (Phase 5):** only "an app started/stopped playing sound" is detected. No capture, no transcription.
- **Launch plan** (`docs/LAUNCH_PLAN.md`, on the unmerged `claude/hackathon-build-plan-ivxdco` branch): one-step installer, built-in default config, first-run window, dashboard sign-up / magic link / onboarding, delete-my-memory, landing page. None of it started.
- **Not planned yet:** CI, a packaged `.exe` or auto-update, macOS/Linux, mobile, teams, billing.

## Weaknesses

**Scale and cost**

- One shared free Groq key serves everyone: about 1,000 requests a day and 8K tokens a minute, and episodes are understood one at a time. That's roughly one episode a minute for all users combined, so a few dozen active users would exhaust the day. No per-user quota.
- Cloud search ranks every episode a user has (the combined score bypasses the vector index). Fine at hundreds, slow at tens of thousands.
- Understanding and Ask share the same 8K tokens a minute. A short rate limit is now waited out once, but a second big send right before Ask can still make it answer "busy". Ask's prompt also grew (period overview, decisions, projects, history), so each question costs more of that budget.
- Digests are written for 3 users per hourly run.

**Setup and reach**

- Windows only; installing needs Python 3.10, a venv and a hand-written `.env`. About 20 minutes to a first memory.
- The agent consent page and the sign-up confirmation link both point to `localhost:8766`. An agent can only be approved on a PC that runs Recall and is signed in, and confirming your email on a phone lands on a dead page.
- The dashboard can't create accounts, and nothing lets a user delete their memory or account.

**Privacy and security**

- Redaction is patterns plus entropy. Names, addresses, customer data and secrets in unusual formats pass through to Groq/Cerebras.
- Only the raw episode text is deleted after 24 h. Window titles and URLs (timeline spans), verbatim evidence quotes and summaries are kept indefinitely.
- On the device, `vault.db` (the real values behind placeholders) and `cloud.json` (the refresh token) are plain files. Anyone with access to the Windows profile can read them; Windows DPAPI would fix it.
- Prompt injection in screen text is caught only by a regex that flags obvious phrases.

**Capture accuracy**

- Browser URLs rely on the English name of the address bar; embeddings and full-text search are English-only.
- Firefox URLs aren't read (Firefox is missing from the capture layer's browser list).
- A chat's first read keeps only its latest 80 lines, by design; older history is never captured.
- An open AI chat panel is read like everything else, so a long Claude Code session can take over an episode's summary (the demo script keeps that panel closed until the MCP step).
- A period rundown sees at most 40 episodes (the most important), so a busy week is summarized from a subset.
- Apps with poor accessibility support (canvas apps, games, some Electron apps) yield little or no text.
- Local search is keyword-only; meaning search needs the cloud.
- Timezone is one fixed offset from the latest device, so daylight-saving changes or travel shift "yesterday".

**Reliability and testing**

- Accuracy is measured on 15 synthetic scenarios; the real-use gate hasn't run.
- Most device tests need Windows (UIA, tray, hotkeys) and there's no CI, so tests run only when someone runs them.
- Data can be dropped quietly at the edges: a full memory queue (1,000 events), the outbox cap (2,000 batches), or a batch the cloud rejects with a 4xx.
- No monitoring: cloud failures show only in Edge Function logs.

## Checked for this summary

On Linux (the Windows-only parts can't run here): 25 cross-platform unit tests pass (segmentation, sketches, redaction, sync, config), and `scripts/eval_memory.py --local` segments all 15 scenarios as expected. Windows integration tests, `--cloud` tests and Deno tests weren't run here.
