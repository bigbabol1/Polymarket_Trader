"""
Polymarket CLOB API Client.
Verbindet sich mit der Polymarket Central Limit Order Book API
und der Gamma API für Marktdaten.
Verwendet py-clob-client für korrekte Authentifizierung.
"""
import time
import json
from typing import Optional
import requests
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, MarketOrderArgs, OrderType, BalanceAllowanceParams, TradeParams, AssetType
from py_clob_client.order_builder.constants import BUY, SELL
from utils.logger import logger
from config import api_config, trading_config

POLYGON_CHAIN_ID = 137
# USDC (PoS) Contract auf Polygon — von Polymarket verwendet
USDC_CONTRACT = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
POLYGON_RPC = "https://polygon-rpc.com"


class PolymarketClient:
    """Client für die Polymarket CLOB und Gamma APIs."""

    def __init__(self):
        self.gamma_url = api_config.gamma_api_url
        self._session = requests.Session()

        # py-clob-client mit API-Key-Authentifizierung (L2)
        creds = ApiCreds(
            api_key=api_config.clob_api_key,
            api_secret=api_config.clob_secret,
            api_passphrase=api_config.clob_pass_phrase,
        )
        # signature_type: 0=EOA, 1=POLY_PROXY (Privy/Magic Link Email-Login), 2=GNOSIS_SAFE
        # Konfigurierbar über SIGNATURE_TYPE env-Variable (Standard: 1 für Privy)
        sig_type = api_config.signature_type
        logger.info(f"CLOB signature_type={sig_type}, funder={api_config.funder_address or 'nicht gesetzt'}")
        self._clob = ClobClient(
            host=api_config.clob_api_url,
            key=api_config.polygon_private_key,
            chain_id=POLYGON_CHAIN_ID,
            creds=creds,
            signature_type=sig_type,
            funder=api_config.funder_address or None,
        )

    def _gamma_get(self, path: str, params: dict = None) -> dict | list:
        """Öffentlicher GET-Request an Gamma API."""
        url = f"{self.gamma_url}{path}"
        resp = self._session.get(url, params=params, timeout=15)
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
                "implied_prob": price,
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
            return self._clob.get_order_book(token_id)
        except Exception as e:
            logger.warning(f"Orderbuch für {token_id} nicht verfügbar: {e}")
            return {"bids": [], "asks": []}

    def get_price(self, token_id: str, side: str = "BUY") -> float:
        """Holt den aktuellen Best-Price für einen Token."""
        try:
            resp = self._clob.get_price(token_id, side)
            if isinstance(resp, dict):
                return float(resp.get("price", 0))
            return float(resp)
        except Exception as e:
            logger.warning(f"Preis für {token_id} nicht verfügbar: {e}")
            return 0.0

    # -------------------------------------------------------------------------
    # Portfolio & Positionen
    # -------------------------------------------------------------------------

    def _get_onchain_usdc_balance(self, address: str) -> float:
        """Liest USDC-Guthaben direkt vom Polygon-Netzwerk (ERC-20 balanceOf)."""
        try:
            # balanceOf(address) Funktionssignatur: 0x70a08231
            padded = address.lower().replace("0x", "").zfill(64)
            data = "0x70a08231" + padded
            payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "eth_call",
                "params": [{"to": USDC_CONTRACT, "data": data}, "latest"],
            }
            resp = requests.post(POLYGON_RPC, json=payload, timeout=8)
            result_hex = resp.json().get("result", "0x0")
            raw = int(result_hex, 16)
            balance = raw / 1_000_000  # USDC hat 6 Dezimalstellen
            logger.debug(f"[DEBUG] On-Chain USDC Balance für {address}: {balance:.2f}")
            return balance
        except Exception as e:
            logger.warning(f"On-Chain Balance-Abfrage fehlgeschlagen: {e}")
            return 0.0

    def get_balance(self) -> float:
        """Holt das USDC-Guthaben des Wallets."""
        try:
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            self._clob.update_balance_allowance(params=params)
            data = self._clob.get_balance_allowance(params=params)
            logger.debug(f"[DEBUG] get_balance_allowance raw response: {data!r}")
            if isinstance(data, dict):
                raw = float(data.get("balance", 0))
                result = raw / 1_000_000 if raw > 1000 else raw
                logger.debug(f"[DEBUG] CLOB Balance: {result:.2f}")
                if result > 0:
                    return result
            # Fallback: USDC direkt on-chain lesen (Proxy-Wallet)
            funder = api_config.funder_address
            if funder:
                logger.info("CLOB Balance = 0 → lese USDC direkt von Polygon-Chain (Proxy-Wallet)")
                return self._get_onchain_usdc_balance(funder)
            return 0.0
        except Exception as e:
            logger.error(f"Fehler beim Laden des Guthabens: {e}", exc_info=True)
            return 0.0

    def get_positions(self) -> list[dict]:
        """Holt alle offenen Positionen (aus Trade-Historie berechnet)."""
        # py-clob-client hat keine direkte get_positions() Methode.
        # Offene Positionen = Tokens mit positivem Bestand aus abgeschlossenen Trades.
        try:
            trades = self.get_trade_history(limit=200)
            positions: dict[str, float] = {}
            for t in trades:
                tid = t.get("asset_id", "")
                size = float(t.get("size", 0) or 0)
                side = t.get("side", "BUY").upper()
                if tid:
                    positions[tid] = positions.get(tid, 0) + (size if side == "BUY" else -size)
            return [
                {"token_id": tid, "size": size}
                for tid, size in positions.items()
                if size > 0
            ]
        except Exception as e:
            logger.error(f"Fehler beim Laden der Positionen: {e}")
            return []

    def get_open_orders(self) -> list[dict]:
        """Holt alle offenen Orders."""
        try:
            data = self._clob.get_orders()
            if isinstance(data, list):
                return data
            return data.get("data", []) if isinstance(data, dict) else []
        except Exception as e:
            logger.error(f"Fehler beim Laden der Orders: {e}")
            return []

    def get_trade_history(self, limit: int = 50) -> list[dict]:
        """Holt die Trade-Historie."""
        try:
            data = self._clob.get_trades(params=TradeParams())
            if isinstance(data, list):
                return data
            return data.get("data", []) if isinstance(data, dict) else []
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
            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=amount_usd,
                side=BUY if side.upper() == "BUY" else SELL,
            )
            signed_order = self._clob.create_market_order(order_args)
            result = self._clob.post_order(signed_order, OrderType.FOK)
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
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size_usd,
                side=BUY if side.upper() == "BUY" else SELL,
            )
            signed_order = self._clob.create_order(order_args)
            result = self._clob.post_order(signed_order, OrderType.GTC)
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
            self._clob.cancel(order_id)
            logger.info(f"[green]Order storniert:[/] {order_id}")
            return True
        except Exception as e:
            logger.error(f"Order-Stornierung fehlgeschlagen: {e}")
            return False
