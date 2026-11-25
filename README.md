# UGC stats sync (Instagram + YouTube → Notion)

Every morning at 06:47 IST this pulls stats for each video in the **Daily Video Log**
(UGC Creator Tracker in Notion) straight from Instagram and YouTube. It covers videos
posted in the last 14 days.

| Platform | Filled in automatically |
|---|---|
| Instagram Reels | Views, Likes, Comments, Shares, Saves, Avg Watch Time |
| YouTube Shorts | Views, Likes, Comments, Shares, Followers Gained (subscribers), Video Length, Avg Watch Time |
| Both | Stats Updated, Sync Status, the account's Current Followers |

Still typed by hand: **Followers Gained** for Instagram (Instagram doesn't report it per Reel)
and **Video Length** for Instagram (needed for % Watched).

The full step-by-step setup guide is in Notion: **UGC Creator Tracker → ⚙️ Stats Automation – Setup Guide**.

## What's in here

| Path | What it does |
|---|---|
| `.github/workflows/daily-sync.yml` | Runs the sync every day (and on demand from the Actions tab) |
| `.github/workflows/connect-account.yml` | One-time step per account: paste the code a creator sends you |
| `docs/index.html` | The page creators land on after approving access (served by GitHub Pages) |
| `ugc_sync/` | The Python code |
| `tests/` | Automated tests (`pip install pytest && pytest`) |

## GitHub secrets

| Secret | Where it comes from |
|---|---|
| `NOTION_TOKEN` | notion.so/profile/integrations → your integration → Internal Integration Secret |
| `ENCRYPTION_PASSPHRASE` | Make one up (20+ characters) and keep it safe. Changing it means reconnecting every account |
| `REDIRECT_URI` | Your GitHub Pages address, e.g. `https://YOUR-USERNAME.github.io/ugc-stats-sync/` |
| `IG_APP_ID`, `IG_APP_SECRET` | Meta app → Instagram → API setup with Instagram login |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google Cloud → OAuth client (Web application) |
| `YOUTUBE_API_KEY` | Optional. Google Cloud → API key. Gives public YouTube numbers before a creator connects |

## Privacy

* No keys are stored in this repository. Secrets live in GitHub's encrypted secrets.
* Each creator's access is stored in Notion, encrypted with your passphrase.
* Logs show only counts and handles, never tokens or stats. This matters because Actions logs on a public repository are public.
* The approvals are read-only. They can't post, message, or change anything on the account. Creators can remove access at any time:
  * Instagram: Settings → Apps and websites
  * Google: myaccount.google.com/permissions
