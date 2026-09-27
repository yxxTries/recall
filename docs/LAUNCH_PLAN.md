# Recall — Launch Build Plan

Sep 27, 2026 · @Amil

The MVP works (see [BUILDPLAN.md](BUILDPLAN.md)). This plan turns it into something a stranger can install, sign into and get value from in five minutes, locally and in the cloud. It adds to the MVP plan; it doesn't replace it.

## The one metric

**Time to first memory:** from landing on the site to seeing your own episode answered in Ask. Target: **under 5 minutes, with no terminal.** Today it's about 20 minutes and needs Python, a venv, a hand-written `.env` and CLI sign-in.

## Where the friction is today

| Step | Today | Launch |
| --- | --- | --- |
| Install | Python 3.10, venv, `pip install`, `.env` by hand | One installer (or `setup.bat`) that does all of it |
| Account | `python -m recall.sync.cloud signup` / `login` | Sign up and sign in from the dashboard and the tray |
| First run | Empty tray; you must find **Tracked apps** | First-run window: sign in, pick apps, press the send hotkey |
| Dashboard | Password sign-in only; no sign-up; empty page for new users | Sign up, magic link, onboarding state with a "download Recall" step |
| Trust | Privacy lives in the README | Privacy shown where you pick apps and on the landing page |

## Rules for how we build

- **Weekly-sprint shape, compressed to days.** Each day has one goal we can demo at the end of it. If it doesn't demo, it doesn't count.
- **Talk to users every day.** Give the build to 1–2 people who haven't seen it; time them to first memory, write down every place they stop. Those stops are tomorrow's list.
- **Ship the smallest thing that moves the metric.** Anything that doesn't shorten time to first memory or make the demo stronger goes on the "later" list.
- **Never break what's done.** New work goes in new files or behind additions; the CLI keeps working; every existing gate (M4, M7, M8) stays green.
- **Test on every loop**, not at the end (see Testing loop below).

## Sprints

**Day 1 · Sign in from anywhere (cloud)**

- [ ] Dashboard: **Create account** next to Sign in; magic-link sign-in as the default, password kept
- [ ] Dashboard: empty state for a new account: "1. Download Recall 2. Sign in 3. Press `Ctrl+Alt+Esc`", with the download link
- [ ] Supabase Auth: confirmation and magic-link emails land on the dashboard, not localhost
- [ ] Optional: Google sign-in through Supabase Auth, if it fits in the day
- **Demo:** a new person signs up on their phone and sees the onboarding steps

**Day 2 · Install in one step (local)**

- [ ] `setup.bat`: finds or installs Python 3.10 (winget), creates the venv, installs requirements, writes `.env` with the public project URL and key (both are safe to ship), then starts Recall
- [ ] Ship the default `.env` values in `recall/config.py` so a missing `.env` still works; `.env` stays an override
- [ ] Stretch: a PyInstaller build (`Recall.exe`) attached to a GitHub release, linked from the dashboard
- **Demo:** clean Windows VM to tray icon with one double-click

**Day 3 · First-run window (local UI)**

- [ ] On first start (no config), open a small pywebview window: sign in or create account (same auth as the CLI), then tick apps from a list of what's running now, with sensible defaults preselected (browser, VS Code)
- [ ] Tray: **Sign in / Sign out** and the signed-in email; **Open dashboard**
- [ ] A notification after the first episode is understood: "Recall remembered: *title*. Ask about it on the dashboard."
- **Demo:** install to first understood episode without touching the terminal

**Day 4 · Polish and trust**

- [ ] Landing section on the dashboard (signed out): one line of what Recall does, the 3 privacy promises, a 30-second GIF, the download button
- [ ] Dashboard: **Delete my memory** (all episodes for the account) and **Sign out everywhere**
- [ ] Error states people will hit: offline, wrong password, email not confirmed, Groq rate limit (show "understanding delayed", not a blank)
- **Demo:** the full 3-minute demo from BUILDPLAN.md, starting from the landing page

**Day 5 · Rehearse and freeze**

- [ ] Three fresh-user runs, timed; fix the top stop from each
- [ ] Record the backup video; tag `v1.1-launch`
- [ ] Freeze: after this, only fixes

**Later (not this build):** audio (Phase 5), billing, macOS, teams, mobile capture.

## Testing loop

Run on every change, before every push:

1. `pytest -q` (unit, ~7 s) and `npx deno test supabase/functions/tests`.
2. `python scripts/smoke.py`: Recall starts in a throwaway folder with no errors.
3. For UI changes: open the dashboard (or the first-run window) and click through the changed flow; for the dashboard, a Playwright script that signs up a throwaway user and reaches the onboarding state.

At the end of each day:

4. `pytest -q --cloud` against the deployed project.
5. `python scripts/eval_memory.py --local` so capture quality hasn't regressed.
6. The day's demo, done by someone who didn't build it. Record time to first memory in the table below.

| Day | Time to first memory | Where they got stuck |
| --- | --- | --- |
| Baseline | ~20 min (terminal) | venv, `.env`, CLI sign-in, finding Tracked apps |
| 1 | | |
| 2 | | |
| 3 | | |
| 4 | | |
| 5 | | |

## Done means

- A new person gets from the landing page to an answered Ask question in under 5 minutes, without a terminal, three times in a row.
- The existing demo, gates and tests still pass.
- Privacy promises are visible before you install and when you pick apps.
