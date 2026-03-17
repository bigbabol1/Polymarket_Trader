"""
Risk-Manager für den Polymarket Trader.

Überwacht Positionsgrößen, Gesamtrisiko und verhindert übermässige Trades.
"""
from collections import defaultdict
from config import trading_config
from utils.logger import logger


class RiskManager:
    """
    Verwaltet Handelsrisiken und Portfolio-Limits.

    Prüft jeden Trade auf:
    - Positionsgrösse (max. MAX_POSITION_SIZE USD)
    - Gesamtportfolio-Risiko (max. MAX_PORTFOLIO_RISK USD)
    - Maximale Anzahl offener Positionen
    - Mindest-Konfidenz
    - Doppel-Positionen im selben Markt
    """

    def __init__(self):
        self.committed_capital = 0.0  # Bereits eingesetztes Kapital dieser Session
        self.open_positions: dict[str, float] = {}  # token_id -> amount_usd
        self.trade_count = 0

    def check_trade(
        self,
        amount_usd: float,
        confidence: float,
        token_id: str,
    ) -> tuple[bool, str]:
        """
        Prüft ob ein Trade erlaubt ist.

        Returns:
            (erlaubt: bool, grund: str)
        """
        # 1. Mindest-Konfidenz
        if confidence < trading_config.min_confidence_threshold:
            return False, (
                f"Konfidenz {confidence:.0%} unter Minimum "
                f"{trading_config.min_confidence_threshold:.0%}"
            )

        # 2. Maximale Positionsgrösse
        if amount_usd > trading_config.max_position_size:
            return False, (
                f"${amount_usd:.2f} überschreitet max. Positionsgrösse "
                f"${trading_config.max_position_size:.2f}"
            )

        # 3. Gesamtportfolio-Risiko
        new_total = self.committed_capital + amount_usd
        if new_total > trading_config.max_portfolio_risk:
            return False, (
                f"Gesamtrisiko ${new_total:.2f} würde Limit "
                f"${trading_config.max_portfolio_risk:.2f} überschreiten"
            )

        # 4. Maximale offene Positionen
        if len(self.open_positions) >= trading_config.max_open_positions:
            if token_id not in self.open_positions:
                return False, (
                    f"Maximale Anzahl offener Positionen erreicht "
                    f"({trading_config.max_open_positions})"
                )

        # 5. Mindestbetrag
        if amount_usd < 1.0:
            return False, f"Betrag ${amount_usd:.2f} zu gering (Minimum $1.00)"

        return True, "OK"

    def record_trade(self, amount_usd: float, token_id: str):
        """Registriert einen ausgeführten Trade."""
        self.committed_capital += amount_usd
        self.open_positions[token_id] = self.open_positions.get(token_id, 0) + amount_usd
        self.trade_count += 1
        logger.debug(
            f"Risk-Manager: Kapital eingesetzt ${self.committed_capital:.2f} | "
            f"Positionen: {len(self.open_positions)}"
        )

    def release_position(self, token_id: str):
        """Gibt eine Position als geschlossen frei."""
        if token_id in self.open_positions:
            amount = self.open_positions.pop(token_id)
            self.committed_capital = max(0, self.committed_capital - amount)

    def get_portfolio_summary(self, balance: float, positions: list[dict]) -> dict:
        """Erstellt eine Portfolio-Zusammenfassung."""
        position_value = sum(
            float(p.get("size", 0)) * float(p.get("currentPrice", 0))
            for p in positions
        )
        return {
            "usdc_balance": balance,
            "position_value_usd": position_value,
            "total_value_usd": balance + position_value,
            "committed_this_session": self.committed_capital,
            "remaining_budget": max(0, trading_config.max_portfolio_risk - self.committed_capital),
            "open_positions_count": len(self.open_positions),
            "max_positions": trading_config.max_open_positions,
            "trades_this_session": self.trade_count,
        }

    def get_risk_status(self) -> dict:
        """Gibt aktuellen Risk-Status zurück."""
        utilization = (
            self.committed_capital / trading_config.max_portfolio_risk
            if trading_config.max_portfolio_risk > 0
            else 0
        )
        return {
            "committed_capital": self.committed_capital,
            "max_portfolio_risk": trading_config.max_portfolio_risk,
            "utilization_pct": f"{utilization:.1%}",
            "open_positions": len(self.open_positions),
            "max_positions": trading_config.max_open_positions,
            "remaining_budget": max(0, trading_config.max_portfolio_risk - self.committed_capital),
        }
