"""
filter.py — Semantic relevance filtering using sentence-transformers.

Embeds each article's title + summary and computes cosine similarity
against a centroid embedding built from topics.txt. Articles above
the configured threshold are kept.
"""

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import yaml

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
_model = None  # module-level cache so the model loads once per process


def _get_model(model_name: str):
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model '%s'...", model_name)
        _model = SentenceTransformer(model_name)
        logger.info("Model loaded.")
    return _model


def _load_topics() -> list[str]:
    topics_path = CONFIG_DIR / "topics.txt"
    lines = topics_path.read_text().splitlines()
    return [l.strip() for l in lines if l.strip()]


def _load_settings() -> dict:
    with open(CONFIG_DIR / "settings.yaml") as f:
        return yaml.safe_load(f)


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def build_topic_centroid(model, topics: list[str]) -> np.ndarray:
    """Embed all topic lines and average them into a single centroid vector."""
    embeddings = model.encode(topics, convert_to_numpy=True, show_progress_bar=False)
    centroid = embeddings.mean(axis=0)
    # Normalise so cosine similarity == dot product
    centroid = centroid / np.linalg.norm(centroid)
    return centroid


def score_articles(
    articles: list[dict],
    model_name: Optional[str] = None,
    threshold: Optional[float] = None,
) -> list[dict]:
    """
    Add a 'score' key to each article dict (cosine similarity vs topic centroid).
    Returns all articles with scores, sorted descending.
    """
    settings = _load_settings()
    model_name = model_name or settings["embedding"]["model"]
    threshold = threshold if threshold is not None else settings["embedding"]["relevance_threshold"]

    topics = _load_topics()
    model = _get_model(model_name)

    logger.info("Building topic centroid from %d topic lines...", len(topics))
    centroid = build_topic_centroid(model, topics)

    # Build text to embed: title + summary (truncated to avoid token overflow)
    texts = []
    for a in articles:
        combined = f"{a.get('title', '')}. {a.get('summary', '')}".strip()
        texts.append(combined[:512])

    logger.info("Embedding %d articles...", len(texts))
    embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=False, batch_size=64)

    scored = []
    for article, emb in zip(articles, embeddings):
        score = _cosine_similarity(emb, centroid)
        scored.append({**article, "score": round(score, 4)})

    scored.sort(key=lambda x: x["score"], reverse=True)
    above = sum(1 for a in scored if a["score"] >= threshold)
    logger.info(
        "Scored %d articles. %d above threshold %.2f.",
        len(scored), above, threshold,
    )
    return scored


def filter_articles(
    articles: list[dict],
    model_name: Optional[str] = None,
    threshold: Optional[float] = None,
    max_articles: Optional[int] = None,
) -> tuple[list[dict], list[dict]]:
    """
    Score and split articles into (kept, rejected).
    `kept` is capped at max_articles if provided.
    """
    settings = _load_settings()
    threshold = threshold if threshold is not None else settings["embedding"]["relevance_threshold"]
    max_articles = max_articles or settings["digest"]["max_articles_per_digest"]

    scored = score_articles(articles, model_name=model_name, threshold=threshold)
    kept = [a for a in scored if a["score"] >= threshold]
    rejected = [a for a in scored if a["score"] < threshold]

    if len(kept) > max_articles:
        rejected = kept[max_articles:] + rejected
        kept = kept[:max_articles]

    logger.info("Kept: %d  Rejected: %d", len(kept), len(rejected))
    return kept, rejected


if __name__ == "__main__":
    import json
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from scrape import scrape_all

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raw = scrape_all(lookback_days=2)
    kept, rejected = filter_articles(raw)

    print(f"\n--- TOP {len(kept)} ARTICLES ---")
    for a in kept:
        print(f"  {a['score']:.3f}  [{a['source_name']}]  {a['title'][:70]}")

    print(f"\n--- REJECTED: {len(rejected)} articles (showing bottom 5) ---")
    for a in rejected[-5:]:
        print(f"  {a['score']:.3f}  [{a['source_name']}]  {a['title'][:70]}")
