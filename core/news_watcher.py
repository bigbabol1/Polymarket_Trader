"""
NewsWatcher: Dual-Speed News-Monitor.

Zwei parallele Polling-Strategien:

  SCHNELL — Direkte RSS-Quellen (BBC, Reuters, Guardian, NPR, CoinDesk, Politico)
    → alle 30s (Standard), parallel gefetcht in ~2-5s
    → Eine Anfrage pro Quelle deckt ALLE Keywords ab (lokales Matching)
    → Typische Reaktionszeit: 10–60s nach Artikel-Veröffentlichung

  LANGSAM — Google News RSS (keyword-spezifische Suche)
    → alle 90s (jeder 3. Zyklus), eine Anfrage pro Keyword
    → Breitere Quellenabdeckung, aber ~15 Min Aggregations-Lag bei Google

KI-Aktivierung:
  - Nur bei wirklich neuen Artikeln (URL-basierter Dedup)
  - Cooldown verhindert KI-Spam bei News-Flut
  - KI bleibt 100% inaktiv solange keine neuen Artikel erscheinen
"""
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from core.news_client import _extract_keywords, fetch_direct_sources, fetch_news
from utils.logger import logger


@dataclass
class NewsEvent:
    """Ein News-Ereignis das den Trading-Trigger auslöst."""
    keyword_label: str    # Lesbare Bezeichnung (z.B. Marktfrage)
    query: str            # Verwendeter Suchbegriff
    articles: list[dict]  # Neue Artikel
    source_type: str      # "direct" oder "google"
    triggered_at: float = field(default_factory=time.time)

    def format_for_ai(self) -> str:
        """Formatiert die News kompakt für den KI-Prompt."""
        speed_label = "Direktquelle" if self.source_type == "direct" else "Google News"
        lines = [f"** Neue Nachrichten zu: {self.keyword_label} ({speed_label}) **\n"]
        for i, a in enumerate(self.articles, 1):
            lines.append(
                f"{i}. [{a.get('source', '?')}] {a['title']}\n"
                f"   Datum: {a.get('published', 'unbekannt')}\n"
                f"   {a.get('summary', '')[:200]}\n"
            )
        return "\n".join(lines)


class NewsWatcher:
    """
    Hintergrund-Thread mit Dual-Speed-Polling:
      - Direkte RSS-Quellen: alle `interval_seconds` (Standard: 30s)
      - Google News RSS: alle `interval_seconds * 3` (Standard: 90s)

    Verwendung:
        watcher = NewsWatcher(on_news=callback, interval_seconds=30)
        watcher.add_keyword("Trump election")
        watcher.start()
        watcher.stop()
    """

    def __init__(
        self,
        on_news: Callable[[NewsEvent], None],
        interval_seconds: int = 30,
    ):
        self.on_news = on_news
        self.interval = interval_seconds

        self._keywords: dict[str, str] = {}   # label → query
        self._seen_urls: set[str] = set()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Keyword-Verwaltung
    # ------------------------------------------------------------------

    def add_keyword(self, label: str, query: str | None = None):
        q = (query or _extract_keywords(label)).strip()
        if not q:
            return
        with self._lock:
            self._keywords[label] = q
        logger.debug(f"News-Watcher +keyword: {q!r}")

    def remove_keyword(self, label: str):
        with self._lock:
            self._keywords.pop(label, None)

    def set_keywords_bulk(self, keyword_map: dict[str, str]):
        with self._lock:
            self._keywords = dict(keyword_map)
        logger.info(f"[cyan]News-Watcher:[/] {len(keyword_map)} Keywords aktiv")

    def get_keywords(self) -> dict[str, str]:
        with self._lock:
            return dict(self._keywords)

    def clear_seen_cache(self):
        with self._lock:
            self._seen_urls.clear()
        logger.debug("News-Watcher: Seen-Cache geleert")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        if self._thread and self._thread.is_alive():
            logger.warning("News-Watcher läuft bereits")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="news-watcher",
        )
        self._thread.start()
        logger.info(
            f"[green]News-Watcher gestartet[/] | "
            f"Direkt: alle {self.interval}s | "
            f"Google: alle {self.interval * 3}s | "
            f"Keywords: {len(self._keywords)}"
        )

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 5)
        logger.info("News-Watcher gestoppt")

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------------
    # Poll-Schleife
    # ------------------------------------------------------------------

    def _poll_loop(self):
        cycle = 0
        logger.debug("News-Watcher Poll-Loop gestartet")

        while not self._stop_event.is_set():
            cycle += 1
            with self._lock:
                snapshot = dict(self._keywords)

            if snapshot:
                queries = list(snapshot.values())
                label_for_query = {v: k for k, v in snapshot.items()}

                # --- Direkte Quellen: jeden Zyklus (30s) ---
                self._poll_direct(queries, label_for_query)

                # --- Google RSS: jeden 3. Zyklus (90s effektiv) ---
                if cycle % 3 == 0:
                    self._poll_google(snapshot)

            self._stop_event.wait(timeout=self.interval)

        logger.debug("News-Watcher Poll-Loop beendet")

    def _poll_direct(self, queries: list[str], label_for_query: dict[str, str]):
        """Holt alle direkten Quellen parallel und prüft auf neue Artikel."""
        if self._stop_event.is_set():
            return
        try:
            matched = fetch_direct_sources(queries)
        except Exception as e:
            logger.warning(f"Direct-RSS Poll Fehler: {e}")
            return

        for query, articles in matched.items():
            new_articles = self._filter_new(articles)
            if new_articles:
                label = label_for_query.get(query, query)
                self._emit(NewsEvent(
                    keyword_label=label,
                    query=query,
                    articles=new_articles,
                    source_type="direct",
                ))

    def _poll_google(self, snapshot: dict[str, str]):
        """Holt Google News RSS pro Keyword (breitere Quellenabdeckung)."""
        for label, query in snapshot.items():
            if self._stop_event.is_set():
                break
            try:
                articles = fetch_news(query, max_articles=10)
                new_articles = self._filter_new(articles)
                if new_articles:
                    self._emit(NewsEvent(
                        keyword_label=label,
                        query=query,
                        articles=new_articles,
                        source_type="google",
                    ))
            except Exception as e:
                logger.warning(f"Google-RSS Fehler für '{query}': {e}")

    def _filter_new(self, articles: list[dict]) -> list[dict]:
        """Gibt nur Artikel zurück die noch nicht gesehen wurden."""
        new = []
        with self._lock:
            for article in articles:
                url = article.get("url", "")
                if url and url not in self._seen_urls:
                    self._seen_urls.add(url)
                    new.append(article)
        return new

    def _emit(self, event: NewsEvent):
        logger.info(
            f"[yellow bold]Breaking News![/] "
            f"{len(event.articles)} Artikel | "
            f"'{event.keyword_label}' | "
            f"Quelle: {event.source_type}"
        )
        try:
            self.on_news(event)
        except Exception as e:
            logger.error(f"News-Callback Fehler: {e}")
