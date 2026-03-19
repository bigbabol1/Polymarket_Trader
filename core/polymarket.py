"""
Polymarket CLOB API Client.
Verbindet sich mit der Polymarket Central Limit Order Book API
und der Gamma API für Marktdaten.
"""
import time
import hmac
import hashlib
import base64
import json
from datetime import datetime, timezone
from typing import Optional
import requests
from utils.logger import logger
from config import api_config, trading_config


class PolymarketClient:
    """Client für die Polymarket CLOB und Gamma APIs."""

    def __init__(self):
        self.clob_url = api_config.clob_api_url
        self.gamma_url = api_config.gamma_api_url
        self.api_key = api_config.clob_api_key
        self.secret = api_config.clob_secret
        self.passphrase = api_config.clob_pass_phrase
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def _decode_secret(self) -> bytes:
        """Gibt das Secret als Bytes zurück."""
        return self.secret.encode("utf-8")

    def _sign_request(self, method: str, path: str, body: str = "") -> dict:
        """Erstellt die CLOB API Signatur-Header."""
        timestamp = str(int(time.time()))
        message = timestamp + method.upper() + path + body
        signature = hmac.new(
            self._decode_secret(),
            message.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        sig_b64 = base64.b64encode(signature).decode("utf-8")

        return {
            "POLY_ADDRESS": self.api_key,
            "POLY_SIGNATURE": sig_b64,
            "POLY_TIMESTAMP": timestamp,
            "POLY_PASSPHRASE": self.passphrase,
        }

    def _clob_get(self, path: str, params: dict = None) -> dict | list:
        """Authentifizierter GET-Request an CLOB API."""
        signed_headers = self._sign_request("GET", path)
        self.session.headers.update(signed_headers)
        url = f"{self.clob_url}{path}"
        resp = self.session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _clob_post(self, path: str, data: dict) -> dict:
        """Authentifizierter POST-Request an CLOB API."""
        body = json.dumps(data)
        signed_headers = self._sign_request("POST", path, body)
        self.session.headers.update(signed_headers)
        url = f"{self.clob_url}{path}"
        resp = self.session.post(url, data=body, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _gamma_get(self, path: str, params: dict = None) -> dict | list:
        """Öffentlicher GET-Request an Gamma API (keine Authentifizierung nötig)."""
        url = f"{self.gamma_url}{path}"
        resp = self.session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    # -------------------------------------------------------------------------
    # Marktdaten
    # -------------------------------------------------------------------------

    def get_markets(
        self,
        limit: int = 20,
        active: bool = True,
        min_volume: float = 1000.0,
    ) -> list[dict]:
        """
        Holt aktive Märkte mit ausreichendem Volumen.

        Returns:
            Liste von Markt-Dicts mit: id, question, outcomes, prices, volume, ...
        """
        try:
            params = {
                "active": str(active).lower(),
                "closed": "false",
                "limit": limit,
            }
            data = self._gamma_get("/markets", params=params)
            markets = data if isinstance(data, list) else data.get("markets", [])

            # Filtere nach Mindestvolumen und bereichere mit Preis-Daten
            enriched = []
            for m in markets:
                volume = float(m.get("volume", 0) or 0)
                if volume >= min_volume:
                    enriched.append(self._enrich_market(m))

            logger.info(f"[cyan]{len(enriched)}[/] aktive Märkte geladen (min. ${min_volume:,.0f} Volumen)")
            return enriched

        except Exception as e:
            logger.error(f"Fehler beim Laden der Märkte: {e}")
            return []

    def _enrich_market(self, market: dict) -> dict:
        """Fügt Preisdaten und berechnete Felder hinzu."""
        tokens = market.get("tokens", [])
        outcomes = []
        for token in tokens:
            outcome_name = token.get("outcome", "")
            token_id = token.get("token_id", "")
            price = float(token.get("price", 0) or 0)
            outcomes.append({
                "name": outcome_name,
                "token_id": token_id,
                "price": price,
                "implied_prob": price,  # Bei Polymarket = Preis
            })

        return {
            "id": market.get("condition_id", market.get("id", "")),
            "question": market.get("question", ""),
            "description": market.get("description", ""),
            "category": market.get("category", ""),
            "end_date": market.get("end_date_iso", ""),
            "volume": float(market.get("volume", 0) or 0),
            "liquidity": float(market.get("liquidity", 0) or 0),
            "outcomes": outcomes,
            "active": market.get("active", True),
            "raw": market,
        }

    def get_market_by_id(self, market_id: str) -> Optional[dict]:
        """Holt einen einzelnen Markt per ID."""
        try:
            data = self._gamma_get(f"/markets/{market_id}")
            return self._enrich_market(data)
        except Exception as e:
            logger.error(f"Markt {market_id} nicht gefunden: {e}")
            return None

    def get_orderbook(self, token_id: str) -> dict:
        """Holt das Orderbuch für einen Token."""
        try:
            data = self._clob_get(f"/book", params={"token_id": token_id})
            return data
        except Exception as e:
            logger.warning(f"Orderbuch für {token_id} nicht verfügbar: {e}")
            return {"bids": [], "asks": []}

    def get_price(self, token_id: str, side: str = "BUY") -> float:
        """Holt den aktuellen Best-Price für einen Token."""
        try:
            resp = self._clob_get("/price", params={"token_id": token_id, "side": side})
            return float(resp.get("price", 0))
        except Exception as e:
            logger.warning(f"Preis für {token_id} nicht verfügbar: {e}")
            return 0.0

    # -------------------------------------------------------------------------
    # Portfolio & Positionen
    # -------------------------------------------------------------------------

    def get_balance(self) -> float:
        """Holt das USDC-Guthaben des Wallets."""
        try:
            data = self._clob_get("/balance-allowance", params={"asset_type": "USDC"})
            return float(data.get("balance", 0))
        except Exception as e:
            logger.error(f"Fehler beim Laden des Guthabens: {e}")
            return 0.0

    def get_positions(self) -> list[dict]:
        """Holt alle offenen Positionen."""
        try:
            data = self._clob_get("/positions")
            positions = data if isinstance(data, list) else data.get("positions", [])
            return positions
        except Exception as e:
            logger.error(f"Fehler beim Laden der Positionen: {e}")
            return []

    def get_open_orders(self) -> list[dict]:
        """Holt alle offenen Orders."""
        try:
            data = self._clob_get("/orders", params={"status": "LIVE"})
            return data if isinstance(data, list) else data.get("orders", [])
        except Exception as e:
            logger.error(f"Fehler beim Laden der Orders: {e}")
            return []

    def get_trade_history(self, limit: int = 50) -> list[dict]:
        """Holt die Trade-Historie."""
        try:
            data = self._clob_get("/trades", params={"limit": limit})
            return data if isinstance(data, list) else data.get("data", [])
        except Exception as e:
            logger.error(f"Fehler beim Laden der Trade-Historie: {e}")
            return []

    # -------------------------------------------------------------------------
    # Order-Ausführung
    # -------------------------------------------------------------------------

    def place_market_order(
        self,
        token_id: str,
        side: str,
        amount_usd: float,
        market_question: str = "",
    ) -> dict:
        """
        Platziert eine Market-Order.

        Args:
            token_id: Token-ID des Outcomes
            side: "BUY" oder "SELL"
            amount_usd: Betrag in USD
            market_question: Für Logging

        Returns:
            Order-Response-Dict
        """
        if trading_config.dry_run:
            logger.info(
                f"[yellow][DRY RUN][/] {side} ${amount_usd:.2f} Token={token_id[:8]}... "
                f"| Frage: {market_question[:60]}"
            )
            return {
                "dry_run": True,
                "order_id": f"dry_run_{int(time.time())}",
                "token_id": token_id,
                "side": side,
                "amount_usd": amount_usd,
                "status": "SIMULATED",
            }

        try:
            order_data = {
                "token_id": token_id,
                "side": side.upper(),
                "size": str(amount_usd),
                "price": "0",  # Market Order = Preis 0
                "type": "MARKET",
                "time_in_force": "IOC",
            }
            result = self._clob_post("/order", order_data)
            logger.info(
                f"[green]Order ausgeführt:[/] {side} ${amount_usd:.2f} "
                f"| OrderID: {result.get('orderID', 'N/A')}"
            )
            return result

        except Exception as e:
            logger.error(f"Order fehlgeschlagen: {e}")
            return {"error": str(e), "status": "FAILED"}

    def place_limit_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size_usd: float,
    ) -> dict:
        """
        Platziert eine Limit-Order.

        Args:
            token_id: Token-ID des Outcomes
            side: "BUY" oder "SELL"
            price: Preis (0.0 - 1.0)
            size_usd: Betrag in USD

        Returns:
            Order-Response-Dict
        """
        if trading_config.dry_run:
            logger.info(
                f"[yellow][DRY RUN][/] LIMIT {side} ${size_usd:.2f} @ {price:.3f} "
                f"Token={token_id[:8]}..."
            )
            return {
                "dry_run": True,
                "order_id": f"dry_run_limit_{int(time.time())}",
                "token_id": token_id,
                "side": side,
                "price": price,
                "size_usd": size_usd,
                "status": "SIMULATED",
            }

        try:
            order_data = {
                "token_id": token_id,
                "side": side.upper(),
                "size": str(size_usd),
                "price": str(price),
                "type": "GTC",  # Good Till Cancelled
            }
            result = self._clob_post("/order", order_data)
            logger.info(
                f"[green]Limit-Order:[/] {side} ${size_usd:.2f} @ {price:.3f} "
                f"| OrderID: {result.get('orderID', 'N/A')}"
            )
            return result

        except Exception as e:
            logger.error(f"Limit-Order fehlgeschlagen: {e}")
            return {"error": str(e), "status": "FAILED"}

    def cancel_order(self, order_id: str) -> bool:
        """Storniert eine offene Order."""
        if trading_config.dry_run:
            logger.info(f"[yellow][DRY RUN][/] Order storniert: {order_id}")
            return True

        try:
            self._clob_post("/cancel", {"orderID": order_id})
            logger.info(f"[green]Order storniert:[/] {order_id}")
            return True
        except Exception as e:
            logger.error(f"Order-Stornierung fehlgeschlagen: {e}")
            return False
