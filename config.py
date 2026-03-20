"""
Zentrale Konfiguration für den Polymarket Trader.
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class TradingConfig:
    """Handels-Parameter."""
    max_position_size: float = float(os.getenv("MAX_POSITION_SIZE", "10.0"))
    max_portfolio_risk: float = float(os.getenv("MAX_PORTFOLIO_RISK", "100.0"))
    min_confidence_threshold: float = float(os.getenv("MIN_CONFIDENCE_THRESHOLD", "0.7"))
    max_open_positions: int = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
    trading_interval_minutes: int = int(os.getenv("TRADING_INTERVAL_MINUTES", "15"))
    dry_run: bool = os.getenv("DRY_RUN", "true").lower() == "true"


@dataclass
class APIConfig:
    """API-Schlüssel und Endpunkte."""
    # LLM Provider: "anthropic" oder "ollama"
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "anthropic"))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    # Ollama
    ollama_url: str = field(default_factory=lambda: os.getenv("OLLAMA_URL", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "llama3.1:8b"))
    # Polymarket
    polygon_private_key: str = field(default_factory=lambda: os.getenv("POLYGON_PRIVATE_KEY", ""))
    funder_address: str = field(default_factory=lambda: os.getenv("FUNDER_ADDRESS", ""))
    clob_api_key: str = field(default_factory=lambda: os.getenv("CLOB_API_KEY", ""))
    clob_secret: str = field(default_factory=lambda: os.getenv("CLOB_SECRET", ""))
    clob_pass_phrase: str = field(default_factory=lambda: os.getenv("CLOB_PASS_PHRASE", ""))
    clob_api_url: str = field(default_factory=lambda: os.getenv("CLOB_API_URL", "https://clob.polymarket.com"))
    gamma_api_url: str = field(default_factory=lambda: os.getenv("GAMMA_API_URL", "https://gamma-api.polymarket.com"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    def validate(self) -> list[str]:
        """Prüft ob alle nötigen Keys vorhanden sind."""
        errors = []
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            errors.append("ANTHROPIC_API_KEY fehlt (oder LLM_PROVIDER=ollama setzen)")
        if not self.polygon_private_key or self.polygon_private_key == "0xYOUR_PRIVATE_KEY_HERE":
            errors.append("POLYGON_PRIVATE_KEY fehlt oder ist noch der Beispielwert")
        return errors


# Globale Instanzen
trading_config = TradingConfig()
api_config = APIConfig()
