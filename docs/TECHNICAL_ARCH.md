# Technical architecture

Sep 27, 2026 · @Amil

Recall has three parts:

- **Device agent** (`recall/`): one Python process on Windows. It captures text, keeps a local keyword index, cuts activity into episodes, redacts secrets and uploads.
- **Cloud** (`supabase/`): Postgres with pgvector, pgmq, pg_cron and pg_net, plus five Deno Edge Functions. It turns episodes into structured memory and serves it.
- **Dashboard** (`web/index.html`): one static page on Vercel.

A VS Code extension (`vscode-extension/`) feeds editor text to the device agent.

```
DEVICE (one Python process)                                   CLOUD (Supabase)
ForegroundWatcher --session/content events--> TextCapture      ingest ---> raw_episodes, timeline_spans, devices
VS Code extension --POST /vscode-->  EditorCapture                            | trigger (pg_net) + cron every minute
            \                          /                                      v
             text events -> MemoryWorker -> ActivityTracker -> memory.db   understand (Groq + gte-small)
                                 \                                            -> episodes, threads, digests
                                  -> SyncWorker: Segmenter -> redact -> gzip  ^
                                     -> outbox.db -> POST /functions/v1/ingest-|
Search window (pywebview) -> memory.db (FTS5) or /functions/v1/search      search, ask, mcp -> read the memory
```

## Device agent

### Process and threads

`recall/main.py` starts the tray (pystray, detached) and runs the search window's UI loop (pywebview) on the main thread. Everything else is a daemon thread that blocks until something happens. Nothing polls.

| Thread | Module | Does | Waits on |
| --- | --- | --- | --- |
| foreground-watcher | `watcher.py` | foreground and accessibility WinEvent hooks | Win32 message loop |
| text-capture | `capture/text_uia.py` | debounced UI Automation reads (COM, MTA) | an event, with a timeout while debouncing |
| vscode-ingest | `capture/vscode.py` | localhost HTTP server for the extension | socket |
| audio-watcher | `watcher.py` | audio session start and stop (pycaw, COM) | COM callbacks |
| memory-worker | `memory/worker.py` | turns events into local records, feeds sync; below-normal priority | `queue.Queue` |
| sync | `sync/uploader.py` | closes idle episodes, uploads the outbox | a 60 s timer, or the send hotkey |
| consent | `sync/consent.py` | local account page on `localhost:8766` (sign in, sign out) | socket |
| hotkey x2 | `ui/hotkey.py` | search and send hotkeys (`RegisterHotKey`) | Win32 message loop |

### Capture

**Foreground watcher.** `SetWinEventHook(EVENT_SYSTEM_FOREGROUND)` is out-of-context and skips Recall's own process. It maps each foreground window to its exe name. For Store apps it looks through `ApplicationFrameHost` to the child window's process. When the exe is on the allowlist, it emits `session_start` and hooks that one process's `EVENT_OBJECT_*` and scrolling events. When focus leaves the app, it emits `session_end` and unhooks. Caret and layout moves (`LOCATIONCHANGE`) are ignored. Any other content event on the session's root window calls `TextCapture.content_changed`. A title change updates the session's title, so a browser tab switch is noticed. Pausing sets the tracked set to empty, which ends the session.

**Text capture (UI Automation).** It reads once the window has been quiet for 1.5 s, and at least every 4 s while the content keeps changing. A window you leave with unread changes is read once more on the way out. Each read makes one cross-process call, chosen per app:

- **Browsers:** the first `Document` element's `TextPattern.DocumentRange` (capped at 200K characters), plus the URL from the address bar. Browser UI pages (`edge://`, `chrome://`…) and InPrivate or Incognito windows are skipped.
- **VS Code:** only the webview chat panels, deduplicated by webview id. Each panel is read from its end: the reader walks backwards through the tree in small steps, paragraph by paragraph, until it has about 10K characters or 1 s has passed. Reading the whole document would make VS Code stall on long chats. Chat-UI noise lines ("Copy code", timers) are filtered out.
- **Other apps:** the `Document` text if there is one. Otherwise a cached walk of the control view of up to 2,000 nodes, collecting text, list, tree and hyperlink names and edit values. Password fields are skipped.

