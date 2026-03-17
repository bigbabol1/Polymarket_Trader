"""
Logging-Utilities mit Rich-Formatierung.
"""
import logging
import os
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

# Logs-Verzeichnis erstellen
Path("logs").mkdir(exist_ok=True)

custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow bold",
    "error": "red bold",
    "success": "green bold",
    "trade": "magenta bold",
    "money": "green",
})

console = Console(theme=custom_theme)


def setup_logger(name: str = "polymarket_trader") -> logging.Logger:
    """Erstellt einen formatierten Logger."""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    if logger.handlers:
        return logger

    # Console Handler (Rich)
    rich_handler = RichHandler(
        console=console,
        rich_tracebacks=True,
        show_time=True,
        show_path=False,
        markup=True,
    )
    rich_handler.setLevel(getattr(logging, log_level, logging.INFO))

    # File Handler
    log_file = f"logs/trader_{datetime.now().strftime('%Y%m%d')}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    )

    logger.addHandler(rich_handler)
    logger.addHandler(file_handler)

    return logger


logger = setup_logger()
