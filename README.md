# UHNW Prospect Agent v2

Weekly automated prospect digest — pulls from 7 data sources, scores with Claude, emails every Monday 7am Austin time.

## What it finds

| Profile | Sources |
|---|---|
| Big law equity partners | Google News, PR Newswire, BizJournals |
| PE / hedge fund professionals | Google News, SEC Form D, BizJournals |
| Corporate C-suite executives | SEC 8-K, Google News, BizJournals |
| Financial professionals (Goldman, Blackstone etc) | Google News |
| Texas energy / mineral rights | Google News, SEC |
| Texas tech founders | Google News, BizJournals |
| Texas ranch / ag land | Google News |
| Medical group sales | Google News |
| Philanthropy signals | IRS 990 / ProPublica |

## Environment variables — set all 4 in Railway

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your sk-ant- key from console.anthropic.com |
| `EMAIL_FROM` | builder4prospo@gmail.com |
| `EMAIL_PASSWORD` | Gmail app password (16 characters, see below) |
| `EMAIL_TO` | Where you want the digest delivered |

## Gmail app password (required — Gmail blocks regular passwords for scripts)

1. Go to myaccount.google.com (logged into builder4prospo@gmail.com)
2. Click Security
3. Enable 2-Step Verification if not already on
4. Search "App passwords" in the top search bar
5. Create one, name it "prospecting-agent"
6. Copy the 16-character password → use as EMAIL_PASSWORD in Railway

## Railway deployment

1. Upload all files to GitHub repo
2. New Project → Deploy from GitHub repo
3. Add the 4 environment variables above
4. Settings → Cron Schedule → `0 13 * * 1` (Monday 7am Austin / 1pm UTC)
5. Hit Deploy manually once to test

## Cron schedule explained

`0 13 * * 1` = Every Monday at 13:00 UTC = 7:00am Austin (CT)

## Estimated running costs

- Anthropic API: ~$0.10–0.20 per run
- Railway hosting: ~$5/month
- All data sources: free
- Total: ~$5–6/month
