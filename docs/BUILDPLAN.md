# Recall — Hackathon Build Plan

Sep 26, 2026 · @Amil

> Local copy of the living plan: https://claude.ai/code/artifact/78121cc9-9357-4365-9e37-8f6b8f132dfb (the two diagrams exist only there).

## Overview

Recall is a Windows tray app. It captures text and audio from the apps you pick, turns them into semantic memory on-device right away, and later syncs that memory to a cloud knowledge base shared by all your devices. The hackathon win is one live demo: use a tracked app, then find what you saw or heard, by meaning, within seconds.

**Demo moment:** read an article in a tracked browser and play a short talk clip. Press the hotkey and type a paraphrase like "that bit about vector databases". Recall shows the exact passage and transcript line with app, window and time. Finale: run the same search from a second device.

**Success criteria**

- [ ] Only apps on the tracked list are captured; untracked apps cost nothing
- [ ] Text is searchable within 5 s of appearing on screen
- [ ] Speech is searchable within 30 s of being spoken
- [ ] Idle overhead is about 0% CPU, and active capture stays within the budgets in *Iterative testing*
- [ ] Capture never steals focus, adds input lag or shows UI inside the tracked app
- [ ] Memory captured on device A is searchable from device B
- [ ] Every milestone is committed, pushed to GitHub and tagged

## Decisions to confirm

The plan defaults to Python in a single process. Confirm or change these five decisions before Phase 0. The rest of the plan works with whichever option you choose.

| Decision | Default in this plan | Alternative | Why the default |
| --- | --- | --- | --- |
| Language | Python 3.10, one process | C# / .NET 8, single exe | Fastest to iterate; ONNX, Whisper and UI Automation already run in native code |
| Microphone | Opt-in per app, off by default | On whenever a tracked app is active | Privacy; the demo works on app output audio alone |
| Answers | Changed 2026-09-26: on-device rule-based activity records + keyword search; embeddings, search by meaning, speech-to-text and any LLM run in the cloud | Local embeddings + semantic search (the original default) | Recall keeps the general context, not every line; the device stays light (no models) |
| Cloud | Supabase (Postgres + pgvector + auth) | Own FastAPI + Postgres | Auth, database and REST API with no server code |
| Git flow | Push to `main`, tag every milestone | Branch + PR per phase | Speed; tags give known-good rollback points before the demo |

**Assumptions**

- The event runs about 48 hours with 1–3 people; timeboxes scale down if it's shorter.
- Machines run Windows 11 (per-app audio capture needs Windows build 20348 or later).
- The `recall` folder isn't a git repo yet; Phase 0 creates it and the GitHub remote.
- No keystroke logging: typed text is read from the app's UI once it appears on screen.
- Raw audio and screenshots are never stored. Speech segments go to the cloud only to be transcribed, then are discarded.

## Architecture

Everything up to search runs on the device in one low-priority process. The cloud only receives finished memory chunks.

```
[Foreground watcher] -> [Text capture: UIA, OCR fallback] --\
                     -> [Audio capture: loopback, silence gate, cloud speech-to-text] --> [Activity memory: rules -> activity records]
[Search UI] <- [Local store: SQLite + FTS5 keywords] <- activity memory
[Local store] -> [Sync worker] -> [Cloud knowledge base: embeddings, search by meaning, LLM] <- [Other devices]   (Phase 7)
```

The watcher is the gate: text capture runs only while a tracked app is in the foreground, and audio capture only while a tracked app is playing sound. When neither is true, no capture code runs. Text and audio feed the same pipeline, so search treats them alike.

## Capture strategy

Each source starts with the cheapest method that works for the app. It falls back to the next method only when the first one returns nothing useful.

**Text: while a tracked app is in the foreground**

| Tier | Method | Used when | Cost |
| --- | --- | --- | --- |
| 1 | UI Automation (UIA) tree read, triggered by focus, text-changed and structure-changed events | Default for Win32, WPF, UWP, Chromium and Electron apps | Low: event-driven, debounced 1.5 s, capped at 150 ms and 2,000 nodes per read |
| 2 | Window frame via Windows.Graphics.Capture + Windows.Media.Ocr (built-in, on-device) | UIA returns under ~50 characters for a window with visible content (canvas apps, games) | Medium: only after the frame changes, at most once every 5 s |

