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

def _markdown_to_html(md: str) -> str:
    """Convert the briefing markdown to clean HTML for email."""
    import re
    html_lines = []
    in_list = False
    for line in md.splitlines():
        # H2 sections
        if line.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            heading = line[3:].strip()
            html_lines.append(f'<h2 style="font-size:17px;font-weight:700;margin:24px 0 8px;color:#1a1a2e;border-bottom:1px solid #e0e0e0;padding-bottom:4px">{heading}</h2>')
        # Bullet points
        elif line.startswith("- "):
            if not in_list:
                html_lines.append('<ul style="margin:8px 0 8px 20px;padding:0">')
                in_list = True
            content = line[2:].strip()
            # Bold **text**
            content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
            html_lines.append(f'<li style="margin-bottom:6px;color:#333;font-size:14px">{content}</li>')
        # Empty line
        elif line.strip() == "":
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("")
        # Regular paragraph
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line)
            html_lines.append(f'<p style="margin:6px 0;font-size:14px;color:#333;line-height:1.6">{content}</p>')
    if in_list:
        html_lines.append("</ul>")
    return "\n".join(html_lines)


def _build_email_html(articles: list[dict], run_date: str, briefing: Optional[str] = None) -> str:
    if briefing:
        briefing_html = _markdown_to_html(briefing)
        body = f"""
  <div style="background:#f0f4ff;border-left:4px solid #1a1a2e;padding:14px 20px;margin-bottom:24px;border-radius:0 6px 6px 0">
    <p style="margin:0;font-size:12px;font-weight:600;color:#555;text-transform:uppercase;letter-spacing:.05em">AI Intelligence Briefing</p>
  </div>
  {briefing_html}"""
    else:
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
          <td style="padding:12px 8px;color:#888;font-size:12px;white-space:nowrap">#{i} &nbsp; {score_pct}%</td>
          <td style="padding:12px 8px">
            <a href="{a['url']}" style="color:#1a0dab;font-weight:bold;text-decoration:none">{a['title']}</a><br>
            <span style="color:#666;font-size:12px">{a.get('source_name','')}</span>
            {f'<span style="color:#aaa;font-size:12px"> &middot; {pub}</span>' if pub else ''}
            {f'<p style="margin:4px 0 0;color:#444;font-size:13px">{a["summary"][:200]}…</p>' if a.get("summary") else ''}
          </td>
        </tr>"""
        body = f'<table style="width:100%;border-collapse:collapse">{rows}</table>'

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>AI PM Digest</title></head>
<body style="font-family:Georgia,serif;max-width:700px;margin:0 auto;padding:20px;color:#222">
  <h1 style="font-size:24px;border-bottom:3px solid #1a1a2e;padding-bottom:8px">
    AI PM Digest &mdash; {run_date}
  </h1>
  <p style="color:#666;font-size:13px;margin-bottom:20px">
    {len(articles)} articles &middot; Powered by Claude + all-MiniLM-L6-v2
  </p>
  {body}
  <hr style="margin-top:32px;border:none;border-top:1px solid #ddd">
  <p style="color:#aaa;font-size:11px;text-align:center">ai-pm-digest</p>
</body>
</html>"""


def _build_email_text(articles: list[dict], run_date: str, briefing: Optional[str] = None) -> str:
    if briefing:
        return f"AI PM Digest — {run_date}\n{'=' * 50}\n\n{briefing}"
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
    briefing: Optional[str] = None,
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
        "html": _build_email_html(articles, run_date, briefing=briefing),
        "text": _build_email_text(articles, run_date, briefing=briefing),
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
    import html as html_mod
    score = a.get("score", 0)
    pub = a.get("published", "")
    pub_display = ""
    if pub:
        try:
            pub_display = datetime.fromisoformat(pub).strftime("%b %d, %Y")
        except Exception:
            pub_display = pub[:10]

    summary_html = ""
    summary_text = (a.get("summary") or "")
    if summary_text:
        trunc = summary_text[:300]
        if len(summary_text) > 300:
            trunc += "…"
        summary_html = f'<p class="summary">{trunc}</p>'

    included_badge = '<span class="badge included">In digest</span>' if a.get("included") else ""

    # JSON payload for save — escape for embedding in a data attribute
    save_data = html_mod.escape(json.dumps({
        "url": a.get("url", ""),
        "title": a.get("title", ""),
        "source": a.get("source_name", ""),
        "score": score,
        "summary": summary_text[:300],
        "published": pub_display,
    }, ensure_ascii=False), quote=True)

    return f"""
    <article class="card" data-score="{score}" data-url="{html_mod.escape(a.get('url',''), quote=True)}">
      <div class="card-meta">
        <span class="source">{a.get('source_name','')}</span>
        {f'<span class="pubdate">{pub_display}</span>' if pub_display else ''}
        <span class="score-bar">{_score_bar(score)}</span>
        {included_badge}
        <button class="save-btn" data-article="{save_data}" onclick="toggleSave(this)" title="Save article">&#9733;</button>
      </div>
      <h3 class="card-title">
        <a href="{a.get('url','')}" target="_blank" rel="noopener">{a.get('title','')}</a>
      </h3>
      {summary_html}
    </article>"""


