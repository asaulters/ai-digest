# ai-pm-digest

A personal AI-powered daily digest for applied AI in project management.

Scrapes RSS feeds → semantic filtering → SQLite → email via Resend → GitHub Pages dashboard.

## How it works

1. **Scrape** — `scripts/scrape.py` pulls from configured RSS feeds and Hacker News Algolia API.
2. **Filter** — `scripts/filter.py` embeds article titles + summaries with `all-MiniLM-L6-v2` and scores them against a centroid of your topic seeds (`config/topics.txt`). Articles below the cosine similarity threshold are dropped.
3. **Store** — `scripts/store.py` writes all articles (with scores) to `data/digest.db`.
4. **Deliver** — `scripts/deliver.py` sends a formatted HTML email via [Resend](https://resend.com) and writes the `dashboard/index.html`.
5. **Schedule** — GitHub Actions runs the full pipeline daily at 7am UTC, commits the updated DB + dashboard, and deploys to GitHub Pages.

## Setup

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/ai-pm-digest
cd ai-pm-digest
pip install -r requirements.txt
```

### 2. Configure

Edit `config/settings.yaml`:
- Set `email.from_address` and `email.to_address`
- Tune `relevance_threshold` (0.35 is a good start — raise to tighten focus)

Edit `config/sources.yaml` to add or remove RSS feeds.

Edit `config/topics.txt` to adjust topic seeds (one topic per line, plain English).

### 3. Set your Resend API key

```bash
export RESEND_API_KEY=re_your_key_here
```

Or in GitHub: **Settings → Secrets → Actions → New secret** named `RESEND_API_KEY`.

### 4. Enable GitHub Pages

In your repo: **Settings → Pages → Source → GitHub Actions**.

### 5. Run locally

```bash
# Dry run — scrape + filter + store, no email
python scripts/run_pipeline.py --dry-run

# Full run (requires RESEND_API_KEY)
python scripts/run_pipeline.py

# Skip email but still update DB and dashboard
python scripts/run_pipeline.py --no-email
```

## Configuration reference

| File | Purpose |
|---|---|
| `config/sources.yaml` | RSS feed list + HN Algolia keywords |
| `config/topics.txt` | Plain-English topic seeds for semantic filtering |
| `config/settings.yaml` | Threshold, lookback window, email, max articles |

### Key settings (`settings.yaml`)

| Setting | Default | Notes |
|---|---|---|
| `embedding.relevance_threshold` | `0.35` | Cosine similarity cutoff. Raise to 0.40–0.45 to tighten. |
| `digest.lookback_days` | `2` | How many days back to look for new articles |
| `digest.max_articles_per_digest` | `25` | Email cap (highest-scoring articles win) |

## Project structure

```
ai-digest/
├── config/
│   ├── sources.yaml      # RSS feeds and sources list
│   ├── topics.txt        # Topic seeds for embeddings
│   └── settings.yaml     # All tunable parameters
├── scripts/
│   ├── scrape.py         # Fetches articles
│   ├── filter.py         # Embedding + cosine similarity filtering
│   ├── store.py          # SQLite read/write
│   ├── deliver.py        # Email + dashboard generation
│   └── run_pipeline.py   # Master orchestrator
├── dashboard/
│   └── index.html        # Generated daily, published to GitHub Pages
├── data/
│   └── digest.db         # SQLite database (git-tracked, grows slowly)
├── .github/workflows/
│   └── daily_digest.yml  # GitHub Actions schedule
└── requirements.txt
```

## Dashboard

The dashboard at your GitHub Pages URL shows all stored articles with a relevance slider. Articles marked "In digest" were included in the most recent email.

## Tuning the filter

If you're getting too many irrelevant articles, raise `relevance_threshold` in `settings.yaml`.
If you're getting too few, lower it or add more topic seeds to `topics.txt`.

Run `python scripts/run_pipeline.py --dry-run` to see scores without sending email.