- Only new text is kept: each window's last snapshot is hashed line by line, and only unseen lines move on.
- Browsers: the address bar URL is read through UIA and stored as metadata.
- Keyboard hooks are never used. They're intrusive, and tier 1 already sees typed text once it's on screen.
- Stretch tier 0: app-native hooks (a browser extension, a VS Code extension) for richer context.

**Audio: while a tracked app has an active audio session, focused or not**

| Tier | Method | Used when | What it records |
| --- | --- | --- | --- |
| 1 | Per-process loopback (`ActivateAudioInterfaceAsync` with process-loopback parameters, child processes included) | Windows build 20348 or later | Only the tracked app's output |
| 2 | System WASAPI loopback, switched on and off by the audio session manager | Tier 1 unavailable | All output, only while the tracked app plays |
| Opt-in | Microphone, while the tracked app holds an active capture session | User enables it per app (meetings) | Your side of the call |

- A loudness gate (no model) drops silence on the device, so only speech segments are uploaded.
- A cloud speech-to-text service transcribes segments of up to 30 s (provider picked in Phase 5).
- Transcripts join the text pipeline tagged `source=audio`; the audio buffer is discarded.

**Privacy guardrails**, built in from Phase 1:

- Capture only allowlisted apps; untracked apps are never read.
- Skip password fields (UIA `IsPassword`) and InPrivate or Incognito windows.
- Tray menu offers "Pause 1 hour", and the tray icon shows when capture is live.
- Search UI can delete memories by app or time range.
- Stretch: encrypt the local database with Windows DPAPI.

## Build phases

Nine phases take 44 of 48 hours, and the app is demo-able end to end at hour 19. Each phase closes with a test gate, and a milestone is pushed only when its gate is green.

| Phase | Hours from kickoff |
| --- | --- |
| P0 Setup and repo | 0–2 |
| P1 App tracking | 2–5 |
| P2 Text capture (UIA) | 5–11 |
| P3 Semantic memory | 11–15 |
| P4 Search UI (MVP gate at hour 19) | 15–19 |
| P5 Audio capture | 19–25 |
| P6 OCR + efficiency | 25–30 |
| P7 Cloud knowledge base | 30–38 |
| P8 Demo polish | 38–44 |

With two or more people, one person builds Phase 5 (audio) on a branch during Phases 2–4 and merges it at the MVP gate.

**Milestone routine**, run at every gate (PowerShell):

```powershell
pytest -q
python scripts/smoke.py
python scripts/perf_monitor.py --minutes 10
git add -A
git commit -m "M3: semantic memory - gate passed"
git tag m3-memory
git push origin main --tags
```

Commit locally whenever a checklist item works. Push and tag at every gate, so each tag on GitHub is a known-good build to fall back to.

### Phase 0 · Setup and repo (2 h)

- [x] `git init`, `.gitignore` (venv, `*.db`, model files, `.env`), private GitHub repo `recall`, first push
- [x] Python 3.10 venv, `requirements.txt`, package layout below
- [x] Tray icon (pystray) with Pause and Quit; logs in `%LOCALAPPDATA%\Recall\logs`
- [x] `config.json` in `%LOCALAPPDATA%\Recall` holds the tracked-app list
- [x] `scripts/perf_monitor.py` logs Recall's CPU % and RAM every second to CSV (psutil)
- [x] pytest with one passing test; `scripts/smoke.py` stub

```
recall/
  recall/      main.py, config.py, watcher.py
    capture/   text_uia.py, text_ocr.py, audio.py
    memory/    chunker.py, embed.py, store.py
    ui/        window.py, index.html
    sync/      client.py
  scripts/     perf_monitor.py, smoke.py, seed_demo.py
  tests/
```

**Gate M0:** `python -m recall` shows the tray icon, idle CPU stays under 1% for 5 min, and pytest is green. Push, tag `m0-setup`.

### Phase 1 · App tracking (3 h)

- [x] App picker in the tray menu lists apps with open windows by exe name (titles and icons moved to the Phase 8 visual pass); tick to track; saved by exe name
- [x] Foreground watcher via `SetWinEventHook(EVENT_SYSTEM_FOREGROUND)`, no polling loop
- [x] Emit `session_start` and `session_end` events with app, window handle, title and time
- [x] Audio-session watcher (`IAudioSessionManager2` notifications) flags when a tracked process starts or stops playing sound

