# Recall: the 80-second pitch

About 210 words, roughly 80 seconds spoken at a relaxed pace. Timings in brackets.

---

**[0:00, the problem]**
You spend all day on your computer, and by Friday you can't remember what you did on Monday. Which sponsor did we pick? What was that fix I found? Your AI agents are worse: every chat starts from zero.

**[0:15, what Recall is]**
Recall is a memory for your computer. You tick the apps you want remembered. Recall reads their text, not screenshots, not keystrokes, and turns it into memories you can ask about from any device, in plain words.

**[0:30, the demo moment]**
Watch: I read an article in Edge and write a few lines in VS Code. One keypress sends it. Four seconds later the cloud has understood it. Now I ask: "how do I stop handling the same event twice?" and there it is, with the evidence. And Claude Code, over MCP, can ask the same memory: "What was I working on in the last ten minutes?"

**[0:55, why you can trust it]**
Only the apps you pick. Passwords, API keys, emails and phone numbers are removed on your PC before anything leaves. Raw text is deleted from the cloud within a day. And it uses under 1% of one CPU core.

**[1:10, close]**
Twenty out of twenty on search by meaning, fourteen out of fourteen on questions. Recall: your computer finally remembers, so you don't have to.

---

## How the local Python script works

Recall on your PC is one low-priority Python process (`python -m recall`, started by `run_recall.bat`). It runs no AI models. It has five stages:

1. **Watch** (`recall/watcher.py`). Windows tells Recall when a tracked app comes to the front, and when its content changes. No polling, and untracked apps are never looked at.
2. **Read** (`recall/capture/`). Once a tracked window's content settles for 1.5 seconds, Recall reads its text through Windows UI Automation and keeps only the lines it hasn't seen before. VS Code hides editor text, so a small VS Code extension posts the visible code over localhost instead.
3. **Remember locally** (`recall/memory/`). New text is grouped into activities ("VS Code, project X, 14:02 to 14:40") and saved to a SQLite file with a keyword index. That powers the `Ctrl+Shift+Space` search, which works offline.
4. **Cut into episodes** (`recall/sync/segment.py`). The text stream is split where your task changes: each 30-second slice becomes a word vector, and when it drifts away from what came before, the episode ends. Five idle minutes or 20 minutes of work also close one. Repeated lines are dropped using fingerprints.
5. **Clean and send** (`recall/sync/redact.py`, `uploader.py`). Secrets, emails and phone numbers become placeholders like `⟨SECRET:3⟩`; the key to map them back never leaves your PC. Episodes are gzipped into a local outbox and uploaded every 60 seconds (or right away with `Ctrl+Alt+Esc`). If you're offline, they wait.

From there the cloud (Supabase plus an LLM) turns each episode into a memory that the dashboard, search window and your AI agents can ask about.
