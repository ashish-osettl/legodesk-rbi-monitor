# RBI Regulatory Monitor — Legodesk Academy

Automatically scrapes the RBI website daily, detects new circulars/directions
that affect lenders and recovery professionals, notifies the Google Chat group,
and queues content for the Legodesk Academy.

---

## What It Does (Daily at 8:30 AM IST)

1. **Fetches** 4 RBI RSS feeds:
   - Press Releases
   - Notifications & Circulars
   - Draft Guidelines
   - Master Directions

2. **Filters** for lender relevance using 40+ keywords covering: NPA, SARFAESI, DRT, IBC, Fair Practices Code, collections, NBFC, ARC, etc.

3. **Analyses** new items using Claude AI to extract:
   - Plain-English summary
   - Impact by team (tele-collections, field, legal)
   - Compliance action required
   - Course module title + learning points

4. **Notifies** your Google Chat group with a formatted card

5. **Queues** content for the Academy (generates `data/pending_modules/*.json`)

---

## Setup

### GitHub Secrets Required

| Secret | Description |
|--------|-------------|
| `GOOGLE_CHAT_WEBHOOK` | Your Google Chat Incoming Webhook URL |
| `ANTHROPIC_API_KEY` | Claude API key (same as Case Engine) |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_USER` | Gmail address for email notifications |
| `SMTP_PASS` | Gmail App Password |
| `NOTIFY_EMAIL` | Email address to receive notifications |

### Get Your Google Chat Webhook
1. Open your Google Chat group
2. Click group name at top → **Manage webhooks**
3. **Add webhook** → name: "RBI Monitor" → Save
4. Copy the URL → add as `GOOGLE_CHAT_WEBHOOK` secret

### Add Secrets to GitHub
`repo → Settings → Secrets and variables → Actions → New repository secret`

---

## Academy Integration

When new RBI updates are found, JSON files are saved to `data/pending_modules/`.

Run `build_academy_from_queue.py` to convert them to HTML snippets:

```bash
python build_academy_from_queue.py
```

This creates HTML content blocks in `academy_pages/`. Copy into the
Legodesk Academy pipeline to publish them as new modules under
`/tracks/rbi-updates/`.

---

## Manual Run

Trigger from GitHub: **Actions → RBI Regulatory Monitor → Run workflow**

Or test locally:
```bash
export ANTHROPIC_API_KEY=sk-ant-...
export GOOGLE_CHAT_WEBHOOK=https://chat.googleapis.com/...
python rbi_monitor.py
```

---

## Files

```
rbi-monitor/
├── rbi_monitor.py              ← Main scraper + notifier
├── build_academy_from_queue.py ← Academy page generator
├── .github/
│   └── workflows/
│       └── rbi-monitor.yml     ← Daily GitHub Actions schedule
├── data/
│   ├── seen_items.json         ← State: tracks what's been sent
│   └── pending_modules/        ← Queue: JSON files for new Academy pages
└── README.md
```