**Gate M1:** a pywinauto script switches between tracked and untracked apps 10 times; the log shows exactly the right sessions, and CPU stays near 0% between switches. Config load/save unit test passes. Push, tag `m1-tracking`.

### Phase 2 · Text capture via UIA (6 h)

- [x] On session start and on the app's accessibility events (a WinEvent hook on its process only): debounce 1.5 s, then read the window's tree with a cache request
- [x] Pull text from `Name`, `ValuePattern` and `TextPattern` document ranges; skip password and offscreen elements
- [x] Cap each read at 150 ms or 2,000 nodes, on a worker thread, never the tracked app's UI thread
- [x] Diff against the window's last snapshot; emit only new lines with app, title, URL and time
- [x] Browsers: read the address-bar URL; skip InPrivate and Incognito windows
- [x] Write captured text to a JSONL file until the store lands in Phase 3

**Gate M2:** a test opens Notepad (tracked) and types a unique sentence with pywinauto; it appears in the JSONL within 3 s, exactly once. Manual check on Edge, VS Code and one Electron app (Slack, Teams or Discord). p95 read time under 150 ms. Push, tag `m2-text`.

### Phase 2.1 · VS Code first (added mid-build)

Text capture must work properly in VS Code before other apps. VS Code hides editor text from UI Automation, so code comes from a companion extension, and chat panels come from UIA.

- [x] Recall Companion extension (`vscode-extension/`) posts the active editor's visible lines, path and line number to Recall on localhost, only while VS Code has focus
- [x] Token-guarded `127.0.0.1` ingest; only new lines per file are stored, with 2 lines of context and a `vscode://file/…:line` link back
- [x] Secret-looking files (`.env`, `*.pem`, `id_rsa`, `*secret*`) are never stored
- [x] UIA reads only VS Code's chat webviews (Claude Code, Gemini, Codex); menus, file tree and status bar are skipped
- [ ] Install the VSIX into your VS Code: `code --install-extension vscode-extension/recall-companion-0.1.0.vsix`, then reload the window
- [ ] Check Copilot Chat, which is a native view rather than a webview

**Gate M2.1:** passed. 29/29 tests. Live check: a separate VS Code window with the extension sent a file, Recall stored it, and a paraphrase search ranked it first. Chat-only read of the real VS Code: 2,380 lines in 125 ms, no menu or file-tree lines. Tag `m2.1-vscode`.

### Phase 3 · Semantic memory (4 h)

- [x] Chunker splits each capture's new lines into chunks of up to ~300 tokens (no waiting to fill a chunk, so text is searchable fast), with small overlap; content hash drops duplicates
- [x] Embeddings: `fastembed` with `BAAI/bge-small-en-v1.5` (384 dimensions, ONNX, no PyTorch), batched on a below-normal-priority thread
- [x] Store: one SQLite file with `sqlite-vec` for vectors and FTS5 for keywords; columns for app, title, URL, source, time, device ID and a `synced` flag
- [x] Hybrid query: vector top-k plus FTS5 BM25, merged by reciprocal rank fusion, filterable by app and time
- [x] Wire capture → queue → chunk → embed → store

**Gate M3:** chunker, dedupe and store unit tests pass. Golden-query test: 20 seeded snippets, 20 paraphrased queries, at least 18 land in the top 3. Capture to searchable in under 5 s. Push, tag `m3-memory`.

Superseded by Phase 3.1: the chunker, local embeddings and vector search were removed; search by meaning moves to Phase 7.

### Phase 3.1 · Context memory (added mid-build)

Recall remembers what you were doing, not every line: "worked on recall in VS Code", "meeting with Sarah and Dev", for every tracked app. Records are built by rules on the device. Embeddings, search by meaning and any LLM live in the cloud (Phase 7), so until then local search matches words, not meaning.

- [x] Activity blocks: events about one subject (VS Code workspace, website, chat or meeting window) merge until it's quiet for 5 min; switching apps in between doesn't split them
- [x] Each record keeps a templated summary, top files or pages, people (`Name:` lines, "with X" titles), top 12 terms and up to 5 key lines; all other captured text is dropped
- [x] Time in a tracked app counts even without readable text (focus of 10 s or more)
- [x] Store: `activities` table + FTS5 keyword search, upserted on every change (searchable at once; `synced` resets for Phase 7)
- [x] Removed fastembed, sqlite-vec, the chunker and the model download; the search window shows summary, app, time span and key lines

