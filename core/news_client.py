"""
News-Client: Google News RSS + Direkte RSS-Quellen.

Zwei Fetch-Modi:
  1. fetch_news()           — Google News RSS, keyword-spezifisch, Cache 5 Min
  2. fetch_direct_sources() — BBC/Reuters/Guardian/etc. direkt, parallel, Cache 25s
                              Schneller weil keine Google-Aggregation dazwischen.

Kosten: $0 — alle Quellen sind öffentliche RSS-Feeds ohne API-Key.
"""
import html
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from utils.logger import logger

try:
    import feedparser
    _FEEDPARSER_OK = True
except ImportError:
    _FEEDPARSER_OK = False
    logger.warning("feedparser nicht installiert. News-Feature deaktiviert. (pip install feedparser)")

# ---------------------------------------------------------------------------
# Google News RSS (keyword-spezifische Suche)
# ---------------------------------------------------------------------------

_GNEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
_gnews_cache: dict[str, tuple[float, list[dict]]] = {}
_GNEWS_CACHE_TTL = 300  # 5 Minuten (Google cached selbst ~15 Min, 5 Min reicht)

# ---------------------------------------------------------------------------
# Direkte RSS-Quellen (schneller als Google-Aggregation)
# ---------------------------------------------------------------------------

DIRECT_RSS_SOURCES: dict[str, str] = {
    "BBC News":        "https://feeds.bbci.co.uk/news/rss.xml",
    "BBC World":       "https://feeds.bbci.co.uk/news/world/rss.xml",
    "Reuters":         "https://feeds.reuters.com/reuters/topNews",
    "Reuters Business":"https://feeds.reuters.com/reuters/businessNews",
    "The Guardian":    "https://www.theguardian.com/world/rss",
    "NPR":             "https://feeds.npr.org/1001/rss.xml",
    "CoinDesk":        "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Politico":        "https://www.politico.com/rss/politics08.xml",
}

# Cache für direkte Quellen: {source_name: (timestamp, [articles])}
_direct_cache: dict[str, tuple[float, list[dict]]] = {}
_DIRECT_CACHE_TTL = 25  # Knapp unter 30s Poll-Intervall → immer fresh

# ---------------------------------------------------------------------------
# Stop-Words für Keyword-Extraktion
# ---------------------------------------------------------------------------

_STOP_WORDS = {
    "will", "the", "a", "an", "in", "on", "at", "to", "of", "for",
    "and", "or", "is", "be", "by", "with", "this", "that", "it",
    "are", "was", "were", "has", "have", "had", "do", "does", "did",
    "can", "could", "would", "should", "may", "might", "shall",
    "who", "what", "when", "where", "which", "how", "from", "their",
    "its", "any", "all", "more", "most", "than", "then", "into",
    "win", "get", "first", "next", "new", "before", "after", "over",
    "über", "der", "die", "das", "ein", "eine", "und", "oder", "ist",
    "wird", "war", "hat", "mit", "bei", "von", "aus", "für",
}


def _extract_keywords(text: str, max_words: int = 5) -> str:
    """Extrahiert die relevantesten Keywords aus einer Marktfrage."""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    words = [w for w in cleaned.split() if w not in _STOP_WORDS and len(w) > 2]
    return " ".join(words[:max_words])


def _parse_entry(entry: dict, source_name: str) -> dict:
    """Parst einen RSS-Feed-Eintrag in ein einheitliches Artikel-Dict."""
    title = html.unescape(entry.get("title", ""))

    # Google News kodiert Quelle als "Titel - Quelle"
    if not source_name and " - " in title:
        parts = title.rsplit(" - ", 1)
        title = parts[0].strip()
        source_name = parts[1].strip()
    elif hasattr(entry, "source") and isinstance(entry.source, dict):
        source_name = source_name or entry.source.get("title", "")

    published = ""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            published = dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            published = entry.get("published", "")

    raw_summary = html.unescape(entry.get("summary", ""))
    summary = re.sub(r"<[^>]+>", "", raw_summary).strip()[:400]

    return {
        "title": title,
        "source": source_name,
        "published": published,
        "url": entry.get("link", ""),
        "summary": summary,
    }