Lines are normalised and deduplicated. Only lines not yet seen in that window (per hwnd, least-recently-used cache of 50 windows) become a `text` event. On the first read of a chat app, only the last 80 lines count as new.

**VS Code editor.** VS Code hides editor text from UI Automation. The extension posts the visible lines of the focused editor (up to 400) to `127.0.0.1:<random port>/vscode`, 1.5 s after the view settles. The port and a per-run token are in `%LOCALAPPDATA%\Recall\ingest.json`, and the server checks the token with `hmac.compare_digest`. `EditorCapture` drops sensitive file names (`.env`, keys, `*secret*`…). It keeps only lines not seen in that file, each run widened by 2 lines of context, and gives each block a `vscode://file/<path>:<line>` URL.

**Events.** Every source produces the same dict: `{type, time, app, title, url, text?, hwnd?}`, where `type` is `session_start`, `session_end` or `text`. Events go to `MemoryWorker.submit`, which never blocks: if the queue (1,000) is full, the event is dropped and logged.

### Local memory

`MemoryWorker` takes events in batches of up to 32 and hands each batch to the sync worker first, then to `ActivityTracker`. The tracker merges events about the same subject (a VS Code workspace, a site, a chat window) into one activity until that subject has been quiet for 5 minutes. It keeps a templated summary, files or pages seen, people named, frequent terms and up to 5 key lines. All other text is dropped. An activity with no text is stored only if it lasted at least 10 s, so windows you only pass through while alt-tabbing are left out.

`MemoryStore` (`memory.db`, SQLite in WAL mode) upserts each activity into `activities`. It rewrites that row in the FTS5 table `activity_words` (porter stemmer, unicode61), so the activity is searchable at once. A query becomes an OR of its non-stopword terms, ranked by BM25.

### Episodes

`Segmenter` (`sync/segment.py`) cuts the event stream into episodes without any model:

1. Events fall into 30-s windows. Each window becomes a 512-slot hashed vector with two parts. The text part is words weighted by IDF, plus window-title words. The context part is app and subject.
2. Word rarity comes from `WordRarity`, a 4 × 8192 count-min sketch that halves daily and is saved as `rarity.npz`.
3. The drift of each new window from the open episode's centroid feeds a CUSUM detector (slack 0.5, threshold 0.8). A cut needs drift that lasts about a minute, and it lands where the drift began. A lead-in shorter than 2 minutes joins the new task.
4. An episode also closes after 5 idle minutes (20 while a tracked window stays in front), at 20 minutes, on quit, or with the send hotkey.

An episode carries:

- **`episode_id`:** a hash of the device and the start time, so re-sending it is idempotent.
- **`spans`:** exact app and window spans.
- **`text`:** the episode's new lines under `## app · title` headers, capped at 12,000 characters (about 3K tokens). Near-duplicate lines already sent are dropped with `SeenLines`, a SimHash index in 8 bands of 8 bits that matches within 7 bits. Runs of lines that differ only in numbers keep their first and last line. When over budget, lines are ranked by rarity, with symbol-heavy lines ranked down.

### Redaction

`redact()` (`sync/redact.py`) runs over the text, titles and URLs of each episode before it is queued:

- **Known key formats:** Anthropic, OpenAI, GitHub, AWS, Slack, Groq, Supabase and Google keys, JWTs and PEM private keys.
- **Assignments:** `password = …`-style values.
- **Personal details:** emails and phone numbers.
- **Random-looking tokens:** 24+ characters that mix cases and digits, with entropy of at least 4 bits per character.

Each match becomes `⟨KIND:n⟩`. The mapping from `n` to the value lives in `vault.db` on the device only. The same value always gets the same placeholder.

