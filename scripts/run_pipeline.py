"""
run_pipeline.py — Master orchestrator for the AI PM digest pipeline.

Usage:
  python scripts/run_pipeline.py              # full run
  python scripts/run_pipeline.py --dry-run    # scrape + filter, no email/commit
  python scripts/run_pipeline.py --no-email   # run but skip email
"""

import argparse
import logging
import os
import sys
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

# Ensure scripts/ is on the path when invoked from project root
sys.path.insert(0, str(Path(__file__).parent))

from scrape import scrape_all
from filter import filter_articles
from store import init_db, get_connection, upsert_article, mark_included, \
    get_articles_since, get_recent_articles_for_dashboard, log_run, already_seen
from deliver import send_email, send_error_email, generate_dashboard

CONFIG_DIR = Path(__file__).parent.parent / "config"
PROJECT_ROOT = Path(__file__).parent.parent


def _load_settings() -> dict:
    with open(CONFIG_DIR / "settings.yaml") as f:
        return yaml.safe_load(f)


def _setup_logging(settings: dict) -> None:
    log_cfg = settings.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    log_file = log_cfg.get("file")
    if log_file:
        log_path = PROJECT_ROOT / log_file
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path))

    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


def run(dry_run: bool = False, no_email: bool = False) -> None:
    settings = _load_settings()
    _setup_logging(settings)
    logger = logging.getLogger("run_pipeline")

    logger.info("=" * 60)
    logger.info("AI PM Digest pipeline starting  (dry_run=%s)", dry_run)
    logger.info("=" * 60)

    digest_cfg = settings.get("digest", {})
    embed_cfg = settings.get("embedding", {})
    lookback_days: int = digest_cfg.get("lookback_days", 2)
    threshold: float = embed_cfg.get("relevance_threshold", 0.35)
    max_articles: int = digest_cfg.get("max_articles_per_digest", 25)

    # --- Init DB ---
    db_path = PROJECT_ROOT / "data" / "digest.db"
    init_db(db_path)
    conn = get_connection(db_path)

    articles_fetched = 0
    articles_kept = 0
    email_sent = False
    dashboard_updated = False
    error_message = None

    try:
        # --- Scrape ---
        logger.info("Step 1/4: Scraping sources...")
        raw_articles = scrape_all(lookback_days=lookback_days)
        articles_fetched = len(raw_articles)

        # Filter out already-seen URLs to avoid re-scoring
        new_articles = [a for a in raw_articles if not already_seen(conn, a["url"])]
        logger.info("New articles (not in DB): %d", len(new_articles))

        # --- Filter ---
        logger.info("Step 2/4: Filtering with embeddings...")
        kept, rejected = filter_articles(
            new_articles,
            threshold=threshold,
            max_articles=max_articles,
        )
        articles_kept = len(kept)

        # --- Store ---
        logger.info("Step 3/4: Storing results...")
        with conn:
            for article in kept + rejected:
                upsert_article(
                    conn,
                    url=article["url"],
                    title=article["title"],
                    summary=article.get("summary"),
                    source_name=article.get("source_name", ""),
                    category=article.get("category", "general"),
                    published=article.get("published"),
                    score=article.get("score"),
                    included=article in kept,
                )
            if not dry_run:
                mark_included(conn, [a["url"] for a in kept])

        logger.info("Stored %d articles.", len(kept) + len(rejected))

        # --- Deliver ---
        logger.info("Step 4/4: Delivering digest...")

        # Pull from DB for dashboard (includes historical articles)
        dash_articles = get_recent_articles_for_dashboard(
            conn,
            limit=settings.get("dashboard", {}).get("max_display_articles", 50),
        )
        # Convert Row objects to dicts
        dash_dicts = [dict(row) for row in dash_articles]

        dash_path = generate_dashboard(dash_dicts)
        dashboard_updated = True
        logger.info("Dashboard generated: %s", dash_path)

        if dry_run:
            logger.info("DRY RUN: skipping email delivery.")
        elif no_email:
            logger.info("--no-email: skipping email delivery.")
        else:
            # For email, use only the freshly kept articles
            kept_dicts = [dict(a) for a in kept]
            if kept_dicts:
                email_sent = send_email(kept_dicts)
            else:
                logger.info("No new articles to send — skipping email.")

        log_run(
            conn,
            articles_fetched=articles_fetched,
            articles_kept=articles_kept,
            email_sent=email_sent,
            dashboard_updated=dashboard_updated,
        )

        logger.info("Pipeline complete. Fetched=%d Kept=%d EmailSent=%s",
                    articles_fetched, articles_kept, email_sent)

    except Exception as e:
        error_message = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        logger.error("Pipeline failed:\n%s", error_message)

        try:
            log_run(
                conn,
                articles_fetched=articles_fetched,
                articles_kept=articles_kept,
                email_sent=False,
                dashboard_updated=dashboard_updated,
                error_message=error_message,
            )
        except Exception:
            pass

        if not dry_run:
            send_error_email(error_message)

        sys.exit(1)

    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI PM Digest pipeline")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scrape and filter but do not send email or mark articles as included",
    )
    parser.add_argument(
        "--no-email", action="store_true",
        help="Run full pipeline but skip email delivery",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run, no_email=args.no_email)
