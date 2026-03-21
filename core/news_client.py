"""
News-Client: Google News RSS (kostenlos, kein API-Key nötig).
Sucht relevante Nachrichten zu Polymarket-Markt-Themen.

Kosten: $0 — Google News RSS ist öffentlich und erfordert keinen Account.
Cache: 5 Minuten, um doppelte Anfragen zu vermeiden.
"""
import html
import re
import time
from datetime import datetime, timezone

from utils.logger import logger

try:
    import feedparser
    _FEEDPARSER_OK = True
except ImportError:
    _FEEDPARSER_OK = False
    logger.warning("feedparser nicht installiert. News-Feature deaktiviert. (pip install feedparser)")

# Google News RSS Suche
_GNEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

# In-Memory-Cache: {query: (timestamp, articles)}
_cache: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL_SECONDS = 300  # 5 Minuten

_STOP_WORDS = {
    # Englisch
    "will", "the", "a", "an", "in", "on", "at", "to", "of", "for",
    "and", "or", "is", "be", "by", "with", "this", "that", "it",
    "are", "was", "were", "has", "have", "had", "do", "does", "did",
    "can", "could", "would", "should", "may", "might", "shall",
    "who", "what", "when", "where", "which", "how", "from", "their",
    "its", "any", "all", "more", "most", "than", "then", "into",
    "win", "get", "first", "next", "new", "before", "after", "over",
    # Deutsch
    "über", "der", "die", "das", "ein", "eine", "und", "oder", "ist",
    "wird", "war", "hat", "mit", "bei", "von", "aus", "für",
}


def _extract_keywords(text: str, max_words: int = 5) -> str:
    """Extrahiert die relevantesten Keywords aus einer Marktfrage."""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    words = [w for w in cleaned.split() if w not in _STOP_WORDS and len(w) > 2]
    return " ".join(words[:max_words])


def fetch_news(market_question: str, max_articles: int = 5) -> list[dict]:
    """
    Holt aktuelle Nachrichten zu einer Marktfrage via Google News RSS.

    Args:
        market_question: Die Polymarket-Marktfrage oder ein freies Suchthema.
        max_articles: Maximale Anzahl zurückgegebener Artikel.

    Returns:
        Liste von Artikeln: [{"title", "source", "published", "url", "summary"}]
    """
    if not _FEEDPARSER_OK:
        return []

    query = _extract_keywords(market_question)
    if not query:
        return []

    # Cache-Prüfung
    now = time.time()
    if query in _cache:
        ts, cached = _cache[query]
        if now - ts < _CACHE_TTL_SECONDS:
            logger.debug(f"News-Cache-Hit für: {query!r}")
            return cached[:max_articles]

    try:
        url = _GNEWS_RSS.format(query=query.replace(" ", "+"))
        logger.debug(f"Fetching Google News RSS: {url}")
        feed = feedparser.parse(url)

        articles = []
        for entry in feed.entries[:max_articles]:
            title = html.unescape(entry.get("title", ""))
            source = ""

            # Google News kodiert Quelle als "Titel - Quelle"
            if hasattr(entry, "source") and isinstance(entry.source, dict):
                source = entry.source.get("title", "")
            if not source and " - " in title:
                parts = title.rsplit(" - ", 1)
                title = parts[0].strip()
                source = parts[1].strip()

            published = ""
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    published = dt.strftime("%Y-%m-%d %H:%M UTC")
                except Exception:
                    published = entry.get("published", "")

            # Summary von HTML-Tags befreien
            raw_summary = html.unescape(entry.get("summary", ""))
            summary = re.sub(r"<[^>]+>", "", raw_summary).strip()[:400]

            articles.append({
                "title": title,
                "source": source,
                "published": published,
                "url": entry.get("link", ""),
                "summary": summary,
            })

        _cache[query] = (now, articles)
        logger.info(f"[green]News:[/] {len(articles)} Artikel für '{query}'")
        return articles

    except Exception as e:
        logger.warning(f"News-Fetch fehlgeschlagen für '{query}': {e}")
        return []