### Upload

`SyncWorker` wakes every 60 s, or at once for the send hotkey. It closes any idle episode and builds a batch `{device_id, device_name, utc_offset_minutes, episodes[]}` with timezone-aware times. The batch is gzipped into `outbox.db` (bounded at 2,000 batches). The worker then posts batches oldest first to `/functions/v1/ingest` with `x-recall-encoding: gzip`. A network error, 401, 408, 429 or 5xx keeps the batch and doubles the wait, up to 10 minutes. Any other rejection drops the batch, so one bad batch can't block the rest.

`CloudSession` (`sync/cloud.py`) holds only the project URL, the publishable key and the user's own session, and refreshes it 60 s before it expires. You sign in on the local page at `localhost:8766`, which is guarded by a per-run nonce.

### UI

- **Tray:** search, send now, open dashboard, the tracked-apps picker (apps with a visible window, plus any new foreground exe), pause (saved in `config.json`), account and quit.
- **Search window:** a hidden pywebview page (`ui/search.html`) with a Python API. It shows search results from `memory.db` ("this device") or from the cloud `search` function ("all devices").
- **Hotkeys:** registered with `RegisterHotKey`, trying a list of fallbacks until one is free.

### Files in `%LOCALAPPDATA%\Recall`

| File | Holds |
| --- | --- |
| `config.json` | tracked apps, paused flag, `device_id` |
| `memory.db` | local activities and the FTS5 index |
| `outbox.db` | gzipped batches waiting to upload |
| `vault.db` | placeholder values (never uploaded) |
| `rarity.npz` | word-rarity sketch |
| `ingest.json` | port and token for the VS Code extension (deleted on quit) |
| `recall.pid`, `logs/recall.log` | PID file and rotating log |

## Cloud

### Data model

Every table has row-level security keyed on `auth.uid()`. Devices may insert `raw_episodes`, `timeline_spans` and `devices` rows as the signed-in user. Everything the model writes goes through the secret key.

| Table | Written by | Holds |
| --- | --- | --- |
| `devices` | ingest | device id, name, label, UTC offset, first and last seen |
| `raw_episodes` | ingest | redacted episode text; deleted 24 h after it's understood (hourly cron) |
| `timeline_spans` | ingest | exact app and window spans |
| `episodes` | understand | `worked_on`, `context`, `actions`, `important`, `topics`, `people`, `importance`, `evidence`, `thread_id`, 384-d `embedding` (HNSW), weighted `tsvector` (GIN) |
| `threads` | understand | projects across days: title, summary, span, episode count, mean embedding |
| `digests` | understand | one summary and highlights per user per local day |

### Ingest

`ingest` runs as the user (`auth: 'user'`), so row-level security applies. It gunzips the body with `DecompressionStream`, checks the shape (at most 200 episodes), then upserts the device and inserts raw episodes and spans with `ignoreDuplicates`, so a re-sent batch is harmless. A row trigger sends each new raw episode to the pgmq queue `understand`. A statement trigger calls the `understand` function at once through `pg_net`. The per-minute cron job is the retry path.

### Understanding

`understand` runs with the secret key. It takes jobs from `understand_next` (120 s visibility timeout) for up to 50 s per call. For each episode it:

