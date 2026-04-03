"""
ai_recommendations.py
=====================
Fetches live AI / HR best-practice recommendations from public RSS feeds
so that super-admins always see up-to-date industry guidance alongside
their internal analytics.

Sources (all publicly accessible RSS – no API key required):
  • SHRM          – Society for Human Resource Management
  • HR Dive       – Daily HR news & analysis
  • MIT Sloan     – Management Review
  • HBR           – Harvard Business Review (management feed)

Caching
-------
Results are cached for 6 hours via Streamlit's @st.cache_data so the app
never makes a live web request on every page render.
"""

import xml.etree.ElementTree as ET
from datetime import datetime
import requests

# ---------------------------------------------------------------------------
# RSS feed catalogue  (title, url, category tag)
# ---------------------------------------------------------------------------
_FEEDS = [
    (
        "SHRM",
        "https://www.shrm.org/rss/feeds/all-news-rss.xml",
        "HR Management",
    ),
    (
        "HR Dive",
        "https://www.hrdive.com/feeds/news/",
        "HR News",
    ),
    (
        "MIT Sloan Review",
        "https://sloanreview.mit.edu/feed/",
        "Leadership & Strategy",
    ),
    (
        "Harvard Business Review",
        "https://feeds.hbr.org/harvardbusiness",
        "Business Leadership",
    ),
]

_REQUEST_TIMEOUT = 8          # seconds per feed request
_MAX_ITEMS_PER_FEED = 4       # items returned per source


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_rss(xml_text: str, source_name: str, category: str) -> list[dict]:
    """Parse a raw RSS XML string and return a list of article dicts."""
    items = []
    try:
        root = ET.fromstring(xml_text)
        # Handle both RSS 2.0 and Atom-based heuristics
        channel = root.find("channel")
        entries = channel.findall("item") if channel is not None else root.findall(
            ".//{http://www.w3.org/2005/Atom}entry"
        )

        for entry in entries[:_MAX_ITEMS_PER_FEED]:
            # RSS 2.0 paths
            title_el = entry.find("title")
            link_el = entry.find("link")
            pub_el = entry.find("pubDate") or entry.find("published")
            desc_el = entry.find("description") or entry.find(
                "{http://www.w3.org/2005/Atom}summary"
            )

            title = (title_el.text or "").strip() if title_el is not None else ""
            link = (link_el.text or "").strip() if link_el is not None else ""

            # Atom <link href="…">
            if not link and link_el is not None:
                link = link_el.get("href", "")

            pub_raw = (pub_el.text or "").strip() if pub_el is not None else ""
            desc = (desc_el.text or "").strip() if desc_el is not None else ""
            # Strip HTML tags from description crudely
            import re
            desc = re.sub(r"<[^>]+>", "", desc)[:200]

            if title:
                items.append(
                    {
                        "source": source_name,
                        "category": category,
                        "title": title,
                        "link": link,
                        "published": pub_raw,
                        "summary": desc,
                    }
                )
    except Exception:
        pass
    return items


def _fetch_feed(name: str, url: str, category: str) -> list[dict]:
    """Fetch and parse a single RSS feed, silently failing on errors."""
    try:
        resp = requests.get(
            url,
            timeout=_REQUEST_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (compatible; HR-PerfBot/1.0)"},
        )
        if resp.status_code == 200:
            return _parse_rss(resp.text, name, category)
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_live_recommendations(max_total: int = 12) -> dict:
    """
    Fetch the latest HR / management recommendations from all configured RSS
    feeds.

    Returns
    -------
    dict with keys:
        "articles"      – list of article dicts
        "fetched_at"    – ISO timestamp string
        "sources_ok"    – number of feeds that returned data
        "error"         – error message if ALL feeds failed, else None
    """
    all_items: list[dict] = []
    sources_ok = 0

    for name, url, category in _FEEDS:
        items = _fetch_feed(name, url, category)
        if items:
            sources_ok += 1
            all_items.extend(items)

    result = {
        "articles": all_items[:max_total],
        "fetched_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "sources_ok": sources_ok,
        "error": None if sources_ok > 0 else "All RSS feeds failed – check network connectivity.",
    }
    return result


def get_cached_recommendations():
    """
    Streamlit-cached wrapper around fetch_live_recommendations().
    Results are refreshed automatically every 6 hours (21 600 seconds).
    Falls back gracefully if Streamlit cache is unavailable.
    """
    try:
        import streamlit as st

        @st.cache_data(ttl=21_600, show_spinner=False)
        def _cached():
            return fetch_live_recommendations()

        return _cached()
    except Exception:
        # Outside Streamlit context (e.g. unit tests)
        return fetch_live_recommendations()
