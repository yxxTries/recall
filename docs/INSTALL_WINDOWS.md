# Installing Recall on Windows

Sep 27, 2026 · @Amil

This installs the Recall tray app on a Windows PC, plus the optional VS Code extension, and connects the app to your cloud memory. It takes about 10 minutes.

## What you need

| | |
| --- | --- |
| Windows | 10 or 11 |
| Python | 3.10, from [python.org](https://www.python.org/downloads/), with the `py` launcher |
| Supabase project | its URL and publishable key; ask the project owner, or deploy your own (see the last section) |
| For the VS Code extension | VS Code 1.90 or later, with `code` on your PATH, and Node.js (for `npx`) |

Windows 11 already has the Microsoft Edge WebView2 Runtime, which the search window needs. On Windows 10, install it from Microsoft if the search window doesn't open.

## 1. Get the code

```
git clone <repo URL> recall
cd recall
```

## 2. Install the Python packages

```
py -3.10 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## 3. Point Recall at the cloud

Create a file named `.env` in the repo folder:

```
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_ANON_KEY=<publishable key>
```

You can skip this step. Recall then captures and searches on this PC only, and nothing is uploaded.

## 4. Start Recall

Double-click `run_recall.bat`. It starts Recall with no console window. A red dot appears in the system tray; you may need to open the tray's hidden-icons area (`^`) to see it.

To start Recall every time you sign in to Windows:

1. Press `Win+R`, type `shell:startup` and press Enter.
2. Put a shortcut to `run_recall.bat` in the folder that opens.

## 5. Sign in

1. Right-click the tray icon and choose **Sign in to the cloud…**.
2. Sign in, or create an account, on the page that opens (`http://localhost:8766`).

The tray item then shows **Cloud: you@…**.

## 6. Choose the apps to remember

In the tray menu, open **Tracked apps** and tick each app you want, for example `code.exe` and `msedge.exe`. The list shows every app that has an open window, and any app you switch to later is added. Recall never reads an app you haven't ticked.

## 7. Install the VS Code extension (optional)

VS Code hides editor text from Windows, so Recall needs this extension to see your code. Without it, Recall reads only VS Code's chat panels.

```
cd vscode-extension
npx @vscode/vsce package
code --install-extension recall-companion-0.1.0.vsix
```

Reload VS Code afterwards. The extension sends text only to Recall on this PC (`127.0.0.1`), and only while `code.exe` is ticked.

## 8. Check that it works

1. Open a tracked app and read or type something for a few seconds.
2. Press `Ctrl+Shift+Space` and search for a word you just saw. It should appear within a couple of seconds.
3. Press `Ctrl+Alt+Esc` to send what you did to the cloud now. A notification says "Sent 1 episode to the cloud".
4. In the tray menu, choose **Open dashboard** and ask "What was I just doing?".

If another app already uses a hotkey, the tray menu shows the key Recall picked instead.

## Connect an AI agent (optional)

The MCP URL is on the account page (tray: **Cloud: you@…**) and looks like `https://<project>.supabase.co/functions/v1/mcp`.

- **Claude Code:** run `claude mcp add --transport http recall <URL>`, then `/mcp`.
- **Other clients:** add the URL as a remote (HTTP) MCP server. A client that runs only local commands uses `npx -y mcp-remote <URL>` as its command.

In either case your browser opens the dashboard's consent page. Choose **Allow** there. To see or revoke the agents you allowed, open the **Agents** tab on the dashboard.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| No tray icon | Look in the hidden-icons area. Check `%LOCALAPPDATA%\Recall\logs\recall.log` for errors. |
| "Port 8766 is busy" when signing in | Close whatever uses port 8766, or sign in from a terminal: `.venv\Scripts\python -m recall.sync.cloud login` |
| Nothing is captured | Check that the app is ticked under **Tracked apps** and that **Pause** is off. InPrivate and Incognito windows are never read. |
| Code from the VS Code editor is missing | Install the extension (step 7) and reload VS Code. |
| The search window doesn't open | Install the Microsoft Edge WebView2 Runtime. |
| "Couldn't reach the cloud" | Recall keeps what it captured and retries. Check your connection, and that `.env` holds the right URL and key. |

## Update

Quit Recall from the tray, then:

```
git pull
.venv\Scripts\pip install -r requirements.txt
```

Start it again with `run_recall.bat`. If the extension changed, repeat step 7.

## Uninstall

1. Quit Recall from the tray.
2. Delete the shortcut from `shell:startup`, if you made one.
3. Run `code --uninstall-extension recall-hackathon.recall-companion`.
4. Delete the repo folder, and `%LOCALAPPDATA%\Recall` (your local memory, settings and logs).

Your cloud memory stays in the Supabase project until it's deleted there.

## Deploy your own cloud (optional)

To use your own Supabase project instead of an existing one, add these to `.env`:

- `SUPABASE_ACCESS_TOKEN`
- `SUPABASE_SERVICE_ROLE_KEY`
- `GROQ_API_KEY`

Then run:

```
.venv\Scripts\python scripts/deploy_cloud.py
```

The script can be run again safely. It applies the database migrations, deploys the Edge Functions (this needs Node.js for `npx`), schedules the background jobs and turns on sign-in for AI agents.