**Gate M3.1:** passed. pytest 34/34, smoke 6/6. Typed text searchable by keyword in 1.53 s. Live VS Code run (2.5 min): 2 activity records; a 2,666-line chat-panel read became one record; RAM 113–116 MB (was 2.2 GB after one big chat read), CPU 0.03% average; database 53 KB. Tag `m3.1-context`.

### Phase 4 · Search UI, the MVP (4 h)

- [x] Global hotkey (Ctrl+Shift+Space, else Win+Alt+Space when another app holds it) opens a small search window
- [x] Window: pywebview (Edge WebView2) rendering one HTML page, with a Python API bridge and no server
- [x] Results show snippet, app icon (initials badge; real icons in the Phase 8 visual pass), window title, time and source (text or audio); click copies the text or opens the URL
- [x] Filters: app; today or this week
- [x] `scripts/seed_demo.py` loads a known demo dataset

**Gate M4 (MVP):** use a tracked app for 5 min, then find its content (by keyword until Phase 7 adds search by meaning). A 30-min perf run stays within budget. Record a 60-s backup demo video. Push, tag `m4-mvp`.

Paused on 2026-09-26 for Phase 3.1. Already checked: the hotkey opens a focused window in 0.03 s; typing, ↓ and Esc work; a stored `vscode://…:123` link opens the file at line 123. Still to run: the 5-min use, the 30-min perf run and the video.

### Phase 5 · Audio capture (6 h)

- [ ] Per-process loopback capture for the tracked app's process tree, resampled to 16 kHz mono
- [ ] Spike limit: if Python bindings for per-process loopback aren't working after 1 h, ship a tiny C# helper exe (based on Microsoft's ApplicationLoopback sample) that streams PCM over stdout
- [ ] Fallback: system WASAPI loopback (PyAudioWPatch), on only while a tracked app's audio session is active
- [ ] Loudness gate (no model) splits speech segments; silence is never uploaded
- [ ] Segments go to cloud speech-to-text (needs the Phase 7 cloud) from a bounded queue, retried with backoff; audio discarded after
- [ ] Transcripts enter the activity pipeline with `source=audio` and segment timestamps
- [ ] Opt-in microphone capture while the tracked app holds the mic

**Gate M5:** play a known 2-min clip in a tracked app; at least 8 of its 10 key phrases are searchable within 30 s. Audio from an untracked app isn't recorded. Silence costs under 1% CPU. Push, tag `m5-audio`.

### Phase 6 · OCR fallback and efficiency hardening (5 h)

- [ ] OCR fallback: Windows.Graphics.Capture frame + Windows.Media.Ocr through the `winrt` Python packages, when UIA text is too thin; only after the frame changes, at most every 5 s
- [ ] Process priority below normal; worker threads sleep when their queues are empty
- [ ] Bounded queues with backpressure: drop OCR work before text work, never block a capture thread
- [ ] Privacy controls from *Capture strategy*: pause, delete by app or time, live-capture tray state

**Gate M6:** a 30-min mixed-use perf run meets every budget in *Iterative testing*. M2–M5 tests still pass. OCR returns visible text from one canvas-rendered app. Push, tag `m6-hardened`.

### Phase 7 · Cloud knowledge base (8 h)

- [ ] Supabase project: `devices` and `activities` tables (summary, key lines, metadata, device ID, `vector(384)` embedding computed in the cloud), row-level security per user
- [ ] Device registration on first run; device ID and auth token stored locally
- [ ] Sync worker: every 10 min when the machine is idle, push unsynced activities in batches of 200; upsert by activity ID so retries never duplicate
- [ ] Offline-safe: failed pushes retry with backoff, and the local DB stays the source of truth
- [ ] Cloud search: one Postgres function for vector + keyword search across devices; "All devices" toggle in the search UI
- [ ] Cloud intelligence: embeddings and search by meaning over synced activities (the golden paraphrase test moves here), and speech-to-text for Phase 5; LLM summaries or answers if time allows
- [ ] Stretch: link related memories across devices (nearest-neighbour "related" items under each result)

**Gate M7:** two devices (or two device IDs on one machine) sync, and device B finds a memory captured on device A. Cut the network mid-sync; after reconnecting, the cloud has no gaps and no duplicates. Push, tag `m7-cloud`.

### Phase 8 · Demo polish (6 h)

