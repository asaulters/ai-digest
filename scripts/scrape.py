"""
scrape.py — Fetches articles from RSS feeds and Hacker News Algolia API.
Returns a list of raw article dicts for downstream filtering.
"""

import logging
import ssl
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional

import certifi
import feedparser
import httpx
import yaml

logger = logging.getLogger(__name__)

CONFIG_DIR = __import__("pathlib").Path(__file__).parent.parent / "config"

# Use certifi's CA bundle so SSL works on macOS without system cert config.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def load_sources() -> dict:
    with open(CONFIG_DIR / "sources.yaml") as f:
        return yaml.safe_load(f)


def _parse_date(entry: feedparser.FeedParserDict) -> Optional[str]:
    """Try several feedparser date fields; return ISO string or None."""
    for field in ("published", "updated", "created"):
        raw = entry.get(f"{field}_parsed") or entry.get(field)
        if raw is None:
            continue
        if isinstance(raw, time.struct_time):
            try:
                dt = datetime(*raw[:6], tzinfo=timezone.utc)
                return dt.isoformat()
            except Exception:
                continue
        if isinstance(raw, str):
            try:
                return parsedate_to_datetime(raw).isoformat()
            except Exception:
                try:
                    return datetime.fromisoformat(raw).isoformat()
                except Exception:
                    continue
    return None


def _entry_to_article(entry: feedparser.FeedParserDict, source: dict) -> dict:
    summary = (
        entry.get("summary")
        or entry.get("content", [{}])[0].get("value", "")
        or ""
    )
    # Strip HTML tags crudely for embedding input
    import re
    summary = re.sub(r"<[^>]+>", " ", summary).strip()
    summary = re.sub(r"\s+", " ", summary)[:2000]

    return {
        "url": entry.get("link", ""),
        "title": entry.get("title", "").strip(),
        "summary": summary,
        "source_name": source["name"],
        "category": source.get("category", "general"),
        "published": _parse_date(entry),
    }


def fetch_rss_feed(source: dict, lookback_days: int = 2) -> list[dict]:
    """Fetch a single RSS/Atom feed. Returns list of article dicts."""
    url = source["url"]
    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    try:
        import urllib.request
        https_handler = urllib.request.HTTPSHandler(context=_SSL_CONTEXT)
        feed = feedparser.parse(url, handlers=[https_handler])
        if feed.bozo and feed.bozo_exception:
            logger.warning("Feed parse warning for %s: %s", url, feed.bozo_exception)

        for entry in feed.entries:
            article = _entry_to_article(entry, source)
            if not article["url"] or not article["title"]:
                continue

            pub = article.get("published")
            if pub:
                try:
                    pub_dt = datetime.fromisoformat(pub)
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pass  # If we can't parse the date, include it anyway

            articles.append(article)

        logger.info("  %s: %d articles", source["name"], len(articles))
    except Exception as e:
        logger.error("Failed to fetch feed %s: %s", url, e)

    return articles


def fetch_hn_algolia(config: dict, lookback_hours: int = 48) -> list[dict]:
    """Query Hacker News via Algolia search API for each keyword."""
    if not config.get("enabled", False):
        return []

    articles = []
    seen_urls: set[str] = set()
    min_points = config.get("min_points", 30)
    base_url = "https://hn.algolia.com/api/v1/search"
    numeric_filters = f"points>={min_points},created_at_i>{int(time.time()) - lookback_hours * 3600}"

    with httpx.Client(timeout=15) as client:
        for keyword in config.get("keywords", []):
            try:
                resp = client.get(base_url, params={
                    "query": keyword,
                    "tags": "story",
                    "numericFilters": numeric_filters,
                    "hitsPerPage": 20,
                })
                resp.raise_for_status()
                data = resp.json()

                for hit in data.get("hits", []):
                    url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    title = hit.get("title", "").strip()
                    if not title:
                        continue

                    pub = None
                    ts = hit.get("created_at_i")
                    if ts:
                        pub = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

                    articles.append({
                        "url": url,
                        "title": title,
                        "summary": f"HN discussion: {title}. Points: {hit.get('points', 0)}, Comments: {hit.get('num_comments', 0)}",
                        "source_name": f"Hacker News ({keyword})",
                        "category": "hn",
                        "published": pub,
                    })

                logger.info("  HN Algolia '%s': %d hits", keyword, len(data.get("hits", [])))
                time.sleep(0.3)  # polite rate limiting

            except Exception as e:
                logger.error("HN Algolia fetch failed for '%s': %s", keyword, e)

    return articles


def scrape_all(lookback_days: int = 2) -> list[dict]:
    """Fetch from all configured sources. Returns deduplicated article list."""
    sources_config = load_sources()
    all_articles: list[dict] = []
    seen_urls: set[str] = set()

    rss_feeds = sources_config.get("rss_feeds", [])
    logger.info("Fetching %d RSS feeds...", len(rss_feeds))
    for source in rss_feeds:
        for article in fetch_rss_feed(source, lookback_days=lookback_days):
            if article["url"] and article["url"] not in seen_urls:
                seen_urls.add(article["url"])
                all_articles.append(article)

    hn_config = sources_config.get("hacker_news_algolia", {})
    if hn_config.get("enabled"):
        logger.info("Fetching HN Algolia...")
        for article in fetch_hn_algolia(hn_config, lookback_hours=lookback_days * 24):
            if article["url"] and article["url"] not in seen_urls:
                seen_urls.add(article["url"])
                all_articles.append(article)

    logger.info("Total raw articles fetched: %d", len(all_articles))
    return all_articles


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    results = scrape_all(lookback_days=2)
    print(f"\nFetched {len(results)} articles.")
    for a in results[:5]:
        print(f"  [{a['source_name']}] {a['title'][:80]}")
