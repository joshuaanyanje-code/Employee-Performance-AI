"""
ai_recommendations.py
=====================
Fetches live AI / HR best-practice recommendations from public RSS feeds
so that super-admins always see up-to-date industry guidance alongside
their internal analytics.

Feeds are selected based on the organization's business type:
  • Office       – general HR, leadership, workplace productivity
  • Service      – customer experience, hospitality, service management
  • Merchandiser – retail, supply chain, inventory management
  • Manufacturer – manufacturing, operations, lean/quality management

All sources are publicly accessible RSS – no API key required.

Caching
-------
Results are cached for 6 hours via Streamlit's @st.cache_data so the app
never makes a live web request on every page render.
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime
import requests

# ---------------------------------------------------------------------------
# RSS feed catalogue keyed by business type
# Each entry: (display_name, rss_url, category_label)
# ---------------------------------------------------------------------------

# Feeds that apply to every business type
_COMMON_FEEDS = [
    ("SHRM",             "https://www.shrm.org/rss/feeds/all-news-rss.xml", "HR Management"),
    ("HR Dive",          "https://www.hrdive.com/feeds/news/",              "HR News"),
    ("Harvard Business Review", "https://feeds.hbr.org/harvardbusiness",   "Business Leadership"),
    ("MIT Sloan Review", "https://sloanreview.mit.edu/feed/",               "Leadership & Strategy"),
]

# Extra feeds layered on top for each specific business type
_BUSINESS_TYPE_FEEDS: dict[str, list[tuple[str, str, str]]] = {
    "Office": [
        ("Gallup Workplace",    "https://www.gallup.com/rss/feed.aspx?g=workplace", "Workplace Engagement"),
        ("Worklife (Fast Co.)", "https://www.fastcompany.com/section/work-life/rss", "Work-Life Balance"),
    ],
    "Service": [
        ("Customer Think",      "https://customerthink.com/feed/",                   "Customer Experience"),
        ("Hospitality Net",     "https://www.hospitalitynet.org/rss/rss.xml",        "Hospitality & Service"),
        ("Service Management",  "https://www.tsia.com/blog/rss.xml",                 "Service Operations"),
    ],
    "Merchandiser": [
        ("Retail Dive",         "https://www.retaildive.com/feeds/news/",            "Retail & Merchandising"),
        ("Supply Chain Dive",   "https://www.supplychaindive.com/feeds/news/",       "Supply Chain"),
        ("Chain Store Age",     "https://chainstoreage.com/rss.xml",                 "Store Operations"),
    ],
    "Manufacturer": [
        ("Industry Week",       "https://www.industryweek.com/rss",                  "Manufacturing"),
        ("Quality Digest",      "https://www.qualitydigest.com/rss.xml",             "Quality & Lean"),
        ("Manufacturing Dive",  "https://www.manufacturingdive.com/feeds/news/",     "Operations"),
    ],
}

_REQUEST_TIMEOUT = 8       # seconds per feed
_MAX_ITEMS_PER_FEED = 4    # articles returned per source


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_rss(xml_text: str, source_name: str, category: str) -> list[dict]:
    """Parse a raw RSS XML string and return a list of article dicts."""
    items = []
    try:
        root = ET.fromstring(xml_text)
        channel = root.find("channel")
        entries = (
            channel.findall("item")
            if channel is not None
            else root.findall(".//{http://www.w3.org/2005/Atom}entry")
        )

        for entry in entries[:_MAX_ITEMS_PER_FEED]:
            title_el = entry.find("title")
            link_el  = entry.find("link")
            pub_el   = entry.find("pubDate") or entry.find("published")
            desc_el  = entry.find("description") or entry.find(
                "{http://www.w3.org/2005/Atom}summary"
            )

            title   = (title_el.text or "").strip() if title_el is not None else ""
            link    = (link_el.text  or "").strip() if link_el  is not None else ""
            if not link and link_el is not None:
                link = link_el.get("href", "")

            pub_raw = (pub_el.text or "").strip() if pub_el is not None else ""
            desc    = (desc_el.text or "").strip() if desc_el is not None else ""
            desc    = re.sub(r"<[^>]+>", "", desc)[:200]

            if title:
                items.append({
                    "source":    source_name,
                    "category":  category,
                    "title":     title,
                    "link":      link,
                    "published": pub_raw,
                    "summary":   desc,
                })
    except Exception:
        pass
    return items


def _fetch_feed(name: str, url: str, category: str) -> list[dict]:
    """Fetch and parse one RSS feed, silently ignoring errors."""
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


def _feeds_for_type(business_type: str) -> list[tuple[str, str, str]]:
    """Return the combined feed list for a given business type."""
    btype = (business_type or "Office").strip().capitalize()
    # Normalize slight spelling differences
    mapping = {
        "Manufacturer": "Manufacturer",
        "Manufacturers": "Manufacturer",
        "Merchandiser": "Merchandiser",
        "Merchandisers": "Merchandiser",
        "Service": "Service",
        "Office": "Office",
    }
    btype = mapping.get(btype, "Office")
    extras = _BUSINESS_TYPE_FEEDS.get(btype, [])
    return _COMMON_FEEDS + extras


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_live_recommendations(business_type: str = "Office", max_total: int = 16) -> dict:
    """
    Fetch the latest HR / management recommendations tailored to the
    organization's business type.

    Parameters
    ----------
    business_type : str
        One of "Office", "Service", "Merchandiser", "Manufacturer".
    max_total : int
        Maximum total articles to return across all feeds.

    Returns
    -------
    dict with keys:
        "articles"      – list of article dicts
        "fetched_at"    – UTC timestamp string
        "sources_ok"    – number of feeds that returned data
        "business_type" – echoed back for display
        "error"         – error message if ALL feeds failed, else None
    """
    feeds = _feeds_for_type(business_type)
    all_items: list[dict] = []
    sources_ok = 0

    for name, url, category in feeds:
        items = _fetch_feed(name, url, category)
        if items:
            sources_ok += 1
            all_items.extend(items)

    return {
        "articles":      all_items[:max_total],
        "fetched_at":    datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "sources_ok":    sources_ok,
        "business_type": business_type,
        "error":         None if sources_ok > 0 else "All RSS feeds failed – check network connectivity.",
    }


def get_cached_recommendations(business_type: str = "Office") -> dict:
    """
    Streamlit-cached wrapper around fetch_live_recommendations().
    Results are refreshed every 6 hours per business type.
    Falls back gracefully outside Streamlit context (e.g. unit tests).
    """
    try:
        import streamlit as st

        @st.cache_data(ttl=21_600, show_spinner=False)
        def _cached(btype: str) -> dict:
            return fetch_live_recommendations(business_type=btype)

        return _cached(business_type)
    except Exception:
        return fetch_live_recommendations(business_type=business_type)