# ---------------------------------------------------------------------------
# Google News RSS fetch (keyword-spezifisch)
# ---------------------------------------------------------------------------


def fetch_news(market_question: str, max_articles: int = 5) -> list[dict]:
    """
    Holt Nachrichten via Google News RSS (keyword-spezifische Suche).
    Cached für 5 Minuten. Gut für manuelle Abfragen im Chat/Trade-Modus.
    """
    if not _FEEDPARSER_OK:
        return []

    query = _extract_keywords(market_question)
    if not query:
        return []

    now = time.time()
    if query in _gnews_cache:
        ts, cached = _gnews_cache[query]
        if now - ts < _GNEWS_CACHE_TTL:
            logger.debug(f"Google-News-Cache-Hit: {query!r}")
            return cached[:max_articles]

    try:
        url = _GNEWS_RSS.format(query=query.replace(" ", "+"))
        feed = feedparser.parse(url)
        articles = [_parse_entry(e, "") for e in feed.entries[:max_articles]]
        _gnews_cache[query] = (now, articles)
        logger.info(f"[green]Google News:[/] {len(articles)} Artikel für '{query}'")
        return articles
    except Exception as e:
        logger.warning(f"Google News fehlgeschlagen für '{query}': {e}")
        return []

# ---------------------------------------------------------------------------
# Direkte RSS-Quellen (parallel, schneller)
# ---------------------------------------------------------------------------


def _fetch_one_source(source_name: str, url: str) -> tuple[str, list[dict]]:
    """Holt eine einzelne RSS-Quelle. Gibt (source_name, articles) zurück."""
    now = time.time()
    if source_name in _direct_cache:
        ts, cached = _direct_cache[source_name]
        if now - ts < _DIRECT_CACHE_TTL:
            return source_name, cached

    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:40]:
            article = _parse_entry(entry, source_name)
            # Internes Suchfeld: Titel + Summary in Kleinbuchstaben
            article["_search"] = f"{article['title']} {article['summary']}".lower()
            articles.append(article)
        _direct_cache[source_name] = (now, articles)
        logger.debug(f"Direct RSS: {source_name} → {len(articles)} Artikel")
        return source_name, articles
    except Exception as e:
        logger.debug(f"Direct RSS Fehler ({source_name}): {e}")
        return source_name, []


def fetch_direct_sources(queries: list[str]) -> dict[str, list[dict]]:
    """
    Holt alle direkten RSS-Quellen PARALLEL und matched gegen Keywords.

    Jede Quelle wird einmal gefetcht (Cache 25s) und dann lokal gegen
    alle Queries gefiltert — viel effizienter als pro-keyword Google-Calls.

    Args:
        queries: Liste von Suchbegriffen (aus _extract_keywords generiert)

    Returns:
        {query: [matching_articles]} — ohne _search Feld
    """
    if not _FEEDPARSER_OK or not queries:
        return {}

    results: dict[str, list[dict]] = {q: [] for q in queries}

    # Alle Quellen parallel fetchen
    all_articles: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(DIRECT_RSS_SOURCES)) as executor:
        futures = {
            executor.submit(_fetch_one_source, name, url): name
            for name, url in DIRECT_RSS_SOURCES.items()
        }
        for future in as_completed(futures):
            _, articles = future.result()
            all_articles.extend(articles)

    # Lokal gegen alle Queries matchen
    for query in queries:
        query_words = query.lower().split()
        for article in all_articles:
            search_text = article.get("_search", "")
            if all(word in search_text for word in query_words):
                # _search-Feld vor Rückgabe entfernen
                clean = {k: v for k, v in article.items() if k != "_search"}
                results[query].append(clean)

    matched_count = sum(len(v) for v in results.values())
    if matched_count:
        logger.debug(f"Direct RSS: {matched_count} Matches für {len(queries)} Queries")

    return results