- [ ] Seed both devices with the demo dataset; rehearse the demo script 3 times
- [ ] Visual pass on the app picker, search window and tray icon states
- [ ] README: setup steps, architecture diagram, privacy stance
- [ ] Optional PyInstaller build; otherwise demo from the venv
- [ ] Record the final backup demo video

**Gate M8:** the full demo script runs twice in a row with no restarts. Push, tag `v1.0-demo`.

## Iterative testing

Every phase runs the same short loop, so the latest green tag on GitHub is always demo-able.

1. Build the smallest slice that completes one checklist item.
2. Run `pytest -q` and `python scripts/smoke.py` (under 60 s together).
3. Commit when green. At a gate, also run the perf check, then push and tag.
4. A red gate blocks the next phase. If it's still red when its timebox ends, cut scope from the *Cut list*.

**Test layers**

| Layer | What it checks | Tool | From |
| --- | --- | --- | --- |
| Unit | Activity rules, store, sync cursor, config | pytest | Phase 0, every commit |
| Scripted UI | Text typed into a tracked app is captured exactly once | pywinauto + pytest | Phase 2 |
| Golden queries | 20 paraphrased queries find seeded snippets in the top 3 | pytest | Phase 7 (cloud search) |
| Audio fixtures | A known clip's key phrases appear in the transcript | pytest + fixture WAV | Phase 5 |
| Sync | Two device IDs, network cut mid-sync, no gaps or duplicates | pytest against a Supabase test project | Phase 7 |
| Perf budget | CPU, RAM and latency against the budgets below | `scripts/perf_monitor.py` | Every gate |
| Demo run | The demo script, start to finish | A person with a stopwatch | M4, M7, M8 |

**Performance budgets**, measured on the demo laptop:

| Metric | Budget |
| --- | --- |
| CPU, no tracked app active | under 0.5% average |
| CPU, active text capture | under 3% average |
| CPU, 10 min of continuous speech | under 15% average |
| RAM | under 600 MB |
| Text on screen → searchable | under 5 s |
| Speech → searchable | under 30 s |
| UIA read time | under 150 ms at p95 |
| Local storage | under 20 MB per hour of active use |

## Demo script and cut list

The demo runs in 3 minutes. It survives a live failure at any step, because every search also works on the seeded dataset.

1. Show the tray and app picker: Edge, VS Code and Teams are tracked, Spotify isn't. (20 s)
2. Read a short article in Edge and play a 30-s talk clip in a tracked app. (40 s)
3. Press Ctrl+Shift+Space and search a paraphrase; open the passage and the transcript line. (40 s)
4. Show the perf monitor: CPU near 0% while idle. (20 s)
5. On a second laptop, search "All devices" and find the same memory. (40 s)
6. Close on privacy: allowlist only, no keystrokes, no raw audio or screenshots stored. (20 s)

**Cut list**: when a gate slips, cut from the top.

1. Cross-device "related" links (Phase 7 stretch)
2. LLM "Ask" answers
3. OCR fallback; keep UIA only
4. Microphone capture
5. Per-process loopback; use gated system loopback instead
6. Per-user cloud auth; use one demo-only service key
7. Never cut: the app allowlist, text capture, semantic search, idle efficiency

## Risks

The biggest schedule risk is per-process audio capture from Python, so Phase 5 has a 1-hour spike limit and two fallbacks.

| Risk | Impact | Mitigation |
| --- | --- | --- |
| No working Python binding for per-process loopback | Phase 5 slips | 1-h spike, then a small C# helper exe or gated system loopback |
| Chromium and Electron apps expose a thin UIA tree until a client asks | Browser and chat text is missed | Query once at session start to wake the tree; OCR fallback; test Edge and one Electron app at M2 |
| Large UIA trees (long web pages) read slowly | CPU spikes, lag in the tracked app | Cache requests, 150 ms and 2,000-node caps, debounce, worker thread |
| Cloud speech-to-text is slow, costly or offline | Speech not searchable within 30 s | Upload only gated speech; queue and retry; seeded transcripts for the demo |
| Recording other people on calls | Consent and legal exposure | Mic off by default; consent note in the UI and README |
| Personal data in the cloud | Privacy exposure | Row-level security; stretch: client-side encryption of chunk text |
| Venue Wi-Fi fails | Cloud step fails live | Second device pre-synced; backup videos from M4 and M8 |
