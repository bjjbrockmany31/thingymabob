# Adaptive Paper Trader — Cloud version 6.1: daily automatic rerolls

This is the web/cloud continuation of the **fake $20 → 100 points** stock game.

The code includes your **22 completed desktop rounds** and last **banked $18.559055 / -6.731036 points**. The still-open desktop INTC holding from September 17 was **not carried over** because importing it on a later date would incorrectly count a multi-day change as a single-day trade. A fresh cloud round will be chosen when the cloud scheduler first sees a current U.S. market session.

## What you get

- One website that works on phones, tablets, or PCs without the desktop EXE.
- **Automatic daily reroll**: On each U.S. trading day, it settles yesterday’s fake holdings first, then chooses completely new companies for the new day (excluding the previous round’s tickers when alternatives exist). It rerolls at most once per market date, even if the updater runs repeatedly. No buttons to press or computer to leave on.
- A scheduled cloud process checks quotes about **every 15 minutes** around U.S. market hours, without your PC being on. GitHub schedules are **best effort** and can be delayed or skipped. **This is not a continuously running server or an exchange-grade real-time feed.**
- Fake dollars, cumulative points, daily money/points history, hoverable points chart, current company picks, and strategy performance.
- Saves new rounds by committing `data/` back to GitHub so they survive later cloud runs.
- Uses Yahoo's unofficial public market-data/news endpoints; these may be delayed, throttled, changed, or unavailable. Errors do **not** become fake profits.
- No real accounts, brokerage connection, or real-money trades.

**PRIVACY WARNING:** A free GitHub Pages website generally requires a **public repository**, and the `data/` files and your bot's fake trading history will be **publicly visible**. The website can be opened by anybody with the link and indexed by search engines. Do not put passwords, account information, or private information in this repository. If you need a private dashboard, do not publish using this public-repo setup; use a hosting service with authentication and private persistent storage instead.

## Updating a published v1 cloud bot WITHOUT resetting its saved game

If you already published the previous cloud package, **do not replace your `data/` folder or `docs/data/` folder with the starter files in this ZIP**. The separate `paper_trader_daily_update_only.zip` contains only the modified code, dashboard files, and workflow. Copy those files into your existing GitHub repository (using a local clone or the GitHub editor), commit, and push to `main`. Your existing rounds/points and fake balance stay in GitHub. On the next successful scheduled Actions run, the updated site will be published.

## Publish it — Windows, one-time setup

1. Sign in to your GitHub account, then create a **new EMPTY public repository** at <https://github.com/new>. Suggested repo name: `paper-trader-cloud`. **Do not select “Add a README”** or other starter files. Creating a repo is something the account owner has to do; this package cannot create a GitHub repo by itself.
2. Download this ZIP and **extract it**. Install [Git for Windows](https://git-scm.com/downloads/win) if you don't already have it.
3. Inside the extracted folder, double-click `PUBLISH_TO_GITHUB.bat`. Paste the **HTTPS URL of your new empty repo** when asked (for example, the URL displayed in the GitHub repo after you create it). Complete GitHub sign-in if prompted. This uploads the project, saved fake game, and scheduled cloud workflow.
4. In your GitHub repo: **Settings → Pages → Build and deployment → Source → GitHub Actions**. If the initial Actions run failed before Pages was enabled, go to **Actions → Paper trader cloud updates and dashboard → Run workflow** to retry. If GitHub blocks saving data, check **Settings → Actions → General → Workflow permissions → Read and write permissions**.
5. In **Settings → Pages**, click **Visit site** once the deployment succeeds. That's your actual permanent dashboard link — save it as a phone bookmark. No fixed link is issued until GitHub has created and deployed your repository.

The web page checks the **published saved data** once per minute; this does *not* make the cloud price fetch run every minute. The cloud price/news process works from GitHub's scheduled jobs, approximately every 15 minutes around U.S. market hours (9:30 a.m.–4 p.m. Eastern, with a buffer before and after for both daylight-saving seasons).

### Important behavior

- The desktop `.exe` and the hosted copy are **not synced** after migration. Once you switch, use the website as the source of truth or you will have two separate fake games.
- **Auto-reroll**: The cloud app automatically selects new companies at the first successful scheduled update after 9:35 a.m. Eastern each trading day, excluding previous-round tickers when alternatives exist. It keeps those picks for the day and banks them after 4:10 p.m. Eastern when data is available. If it already rerolled that market date, it will NOT pick again that day. If it misses the closing run, it tries the previous day's daily closing prices on the next run. If prices are incomplete, it keeps the existing fake holdings and displays an error instead of inventing results.
- Saturday, Sunday, and U.S. stock-market holidays are not trading days: the bot does not reroll then. The bot waits for fresh same-date market quotes before taking new positions. If quotes are missing or a prior round cannot be safely settled, it pauses the reroll and preserves the saved game. Early closes may delay final settlement.
- Headlines must actually mention the company name/ticker before their sentiment can affect a pick. The scoring is still simple and cannot verify sources or predict price reactions.
- Because scheduled jobs and quote services can fail, check the dashboard's last-updated time. GitHub might restrict or stop a workflow, including after periods of inactivity.

### Local preview (no market downloads)

From the extracted folder run `python -m http.server 8000 --directory docs` and then open `http://localhost:8000/`. This previews your saved history on *your* computer. It does not create a public link or start cloud scheduling.

### Files

| Path | Purpose |
|---|---|
| `docs/` | Public, mobile-friendly dashboard and generated JSON |
| `data/bot_state.json` | Adaptation scores and current fake cash/picks |
| `data/paper_history.csv` | Imported and newly completed rounds |
| `run_cloud.py` | Cloud time-of-day logic, headline matching, saved snapshots |
| `bot_core.py` | Paper trading / adaptive strategy engine from the desktop bot |
| `.github/workflows/cloud-bot.yml` | Scheduled processing and direct Pages deployment |
| `PUBLISH_TO_GITHUB.bat` | Windows Git uploader for a repo you create |

### Fine print

News is only a heuristic; markets can move the opposite way. The fake balance is not an actual investment value. Yahoo's public endpoints have no uptime guarantee, and this educational game is not suitable for real-money or time-sensitive trading.