1. Embeds the titles and text with Supabase's built-in `gte-small` (384 dimensions, first 1,500 characters).
2. Finds up to 3 similar threads from the last 14 days (`similar_threads`).
3. Calls the LLM with the text, spans, previous episode, candidate threads and the user's UTC offset. The reply must match `EPISODE_SCHEMA` (JSON schema output).
4. Checks the reply (`parse`). Evidence must quote the source, and thread ids must be among the candidates. An invalid reply gets one retry that names the error.
5. Embeds `worked_on + context + topics`, joins the chosen thread (the thread's embedding becomes a running mean) or creates one, upserts the episode, and marks the raw episode understood.

The LLM client (`_shared/llm.ts`) calls any OpenAI-compatible provider: Groq `openai/gpt-oss-120b` first, Cerebras when it is configured. It waits out a 429 of up to 12 s. The loop stops when fewer than 5K tokens are left in Groq's per-minute budget. A failed job comes back after its visibility timeout. After 4 attempts the episode gets a rules-only memory built from its spans.

An hourly job (`task: digests`) writes yesterday's digest for users whose local time is past 3 am. `task: embed` backfills missing embeddings.

### Retrieval

`search_memory(query_text, query_embedding, since, until, k)` is a SQL function that runs as the caller (security invoker), so row-level security scopes it. It scores each episode in the range:

```
0.55 · cosine similarity
+ 0.25 · min(4 · ts_rank_cd, 1)
+ 0.10 · exp(−age / 1 week)
+ 0.10 · importance / 10
```

`_shared/memory.ts` wraps it and adds reads for timelines, threads, digests, devices, time spent per app and important points. Every read uses the caller's row-level-security client. Evidence is marked as screen data, never instructions. Times are converted to the user's local offset (taken from their most recent device).

### Functions

| Function | Auth | Does |
| --- | --- | --- |
| `ingest` | user | stores a device batch (above) |
| `understand` | secret key | queue worker, digests, embedding backfill (above) |
| `search` | user | `search_memory` for the search window; time words in the query ("yesterday") set the range |
| `ask` | user | answers a question in plain words (below) |
| `mcp` | OAuth 2.1 user token | remote MCP server (below) |

**Ask.** It gathers context for the question:

- the top 8 search hits;
- for a period ("today", "this week"), every episode in it and the time per app;
- recorded decisions and deadlines;
- device names, and the one device the question mentions;
- ongoing threads;
- the conversation so far.

One LLM call with `ANSWER_SCHEMA` returns the answer and the ids of the episodes it used. Ids that were not in the context are dropped.

**MCP.** The Streamable HTTP transport is built with `@modelcontextprotocol/server`, behind `withOAuthProtectedResource`. Supabase Auth is the OAuth 2.1 authorization server: clients register dynamically, and Auth sends the user to the dashboard's `/oauth/consent` page to allow or deny them. There are six read-only tools: `search_memory`, `get_timeline`, `get_episode`, `list_threads`, `get_thread` and `daily_digest`. The server instructions and the tool schemas carry the user's timezone. For compatibility, CORS is open to browser clients, JSON-only clients get plain JSON instead of an event stream (`_shared/transport.ts`), and every protocol version is accepted. Clients without OAuth use the `mcp-remote` bridge.

## Dashboard

`web/index.html` is a single static page on Vercel that holds only the project URL and the publishable key. It signs in with supabase-js. It reads `episodes`, `threads`, `digests`, `devices`, `timeline_spans` and `raw_episodes` directly through row-level security, calls `ask` for questions, and writes only device labels.

The page is also the OAuth consent page for AI agents. `web/vercel.json` rewrites `/oauth/consent` to `index.html`. The page reads the request with `auth.oauth.getAuthorizationDetails` and calls `approveAuthorization` or `denyAuthorization`. Its Agents tab lists the agents you allowed (`listGrants`) and lets you revoke them. Every response is served with `X-Frame-Options: DENY` and `frame-ancestors 'none'`, so another site can't embed the page to trick you into allowing an agent.

## Deployment and tests

- **Cloud:** `scripts/deploy_cloud.py` applies migrations and deploys the five functions. It stores the project URL and secret key in Supabase Vault and schedules the cron jobs (`recall-understand` every minute, `recall-digests` hourly, `recall-forget-raw` hourly).
- **Dashboard:** `vercel deploy --prod` in `web/`.
- **Tests:** device tests in `tests/` (pytest, with `--integration` for real windows and `--cloud` for the deployed project), and cloud tests in `supabase/functions/tests/` (Deno).
