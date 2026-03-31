"""
deliver.py — Email delivery via Resend API + static HTML dashboard generation.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import yaml

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
PROJECT_ROOT = Path(__file__).parent.parent


def _load_settings() -> dict:
    with open(CONFIG_DIR / "settings.yaml") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def _build_email_html(articles: list[dict], run_date: str) -> str:
    rows = ""
    for i, a in enumerate(articles, 1):
        score_pct = int(a.get("score", 0) * 100)
        pub = a.get("published", "")
        if pub:
            try:
                pub = datetime.fromisoformat(pub).strftime("%b %d")
            except Exception:
                pub = pub[:10]
        rows += f"""
        <tr style="border-bottom:1px solid #eee">
          <td style="padding:12px 8px;color:#888;font-size:12px;white-space:nowrap">
            #{i} &nbsp; {score_pct}%
          </td>
          <td style="padding:12px 8px">
            <a href="{a['url']}" style="color:#1a0dab;font-weight:bold;text-decoration:none">
              {a['title']}
            </a><br>
            <span style="color:#666;font-size:12px">{a.get('source_name','')}</span>
            {f'<span style="color:#aaa;font-size:12px"> &middot; {pub}</span>' if pub else ''}
            {f'<p style="margin:4px 0 0;color:#444;font-size:13px">{a["summary"][:200]}…</p>' if a.get("summary") else ''}
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>AI PM Digest</title></head>
<body style="font-family:Georgia,serif;max-width:700px;margin:0 auto;padding:20px;color:#222">
  <h1 style="font-size:24px;border-bottom:3px solid #333;padding-bottom:8px">
    AI PM Digest &mdash; {run_date}
  </h1>
  <p style="color:#666;font-size:13px">
    {len(articles)} articles selected by semantic relevance to your topic focus.
  </p>
  <table style="width:100%;border-collapse:collapse">
    {rows}
  </table>
  <hr style="margin-top:32px;border:none;border-top:1px solid #ddd">
  <p style="color:#aaa;font-size:11px;text-align:center">
    Powered by ai-pm-digest &middot; sentence-transformers all-MiniLM-L6-v2
  </p>
