"""
NewsWatcher: Push-ähnlicher News-Monitor via periodisches RSS-Polling.

Funktionsweise:
  - Pollt Google News RSS alle NEWS_WATCH_INTERVAL Sekunden (Standard: 90s)
  - Vergleicht neue Artikel mit bereits gesehenen URLs
  - Ruft bei wirklich neuen Artikeln sofort einen Callback auf
  - KI bleibt vollständig inaktiv solange nichts Neues eintrifft

Vorteile gegenüber Pull:
  - Reaktionszeit = Polling-Intervall (nicht Trading-Intervall)
  - Keine KI-Kosten wenn keine relevanten News erscheinen
  - Mehrere Keywords/Märkte werden parallel überwacht
"""
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from core.news_client import _extract_keywords, fetch_news
from utils.logger import logger


@dataclass
class NewsEvent:
    """Ein News-Ereignis das den Trading-Trigger auslöst."""
    keyword_label: str        # Lesbare Bezeichnung (z.B. die Marktfrage)
    query: str                # Verwendeter Suchbegriff
    articles: list[dict]      # Neue Artikel (title, source, published, url, summary)
    triggered_at: float = field(default_factory=time.time)

    def format_for_ai(self) -> str:
        """Formatiert die News kompakt für den KI-Prompt."""
        lines = [f"** Neue Nachrichten zu: {self.keyword_label} **\n"]
        for i, a in enumerate(self.articles, 1):
            lines.append(
                f"{i}. [{a.get('source', '?')}] {a['title']}\n"
                f"   Datum: {a.get('published', 'unbekannt')}\n"
                f"   {a.get('summary', '')[:200]}\n"
            )
        return "\n".join(lines)


class NewsWatcher:
    """
    Hintergrund-Thread der RSS-Feeds überwacht und bei neuen Artikeln
    sofort einen Callback aufruft (push-ähnliches Verhalten).

    Verwendung:
        watcher = NewsWatcher(on_news=my_callback, interval_seconds=90)
        watcher.add_keyword("US Election", "Trump election 2025")
        watcher.add_keyword("Bitcoin ETF")  # Query wird automatisch extrahiert
        watcher.start()
        # ... später:
        watcher.stop()
    """

    def __init__(
        self,
        on_news: Callable[[NewsEvent], None],
        interval_seconds: int = 90,
    ):
        self.on_news = on_news
        self.interval = interval_seconds

        # label → query_string (label ist die menschenlesbare Bezeichnung)
        self._keywords: dict[str, str] = {}
        self._seen_urls: set[str] = set()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Keyword-Verwaltung (thread-safe, jederzeit änderbar)
    # ------------------------------------------------------------------

    def add_keyword(self, label: str, query: str | None = None):
        """
        Fügt ein Keyword zum Monitoring hinzu.

        Args:
            label: Menschenlesbare Bezeichnung (z.B. Marktfrage)
            query: RSS-Suchbegriff. Falls None: wird aus label extrahiert.
        """
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
        """Ersetzt alle Keywords auf einmal (atomare Operation)."""
        with self._lock:
            self._keywords = dict(keyword_map)
        logger.info(f"[cyan]News-Watcher:[/] {len(keyword_map)} Keywords aktiv")

    def get_keywords(self) -> dict[str, str]:
        with self._lock:
            return dict(self._keywords)

    def clear_seen_cache(self):
        """Setzt den Seen-Cache zurück (alle URLs gelten wieder als neu)."""
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
            f"Intervall: {self.interval}s | "
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
    # Poll-Schleife (läuft im Hintergrund-Thread)
    # ------------------------------------------------------------------

    def _poll_loop(self):
        logger.debug("News-Watcher Poll-Loop gestartet")
        while not self._stop_event.is_set():
            with self._lock:
                snapshot = dict(self._keywords)

            for label, query in snapshot.items():
                if self._stop_event.is_set():
                    break
                try:
                    self._check_keyword(label, query)
                except Exception as e:
                    logger.warning(f"News-Watcher Fehler für '{query}': {e}")

            # Warte auf nächsten Zyklus — reagiert sofort auf stop()
            self._stop_event.wait(timeout=self.interval)

        logger.debug("News-Watcher Poll-Loop beendet")

    def _check_keyword(self, label: str, query: str):
        """Holt RSS-Artikel und filtert bereits gesehene heraus."""
        articles = fetch_news(query, max_articles=10)

        new_articles = []
        with self._lock:
            for article in articles:
                url = article.get("url", "")
                if url and url not in self._seen_urls:
                    self._seen_urls.add(url)
                    new_articles.append(article)

        if new_articles:
            logger.info(
                f"[yellow bold]Breaking News![/] "
                f"{len(new_articles)} neuer Artikel | Thema: '{label}'"
            )
            event = NewsEvent(
                keyword_label=label,
                query=query,
                articles=new_articles,
            )
            try:
                self.on_news(event)
            except Exception as e:
                logger.error(f"News-Callback Fehler: {e}")