def _briefing_to_dashboard_html(md: str) -> str:
    """Convert the briefing markdown to styled HTML sections for the dashboard."""
    import re
    html_parts = []
    in_list = False
    for line in md.splitlines():
        if line.startswith("## "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            heading = line[3:].strip()
            # Highlight the first section specially
            if "Today's Signal" in heading:
                html_parts.append(f'<h2 class="briefing-signal-heading">{heading}</h2>')
            else:
                html_parts.append(f'<h2 class="briefing-heading">{heading}</h2>')
        elif line.startswith("- "):
            if not in_list:
                html_parts.append('<ul class="briefing-list">')
                in_list = True
            content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line[2:])
            html_parts.append(f"<li>{content}</li>")
        elif line.strip() == "":
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append("")
        else:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line)
            html_parts.append(f"<p>{content}</p>")
    if in_list:
        html_parts.append("</ul>")
    return "\n".join(html_parts)


def generate_dashboard(
    articles: list[dict],
    briefing: Optional[str] = None,
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

    briefing_section = ""
    if briefing:
        briefing_html = _briefing_to_dashboard_html(briefing)
        briefing_section = f"""
  <section class="briefing-panel" id="briefing">
    <div class="briefing-label">AI Intelligence Briefing</div>
    <div class="briefing-body">
      {briefing_html}
    </div>
  </section>"""

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

    /* Briefing panel */
    .briefing-panel {{ max-width: 900px; margin: 24px auto 0; padding: 0 16px; }}
    .briefing-label {{ font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
                       letter-spacing: .08em; color: #888; margin-bottom: 4px; }}
    .briefing-body {{ background: #fff; border-radius: 10px; padding: 28px 32px;
                      box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
    .briefing-signal-heading {{ font-size: 1.1rem; font-weight: 700; color: #1a1a2e;
                                 border-left: 4px solid #f59e0b; padding-left: 12px;
                                 margin: 0 0 12px; }}
    .briefing-heading {{ font-size: 1rem; font-weight: 700; color: #1a1a2e;
                         border-bottom: 1px solid #eee; padding-bottom: 6px;
                         margin: 24px 0 10px; }}
    .briefing-body p {{ font-size: 0.925rem; color: #333; margin: 6px 0; line-height: 1.65; }}
    .briefing-list {{ margin: 8px 0 8px 20px; }}
    .briefing-list li {{ font-size: 0.9rem; color: #444; margin-bottom: 8px; line-height: 1.55; }}

    /* Article list */
    .controls {{ padding: 16px 32px; background: #fff; border-bottom: 1px solid #ddd;
                 display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
                 margin-top: 24px; }}
    .controls label {{ font-size: 0.85rem; color: #666; }}
    .controls input[type=range] {{ width: 160px; }}
    #threshold-val {{ font-weight: bold; color: #1a1a2e; }}
    .section-title {{ max-width: 900px; margin: 24px auto 8px; padding: 0 16px;
                      font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
                      letter-spacing: .08em; color: #888; }}
    main {{ max-width: 900px; margin: 0 auto 24px; padding: 0 16px; }}
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
    .save-btn {{ background: none; border: none; cursor: pointer; font-size: 1.1rem;
                 color: #ccc; padding: 0 2px; line-height: 1; transition: color 0.15s; }}
    .save-btn:hover {{ color: #f59e0b; }}
    .save-btn.saved {{ color: #f59e0b; }}
    .hidden {{ display: none !important; }}
    footer {{ text-align: center; padding: 32px; font-size: 0.8rem; color: #aaa; }}
    .nav-link {{ float: right; color: #fff; opacity: 0.8; font-size: 0.85rem;
                 text-decoration: none; border: 1px solid rgba(255,255,255,0.3);
                 padding: 4px 12px; border-radius: 4px; }}
    .nav-link:hover {{ opacity: 1; background: rgba(255,255,255,0.1); }}
  </style>
</head>
<body>
  <header>
    <a href="saved.html" class="nav-link">&#9733; Saved Articles</a>
    <h1>{title}</h1>
    <div class="meta">Updated {run_date}</div>
    <div class="stats">
      <div class="stat">
        <div class="stat-num">{total}</div>
        <div class="stat-label">Articles</div>
      </div>
      <div class="stat">
        <div class="stat-num">{in_digest}</div>
        <div class="stat-label">In digest</div>
      </div>
    </div>
  </header>
  {briefing_section}

  <div class="section-title">All Articles</div>
  <div class="controls" style="margin-top:0">
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
    ai-pm-digest &middot; Claude + sentence-transformers all-MiniLM-L6-v2
  </footer>

  <script>
    const STORAGE_KEY = 'aipm_saved_articles';

    function getSaved() {{
      try {{ return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{{}}'); }}
      catch {{ return {{}}; }}
    }}

    function toggleSave(btn) {{
      const article = JSON.parse(btn.dataset.article);
      const saved = getSaved();
      if (saved[article.url]) {{
        delete saved[article.url];
        btn.classList.remove('saved');
        btn.title = 'Save article';
      }} else {{
        article.savedAt = new Date().toISOString();
        saved[article.url] = article;
        btn.classList.add('saved');
        btn.title = 'Saved';
      }}
      localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
    }}

    function restoreSaveStates() {{
      const saved = getSaved();
      document.querySelectorAll('.save-btn').forEach(btn => {{
        const article = JSON.parse(btn.dataset.article);
        if (saved[article.url]) {{
          btn.classList.add('saved');
          btn.title = 'Saved';
        }}
      }});
    }}

    function filterCards(val) {{
      document.getElementById('threshold-val').textContent = val + '%';
      const threshold = parseInt(val) / 100;
      document.querySelectorAll('.card').forEach(card => {{
        const score = parseFloat(card.dataset.score);
        card.classList.toggle('hidden', score < threshold);
      }});
    }}

    restoreSaveStates();
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