</body>
</html>"""


def _build_email_text(articles: list[dict], run_date: str) -> str:
    lines = [f"AI PM Digest — {run_date}", "=" * 50, ""]
    for i, a in enumerate(articles, 1):
        score_pct = int(a.get("score", 0) * 100)
        lines.append(f"{i}. [{score_pct}%] {a['title']}")
        lines.append(f"   Source: {a.get('source_name', '')}")
        lines.append(f"   URL: {a['url']}")
        if a.get("summary"):
            lines.append(f"   {a['summary'][:150]}…")
        lines.append("")
    return "\n".join(lines)


def send_email(
    articles: list[dict],
    api_key: Optional[str] = None,
    from_address: Optional[str] = None,
    to_address: Optional[str] = None,
    subject_prefix: Optional[str] = None,
) -> bool:
    """Send digest email via Resend API. Returns True on success."""
    settings = _load_settings()
    email_cfg = settings.get("email", {})

    api_key = api_key or os.environ.get("RESEND_API_KEY") or email_cfg.get("resend_api_key")
    from_address = from_address or email_cfg.get("from_address", "digest@example.com")
    to_address = to_address or email_cfg.get("to_address", "you@example.com")
    subject_prefix = subject_prefix or email_cfg.get("subject_prefix", "AI PM Digest")

    if not api_key:
        logger.error("No RESEND_API_KEY found. Skipping email delivery.")
        return False

    run_date = datetime.now(timezone.utc).strftime("%B %d, %Y")
    subject = f"{subject_prefix} — {run_date} ({len(articles)} articles)"

    payload = {
        "from": from_address,
        "to": [to_address],
        "subject": subject,
        "html": _build_email_html(articles, run_date),
        "text": _build_email_text(articles, run_date),
    }

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                content=json.dumps(payload),
            )
        resp.raise_for_status()
        logger.info("Email sent successfully. ID: %s", resp.json().get("id"))
        return True
    except httpx.HTTPStatusError as e:
        logger.error("Resend API error %s: %s", e.response.status_code, e.response.text)
        return False
    except Exception as e:
        logger.error("Email delivery failed: %s", e)
        return False


def send_error_email(error_msg: str, api_key: Optional[str] = None) -> None:
    """Send a plain-text error notification email."""
    settings = _load_settings()
    email_cfg = settings.get("email", {})

    api_key = api_key or os.environ.get("RESEND_API_KEY") or email_cfg.get("resend_api_key")
    if not api_key:
        return

    from_address = email_cfg.get("from_address", "digest@example.com")
    to_address = email_cfg.get("to_address", "you@example.com")
    run_date = datetime.now(timezone.utc).strftime("%B %d, %Y %H:%M UTC")

    payload = {
        "from": from_address,
        "to": [to_address],
        "subject": f"[ai-pm-digest] Pipeline error — {run_date}",
        "text": f"The ai-pm-digest pipeline failed on {run_date}.\n\nError:\n{error_msg}\n\nCheck GitHub Actions logs for details.",
    }

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                content=json.dumps(payload),
            )
        resp.raise_for_status()
        logger.info("Error notification email sent.")
    except Exception as e:
        logger.error("Failed to send error notification: %s", e)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def _score_bar(score: float) -> str:
    pct = int(score * 100)
    color = "#2ecc71" if pct >= 60 else "#f39c12" if pct >= 40 else "#e74c3c"
    return f'<span style="display:inline-block;width:{pct}px;height:8px;background:{color};border-radius:3px;vertical-align:middle" title="{pct}% relevance"></span> {pct}%'


def _article_card(a: dict, idx: int) -> str:
    score = a.get("score", 0)
    pub = a.get("published", "")
    if pub:
        try:
            pub = datetime.fromisoformat(pub).strftime("%b %d, %Y")
        except Exception:
            pub = pub[:10]

    summary_html = ""
    if a.get("summary"):
        trunc = a["summary"][:300]
        if len(a["summary"]) > 300:
            trunc += "…"
        summary_html = f'<p class="summary">{trunc}</p>'

    included_badge = '<span class="badge included">In digest</span>' if a.get("included") else ""

    return f"""
    <article class="card" data-score="{score}">
      <div class="card-meta">
        <span class="source">{a.get('source_name','')}</span>
        {f'<span class="pubdate">{pub}</span>' if pub else ''}
        <span class="score-bar">{_score_bar(score)}</span>
        {included_badge}
      </div>
      <h3 class="card-title">
        <a href="{a['url']}" target="_blank" rel="noopener">{a['title']}</a>
      </h3>
      {summary_html}
    </article>"""


def generate_dashboard(
    articles: list[dict],
    output_path: Optional[Path] = None,
    title: Optional[str] = None,
) -> Path:
    """Write static HTML dashboard. Returns the output path."""
    settings = _load_settings()
    dash_cfg = settings.get("dashboard", {})
    output_path = output_path or (PROJECT_ROOT / dash_cfg.get("output_path", "dashboard/index.html"))
    title = title or dash_cfg.get("title", "AI PM Daily Digest")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    run_date = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    cards_html = "\n".join(_article_card(a, i) for i, a in enumerate(articles, 1))
    total = len(articles)
    in_digest = sum(1 for a in articles if a.get("included"))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #f5f5f5; color: #222; line-height: 1.5; }}
    header {{ background: #1a1a2e; color: #fff; padding: 24px 32px; }}
    header h1 {{ font-size: 1.6rem; font-weight: 700; }}
    header .meta {{ font-size: 0.85rem; color: #aaa; margin-top: 4px; }}
    .stats {{ display: flex; gap: 24px; margin-top: 12px; }}
    .stat {{ background: rgba(255,255,255,0.1); border-radius: 6px; padding: 8px 16px; }}
    .stat-num {{ font-size: 1.4rem; font-weight: 700; }}
    .stat-label {{ font-size: 0.75rem; color: #ccc; }}
    .controls {{ padding: 16px 32px; background: #fff; border-bottom: 1px solid #ddd;
                 display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
    .controls label {{ font-size: 0.85rem; color: #666; }}
    .controls input[type=range] {{ width: 160px; }}
    #threshold-val {{ font-weight: bold; color: #1a1a2e; }}
    main {{ max-width: 900px; margin: 24px auto; padding: 0 16px; }}
    .card {{ background: #fff; border-radius: 8px; padding: 16px 20px; margin-bottom: 12px;
             box-shadow: 0 1px 3px rgba(0,0,0,0.08); transition: box-shadow 0.15s; }}
    .card:hover {{ box-shadow: 0 3px 12px rgba(0,0,0,0.12); }}
    .card-meta {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
                  font-size: 0.78rem; color: #888; margin-bottom: 6px; }}
    .source {{ background: #e8f4fd; color: #1565c0; padding: 2px 8px; border-radius: 12px;
               font-weight: 500; }}
    .pubdate {{ color: #aaa; }}
    .score-bar {{ margin-left: auto; }}
    .badge.included {{ background: #e8f5e9; color: #2e7d32; padding: 2px 8px;
                        border-radius: 12px; font-weight: 500; }}
    .card-title a {{ color: #1a1a2e; text-decoration: none; font-size: 1.05rem;
                      font-weight: 600; line-height: 1.35; }}
    .card-title a:hover {{ color: #1565c0; text-decoration: underline; }}
    .summary {{ color: #555; font-size: 0.875rem; margin-top: 6px; }}
    .hidden {{ display: none !important; }}
    footer {{ text-align: center; padding: 32px; font-size: 0.8rem; color: #aaa; }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <div class="meta">Updated {run_date}</div>
    <div class="stats">
      <div class="stat">
        <div class="stat-num">{total}</div>
        <div class="stat-label">Articles</div>
      </div>
      <div class="stat">
        <div class="stat-num">{in_digest}</div>
        <div class="stat-label">In email digest</div>
      </div>
    </div>
  </header>

  <div class="controls">
    <label>
      Min relevance:
      <input type="range" id="threshold" min="0" max="100" value="0"
             oninput="filterCards(this.value)">
      <span id="threshold-val">0%</span>
    </label>
  </div>

  <main id="articles">
    {cards_html}
  </main>

  <footer>
    ai-pm-digest &middot; Powered by sentence-transformers all-MiniLM-L6-v2
  </footer>

  <script>
    function filterCards(val) {{
      document.getElementById('threshold-val').textContent = val + '%';
      const threshold = parseInt(val) / 100;
      document.querySelectorAll('.card').forEach(card => {{
        const score = parseFloat(card.dataset.score);
        card.classList.toggle('hidden', score < threshold);
      }});
    }}
  </script>
</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")
    logger.info("Dashboard written to %s (%d articles)", output_path, total)
    return output_path


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # Quick smoke test: generate a dashboard with dummy data
    dummy = [
        {"url": "https://example.com/1", "title": "AI Tools for PMs", "summary": "A look at emerging AI tools.",
         "source_name": "Test Source", "category": "ai", "published": "2025-01-01T00:00:00", "score": 0.72, "included": True},
        {"url": "https://example.com/2", "title": "LLM in Agile Teams", "summary": "How LLMs help agile teams.",
         "source_name": "Test Blog", "category": "ai", "published": "2025-01-01T00:00:00", "score": 0.55, "included": False},
    ]
    path = generate_dashboard(dummy)
    print(f"Dashboard generated at: {path}")
