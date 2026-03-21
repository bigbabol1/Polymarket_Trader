"""
Polymarket AI Trader - Hauptprogramm

Starte mit:
    python main.py --help
    python main.py trade          # Autonome Trading-Session
    python main.py chat           # Interaktiver Chat-Modus
    python main.py schedule       # Automatisch nach Intervall traden
    python main.py status         # Portfolio-Status anzeigen
"""
import queue
import sys
import time
import json
import signal
from pathlib import Path
from datetime import datetime

import typer
import schedule
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt
from rich import print as rprint

from config import api_config, trading_config
from core.polymarket import PolymarketClient
from core.claude_agent import ClaudeTrader
from core.risk_manager import RiskManager
from utils.logger import logger

app = typer.Typer(
    name="polymarket-trader",
    help="🤖 Polymarket AI Trader powered by Claude",
    add_completion=False,
)
console = Console()

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║          POLYMARKET AI TRADER  powered by Claude             ║
║                                                              ║
║  Autonomes Trading auf Polymarket.com mit Claude AI          ║
╚══════════════════════════════════════════════════════════════╝
"""


def print_banner():
    console.print(BANNER, style="cyan bold")
    if trading_config.dry_run:
        console.print(
            "⚠️  [yellow bold]DRY RUN MODUS[/] - Keine echten Trades werden ausgeführt\n"
        )


# ---------------------------------------------------------------------------
# Startup-Checks
# ---------------------------------------------------------------------------

def run_startup_checks() -> bool:
    """Prüft Konfiguration vor dem Start."""
    console.print("[cyan]Starte Konfigurationsprüfung...[/]")

    errors = api_config.validate()
    if errors and not trading_config.dry_run:
        for err in errors:
            console.print(f"[red]✗ {err}[/]")
        console.print("\n[yellow]Tipp: Kopiere .env.example nach .env und trage deine Werte ein.[/]")
        return False

    if api_config.llm_provider == "anthropic" and not api_config.anthropic_api_key:
        console.print("[red]✗ ANTHROPIC_API_KEY fehlt - setze LLM_PROVIDER=ollama oder trage den Key ein[/]")
        return False

    console.print("[green]✓ Konfiguration OK[/]")
    if trading_config.dry_run:
        console.print("[yellow]✓ Dry-Run Modus aktiv (sichere Simulation)[/]")
    return True


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def create_components() -> tuple[PolymarketClient, RiskManager, ClaudeTrader]:
    """Erstellt und gibt die Kern-Komponenten zurück."""
    polymarket = PolymarketClient()
    risk_manager = RiskManager()
    trader = ClaudeTrader(polymarket, risk_manager)
    return polymarket, risk_manager, trader


def print_trade_summary(summary: dict):
    """Zeigt Trade-Zusammenfassung formatiert an."""
    table = Table(title="Trading-Session Zusammenfassung", show_header=True)
    table.add_column("Metrik", style="cyan")
    table.add_column("Wert", style="white")

    table.add_row("Trades gesamt", str(summary["total_trades"]))
    table.add_row("Ausgeführt", str(summary["executed"]))
    table.add_row("Abgelehnt", str(summary["rejected"]))
    table.add_row("Gesamtvolumen", f"${summary['total_volume_usd']:.2f}")

    console.print(table)

    if summary["trades"]:
        console.print("\n[cyan]Detaillierte Trades:[/]")
        for i, trade in enumerate(summary["trades"], 1):
            status_color = "green" if trade["result"].get("status") != "FAILED" else "red"
            console.print(
                f"  {i}. [{status_color}]{trade['side']}[/] "
                f"${trade['amount_usd']:.2f} | "
                f"Konfidenz: {trade['confidence']:.0%} | "
                f"Status: {trade['result'].get('status', 'N/A')}"
            )
            if trade.get("reasoning"):
                console.print(f"     [dim]→ {trade['reasoning'][:100]}[/]")


def print_risk_status(risk_manager: RiskManager):
    """Zeigt Risk-Status an."""
    status = risk_manager.get_risk_status()
    panel_content = (
        f"Eingesetztes Kapital: [yellow]${status['committed_capital']:.2f}[/]\n"
        f"Risiko-Limit:         [cyan]${status['max_portfolio_risk']:.2f}[/]\n"
        f"Auslastung:           {status['utilization_pct']}\n"
        f"Restbudget:           [green]${status['remaining_budget']:.2f}[/]\n"
        f"Offene Positionen:    {status['open_positions']}/{status['max_positions']}"
    )
    console.print(Panel(panel_content, title="Risk-Status", border_style="yellow"))


# ---------------------------------------------------------------------------
# CLI Commands
# ---------------------------------------------------------------------------

@app.command()
def trade(
    instruction: str = typer.Option(
        "",
        "--instruction", "-i",
        help="Anweisung für Claude (z.B. 'Fokussiere auf Krypto-Märkte')",
    ),
    markets_limit: int = typer.Option(
        20,
        "--markets", "-m",
        help="Anzahl zu analysierender Märkte",
    ),
):
    """
    Startet eine autonome Trading-Session.
    Claude analysiert Märkte und führt Trades selbstständig aus.
    """
    print_banner()
    if not run_startup_checks():
        raise typer.Exit(1)

    polymarket, risk_manager, trader = create_components()

    full_instruction = instruction or (
        f"Analysiere bis zu {markets_limit} aktive Märkte und führe profitable Trades aus. "
        "Prüfe zuerst mein Portfolio, dann analysiere den Markt systematisch."
    )

    console.print(f"\n[cyan]Anweisung:[/] {full_instruction}\n")
    console.print("[green]Claude beginnt mit der Analyse...[/]\n")

    try:
        result = trader.run_trading_session(full_instruction)
        console.print("\n" + "="*60)
        console.print("[cyan bold]Claude's Zusammenfassung:[/]")
        console.print(result)
        console.print("="*60 + "\n")

        summary = trader.get_trade_summary()
        print_trade_summary(summary)
        print_risk_status(risk_manager)

        # Trade-Log speichern
        log_file = f"logs/trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        Path(log_file).write_text(
            json.dumps(summary, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        console.print(f"\n[dim]Trade-Log gespeichert: {log_file}[/]")

    except KeyboardInterrupt:
        console.print("\n[yellow]Session abgebrochen.[/]")
    except Exception as e:
        logger.exception(f"Fehler in Trading-Session: {e}")
        raise typer.Exit(1)


@app.command()
def chat():
    """
    Interaktiver Chat-Modus.
    Stelle Claude Fragen und lass ihn Trades analysieren oder ausführen.
    """
    print_banner()
    if not run_startup_checks():
        raise typer.Exit(1)

    polymarket, risk_manager, trader = create_components()

    console.print("[green]Chat-Modus gestartet.[/] Schreibe 'exit' oder 'quit' zum Beenden.\n")
    console.print("[dim]Beispiele:[/]")
    console.print("  - 'Zeig mir mein Portfolio'")
    console.print("  - 'Analysiere die besten 10 Krypto-Märkte'")
    console.print("  - 'Was sind gerade die interessantesten Trading-Opportunities?'")
    console.print("  - 'Kaufe für $5 den wahrscheinlichsten Ausgang von Markt X'\n")

    while True:
        try:
            user_input = Prompt.ask("[cyan]Du[/]")

            if user_input.lower() in ("exit", "quit", "q", "bye"):
                console.print("[yellow]Auf Wiedersehen![/]")

                summary = trader.get_trade_summary()
                if summary["total_trades"] > 0:
                    print_trade_summary(summary)
                break

            if not user_input.strip():
                continue

            console.print("[dim]Claude denkt nach...[/]")
            response = trader.chat(user_input)

            console.print(f"\n[magenta]Claude:[/] {response}\n")

        except KeyboardInterrupt:
            console.print("\n[yellow]Chat beendet.[/]")
            break
        except Exception as e:
            logger.error(f"Fehler im Chat: {e}")
            console.print(f"[red]Fehler: {e}[/]")


@app.command()
def status():
    """
    Zeigt aktuellen Portfolio-Status ohne Trading.
    """
    print_banner()
    if api_config.llm_provider == "anthropic" and not api_config.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY fehlt (oder LLM_PROVIDER=ollama setzen)[/]")
        raise typer.Exit(1)

    polymarket, risk_manager, trader = create_components()

    console.print("[cyan]Lade Portfolio-Status...[/]\n")

    result = trader.chat(
        "Zeige mir meinen aktuellen Portfolio-Status. "
        "Gib Balance, offene Positionen und eine kurze Einschätzung."
    )
    console.print(f"\n[magenta]Claude:[/] {result}\n")
    print_risk_status(risk_manager)


@app.command()
def schedule_trading(
    interval_minutes: int = typer.Option(
        None,
        "--interval", "-n",
        help="Handelsintervall in Minuten (überschreibt .env Einstellung)",
    ),
    instruction: str = typer.Option(
        "",
        "--instruction", "-i",
        help="Basis-Anweisung für alle Sessions",
    ),
    max_sessions: int = typer.Option(
        0,
        "--max-sessions",
        help="Maximale Anzahl Sessions (0 = unbegrenzt)",
    ),
):
    """
    Führt Trading-Sessions automatisch in einem Zeitintervall aus.
    Läuft kontinuierlich bis CTRL+C gedrückt wird.
    """
    print_banner()
    if not run_startup_checks():
        raise typer.Exit(1)

    interval = interval_minutes or trading_config.trading_interval_minutes
    session_count = 0

    console.print(
        f"[green]Automatisches Trading gestartet[/]\n"
        f"Intervall: alle [cyan]{interval}[/] Minuten\n"
        f"Max. Sessions: {'unbegrenzt' if max_sessions == 0 else max_sessions}\n"
        f"CTRL+C zum Stoppen\n"
    )

    def run_session():
        nonlocal session_count
        session_count += 1
        console.print(
            f"\n[cyan]{'='*60}[/]\n"
            f"[green]Session #{session_count}[/] | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )

        polymarket, risk_manager, trader = create_components()
        result = trader.run_trading_session(instruction)
        console.print(f"\n[dim]{result[:500]}...[/]" if len(result) > 500 else f"\n{result}")

        summary = trader.get_trade_summary()
        if summary["total_trades"] > 0:
            print_trade_summary(summary)

        if max_sessions > 0 and session_count >= max_sessions:
            console.print(f"\n[green]Alle {max_sessions} Sessions abgeschlossen.[/]")
            raise typer.Exit(0)

    # Erste Session sofort starten
    run_session()

    # Danach nach Intervall
    schedule.every(interval).minutes.do(run_session)

    def signal_handler(sig, frame):
        console.print(f"\n[yellow]Automatisches Trading gestoppt nach {session_count} Sessions.[/]")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    while True:
        schedule.run_pending()
        time.sleep(30)


@app.command()
def markets(
    limit: int = typer.Option(10, "--limit", "-l", help="Anzahl Märkte"),
    min_volume: float = typer.Option(5000.0, "--min-volume", help="Mindestvolumen in USD"),
    category: str = typer.Option("", "--category", "-c", help="Kategorie-Filter"),
):
    """
    Zeigt aktuelle Polymarket-Märkte an (ohne Trading).
    """
    print_banner()

    polymarket = PolymarketClient()
    console.print(f"[cyan]Lade {limit} Märkte (min. ${min_volume:,.0f} Volumen)...[/]\n")

    market_list = polymarket.get_markets(limit=limit, min_volume=min_volume)

    if category:
        market_list = [m for m in market_list if category.lower() in m.get("category", "").lower()]

    if not market_list:
        console.print("[yellow]Keine Märkte gefunden.[/]")
        return

    table = Table(title=f"Aktive Polymarket-Märkte (Top {len(market_list)})")
    table.add_column("Frage", style="white", max_width=50)
    table.add_column("Kategorie", style="cyan", max_width=12)
    table.add_column("Volumen", style="green", justify="right")
    table.add_column("Outcomes & Preise", style="yellow", max_width=40)

    for m in market_list:
        outcomes_str = " | ".join(
            f"{o['name']}: {o['price']:.2f}"
            for o in m.get("outcomes", [])
        )
        table.add_row(
            m["question"][:50],
            m.get("category", "-")[:12],
            f"${m['volume']:,.0f}",
            outcomes_str[:40],
        )

    console.print(table)


@app.command()
def analyze(
    market_id: str = typer.Argument(..., help="Condition-ID des Marktes"),
):
    """
    Analysiert einen spezifischen Markt mit Claude.
    """
    print_banner()
    if not run_startup_checks():
        raise typer.Exit(1)

    polymarket, risk_manager, trader = create_components()

    prompt = (
        f"Analysiere Markt {market_id} im Detail. "
        "Schau dir das Orderbuch an, bewerte die Wahrscheinlichkeiten und "
        "gib eine klare Trading-Empfehlung mit Begründung."
    )
    console.print(f"[cyan]Claude analysiert Markt {market_id}...[/]\n")
    result = trader.chat(prompt)
    console.print(f"\n[magenta]Analyse:[/]\n{result}\n")


@app.command()
def watch(
    keywords: str = typer.Option(
        "",
        "--keywords", "-k",
        help="Komma-getrennte Keywords (z.B. 'Trump,Bitcoin,Fed'). Ergänzt NEWS_WATCH_KEYWORDS aus .env",
    ),
    interval: int = typer.Option(
        None,
        "--interval", "-n",
        help="Poll-Intervall in Sekunden (Standard: NEWS_WATCH_INTERVAL aus .env, default 90)",
    ),
    cooldown: int = typer.Option(
        None,
        "--cooldown", "-c",
        help="Mindest-Pause zwischen AI-Aktivierungen in Sekunden (Standard: NEWS_COOLDOWN aus .env, default 300)",
    ),
    no_auto_keywords: bool = typer.Option(
        False,
        "--no-auto-keywords",
        help="Deaktiviert automatische Keywords aus aktiven Polymarket-Märkten",
    ),
    auto_refresh_minutes: int = typer.Option(
        30,
        "--auto-refresh",
        help="Wie oft (in Minuten) die Markt-Keywords neu geladen werden (0 = nie)",
    ),
):
    """
    News-getriggertes Trading: KI wird NUR aktiv wenn relevante Nachrichten erscheinen.

    Der Watcher pollt Google News RSS kontinuierlich und löst eine Trading-Session
    nur aus wenn wirklich neue Artikel erscheinen — kein unnötiger KI-Verbrauch.
    """
    print_banner()
    if not run_startup_checks():
        raise typer.Exit(1)

    effective_interval = interval or api_config.news_watch_interval
    effective_cooldown = cooldown or api_config.news_cooldown

    console.print(
        f"[green]News-Watcher Modus[/]\n"
        f"Poll-Intervall:  [cyan]{effective_interval}s[/]\n"
        f"AI-Cooldown:     [cyan]{effective_cooldown}s[/]\n"
        f"Auto-Keywords:   [cyan]{'ja' if not no_auto_keywords else 'nein'}[/]\n"
        f"CTRL+C zum Stoppen\n"
    )

    polymarket, risk_manager, trader = create_components()

    # Queue für thread-safe Kommunikation zwischen Watcher und Hauptschleife
    event_queue: queue.Queue = queue.Queue()

    def on_news(event):
        event_queue.put(event)

    watcher_interval = effective_interval
    from core.news_watcher import NewsWatcher
    watcher = NewsWatcher(on_news=on_news, interval_seconds=watcher_interval)

    # --- Keywords sammeln ---

    # 1. Statische Keywords aus .env (NEWS_WATCH_KEYWORDS)
    static_keywords = [
        kw.strip() for kw in api_config.news_watch_keywords.split(",") if kw.strip()
    ]
    # 2. CLI-Keywords
    cli_keywords = [kw.strip() for kw in keywords.split(",") if kw.strip()]

    all_static = static_keywords + cli_keywords
    for kw in all_static:
        watcher.add_keyword(kw)

    # 3. Auto-Keywords aus aktiven Märkten
    last_market_refresh = 0.0

    def refresh_market_keywords():
        nonlocal last_market_refresh
        if no_auto_keywords:
            return
        try:
            console.print("[dim]Lade Markt-Keywords...[/]")
            markets = polymarket.get_markets(limit=40, min_volume=5000.0)
            kw_map = {}
            # Statische Keywords beibehalten
            for kw in all_static:
                from core.news_client import _extract_keywords
                kw_map[kw] = _extract_keywords(kw) or kw
            # Markt-Keywords hinzufügen
            for m in markets:
                question = m.get("question", "")
                if question:
                    from core.news_client import _extract_keywords
                    q = _extract_keywords(question)
                    if q:
                        kw_map[question[:60]] = q
            watcher.set_keywords_bulk(kw_map)
            last_market_refresh = time.time()
            console.print(
                f"[green]Keywords aktualisiert:[/] {len(kw_map)} aktiv "
                f"({len(markets)} Märkte + {len(all_static)} statisch)"
            )
        except Exception as e:
            logger.warning(f"Markt-Keywords konnten nicht geladen werden: {e}")

    refresh_market_keywords()
    watcher.start()

    last_ai_call = 0.0
    session_count = 0

    def signal_handler(sig, frame):
        console.print(f"\n[yellow]News-Watcher gestoppt. {session_count} Sessions ausgeführt.[/]")
        watcher.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    console.print(
        f"[green]Watcher aktiv[/] | Überwache [cyan]{len(watcher.get_keywords())}[/] Keywords\n"
        "[dim]Warte auf News...[/]\n"
    )

    while True:
        # Markt-Keywords periodisch auffrischen
        if (
            not no_auto_keywords
            and auto_refresh_minutes > 0
            and time.time() - last_market_refresh > auto_refresh_minutes * 60
        ):
            refresh_market_keywords()

        try:
            event = event_queue.get(timeout=30)
        except queue.Empty:
            continue

        # Cooldown prüfen
        now = time.time()
        if now - last_ai_call < effective_cooldown:
            remaining = int(effective_cooldown - (now - last_ai_call))
            console.print(
                f"[dim]News zu '{event.keyword_label}' erkannt — "
                f"Cooldown aktiv, noch {remaining}s[/]"
            )
            continue

        # --- AI-Session starten ---
        session_count += 1
        last_ai_call = time.time()

        console.print(
            f"\n[yellow bold]{'='*60}[/]\n"
            f"[green]AI-Session #{session_count}[/] | "
            f"Trigger: [yellow]{event.keyword_label}[/] | "
            f"{datetime.now().strftime('%H:%M:%S')}\n"
        )

        news_context = event.format_for_ai()
        console.print(f"[dim]{news_context[:600]}[/]\n")

        instruction = (
            f"BREAKING NEWS — sofortiger Handlungsbedarf:\n\n"
            f"{news_context}\n\n"
            "Deine Aufgabe:\n"
            "1. Prüfe Portfolio-Status\n"
            "2. Suche aktive Märkte die von dieser News betroffen sein könnten\n"
            "3. Falls du einen relevanten Markt findest: analysiere ihn sofort (Details + Orderbuch)\n"
            "4. Trade NUR wenn der Expected Value klar positiv ist und du eine hohe Konfidenz hast\n"
            "5. Fasse deine Entscheidung kurz zusammen"
        )

        try:
            result = trader.run_trading_session(instruction)
            console.print(f"\n[magenta]AI-Zusammenfassung:[/]\n{result}\n")

            summary = trader.get_trade_summary()
            if summary["total_trades"] > 0:
                print_trade_summary(summary)

                log_file = f"logs/news_trade_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                Path(log_file).write_text(
                    json.dumps(
                        {"trigger": event.keyword_label, "articles": event.articles, "trades": summary},
                        indent=2,
                        ensure_ascii=False,
                        default=str,
                    ),
                    encoding="utf-8",
                )
                console.print(f"[dim]Log: {log_file}[/]")
            else:
                console.print("[dim]Keine Trades ausgeführt — weiter überwachen.[/]")

        except Exception as e:
            logger.error(f"Fehler in News-getriggerter Session: {e}")

        console.print(f"\n[dim]Warte auf nächste News...[/]\n")


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
