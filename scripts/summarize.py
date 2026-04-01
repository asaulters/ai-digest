"""
summarize.py — Calls the Claude API to produce a structured daily intelligence briefing.

Takes the list of filtered articles and returns a markdown briefing string with
six structured sections useful to someone transitioning into AI product management.

Cost estimate (as of 2025):
  ~3,000 tokens input + ~2,000 tokens output per day using claude-sonnet-4-20250514
  ≈ $0.009 input + $0.030 output ≈ $0.04/day (~$1.20/month)
  Well within acceptable range for a personal digest tool.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import anthropic
import yaml

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"

SYSTEM_PROMPT = """You are an AI intelligence analyst briefing a professional who is:
- Transitioning into Applied AI / AI Product Management
- Working in project management and compliance workflows today
- Building skills in prompt engineering, LLM systems, and AI product thinking
- Trying to stay ahead of AI tools, workflows, and industry shifts
- Goal: extract signal that helps them learn, build, and stay marketable

Here are today's articles pulled from curated sources. For each one \
you have the title, source, relevance score, and a summary snippet.

Your job is to produce a structured daily briefing with these exact sections:

## Today's Signal (2-3 sentences)
What is the most important thing happening in AI today as it relates \
to project management, AI product work, and applied workflows? \
Write this like a senior analyst briefing a smart generalist.

## Tools & Releases Worth Knowing
For each genuinely new tool, model, or product release in today's \
articles, write:
- Tool name and what it does in one sentence
- Why it matters for AI PM or workflow automation specifically
- Skill or action: one concrete thing to do or learn from this
Skip anything that is not a real tool or release.

## Workflow & Use Case Ideas
Extract any workflow patterns, use cases, or application ideas from \
today's articles that could apply to:
- AI-augmented project management
- Building or prompting AI products
- Automating compliance, documentation, or operational workflows
Write each as: "Idea: [what it is] — Why it matters: [one sentence]"

## What The Leaders Are Doing
Pull out what senior practitioners, founders, or companies are \
actually building or changing in their workflows right now. \
This is about adoption patterns, not announcements.

## Skill to Build This Week
Based on today's articles, what is one specific skill, concept, \
or tool that this person should spend 30-60 minutes learning \
this week? Be specific — name the thing, explain why now, \
and suggest one concrete starting point.

## Articles In Full Digest
List each article with: title, source, score, and 2-sentence summary."""


def _load_settings() -> dict:
    with open(CONFIG_DIR / "settings.yaml") as f:
        return yaml.safe_load(f)


def _build_article_block(articles: list[dict]) -> str:
    """Format articles into a compact text block for the prompt."""
    lines = []
    for i, a in enumerate(articles, 1):
        score_pct = int(a.get("score", 0) * 100)
        title = a.get("title", "").strip()
        source = a.get("source_name", "")
        summary = (a.get("summary") or "")[:400].strip()
        url = a.get("url", "")
        lines.append(
            f"{i}. [{score_pct}% relevance] {title}\n"
            f"   Source: {source}\n"
            f"   URL: {url}\n"
            f"   Snippet: {summary}"
        )
    return "\n\n".join(lines)


def generate_briefing(
    articles: list[dict],
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> Optional[str]:
    """
    Call Claude API to produce a structured briefing from the article list.

    Returns the briefing markdown string, or None if summarization is disabled
    or the API call fails (caller should fall back to raw article list).
    """
    settings = _load_settings()
    summ_cfg = settings.get("summarization", {})

    if not summ_cfg.get("enabled", True):
        logger.info("Summarization disabled in settings. Skipping.")
        return None

    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set. Cannot generate briefing.")
        return None

    model = model or summ_cfg.get("model", "claude-sonnet-4-20250514")
    max_tokens = max_tokens or summ_cfg.get("max_tokens", 2000)

    if not articles:
        logger.warning("No articles to summarize.")
        return None

    article_block = _build_article_block(articles)
    user_message = (
        f"Here are today's {len(articles)} filtered articles for the AI PM digest:\n\n"
        f"{article_block}\n\n"
        "Please produce the structured briefing now."
    )

    logger.info(
        "Calling Claude API (model=%s, articles=%d, max_tokens=%d)...",
        model, len(articles), max_tokens,
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        briefing = next(
            (block.text for block in response.content if block.type == "text"), ""
        )
        usage = response.usage
        logger.info(
            "Briefing generated. Input tokens: %d, Output tokens: %d",
            usage.input_tokens, usage.output_tokens,
        )
        return briefing.strip()

    except anthropic.AuthenticationError:
        logger.error("Invalid ANTHROPIC_API_KEY.")
        return None
    except anthropic.RateLimitError as e:
        logger.error("Claude API rate limited: %s", e)
        return None
    except anthropic.APIStatusError as e:
        logger.error("Claude API error %s: %s", e.status_code, e.message)
        return None
    except Exception as e:
        logger.error("Unexpected error calling Claude API: %s", e)
        return None


if __name__ == "__main__":
    """
    Dry-run test: pull the most recent articles from digest.db,
    call Claude, and print the full briefing to stdout.
    Does NOT update the dashboard or send any email.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from store import get_connection, get_recent_articles_for_dashboard

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    conn = get_connection()
    # Grab the most recent included articles first, then fill with top-scored
    rows = conn.execute(
        """
        SELECT * FROM articles
        WHERE score IS NOT NULL
        ORDER BY included DESC, score DESC
        LIMIT 25
        """
    ).fetchall()
    conn.close()

    if not rows:
        print("No articles in digest.db. Run the pipeline first.")
        sys.exit(1)

    articles = [dict(r) for r in rows]
    print(f"Summarising {len(articles)} articles from digest.db...\n")

    briefing = generate_briefing(articles)
    if briefing:
        print("=" * 70)
        print(briefing)
        print("=" * 70)
    else:
        print("ERROR: No briefing returned. Check ANTHROPIC_API_KEY and logs.")
        sys.exit(1)
