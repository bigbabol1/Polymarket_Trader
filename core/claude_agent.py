"""
AI Trading Agent — unterstützt Anthropic Claude und lokales Ollama-LLM.

Provider wird über LLM_PROVIDER in .env gesteuert:
  LLM_PROVIDER=anthropic  → Claude API (Standard)
  LLM_PROVIDER=ollama     → lokales Ollama (OpenAI-kompatible API)
"""
import json
from typing import Any
from config import api_config, trading_config
from utils.logger import logger
from core.polymarket import PolymarketClient
from core.risk_manager import RiskManager


# ---------------------------------------------------------------------------
# Tool-Definitionen (Anthropic-Format, wird für Ollama konvertiert)
# ---------------------------------------------------------------------------

TRADING_TOOLS = [
    {
        "name": "get_active_markets",
        "description": (
            "Holt eine Liste aktiver Polymarket-Märkte mit Volumens- und Preisdaten. "
            "Nutze dies um interessante Handelsmöglichkeiten zu finden."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Anzahl der Märkte (max 50, Standard 20)",
                    "default": 20,
                },
                "min_volume": {
                    "type": "number",
                    "description": "Mindestvolumen in USD (Standard 1000)",
                    "default": 1000.0,
                },
                "category": {
                    "type": "string",
                    "description": "Optional: Kategorie-Filter (z.B. 'politics', 'sports', 'crypto')",
                },
            },
        },
    },
    {
        "name": "get_market_details",
        "description": (
            "Holt detaillierte Informationen über einen spezifischen Markt inkl. "
            "Orderbuch und aktuellen Preisen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "market_id": {
                    "type": "string",
                    "description": "Die Condition-ID des Marktes",
                },
            },
            "required": ["market_id"],
        },
    },
    {
        "name": "get_portfolio_status",
        "description": (
            "Gibt den aktuellen Portfoliostatus zurück: Guthaben, offene Positionen, "
            "P&L und verfügbares Kapital."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "place_trade",
        "description": (
            "Führt einen Trade aus. Kauft oder verkauft einen Markt-Outcome. "
            "WICHTIG: Nur aufrufen wenn du eine klare Einschätzung hast und das "
            "Risk-Management zustimmt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "token_id": {
                    "type": "string",
                    "description": "Token-ID des Outcomes (aus get_active_markets)",
                },
                "side": {
                    "type": "string",
                    "enum": ["BUY", "SELL"],
                    "description": "Kaufen (BUY) oder Verkaufen (SELL)",
                },
                "amount_usd": {
                    "type": "number",
                    "description": "Handelsbetrag in USD (max: MAX_POSITION_SIZE aus Config)",
                },
                "market_question": {
                    "type": "string",
                    "description": "Die Marktfrage für Logging-Zwecke",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Deine Begründung für diesen Trade (wichtig für Nachverfolgung)",
                },
                "confidence": {
                    "type": "number",
                    "description": "Deine Konfidenz in den Trade (0.0 bis 1.0)",
                },
            },
            "required": ["token_id", "side", "amount_usd", "reasoning", "confidence"],
        },
    },
    {
        "name": "place_limit_order",
        "description": (
            "Platziert eine Limit-Order zu einem bestimmten Preis. "
            "Nutze dies wenn du auf einen besseren Einstiegspreis warten möchtest."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "token_id": {
                    "type": "string",
                    "description": "Token-ID des Outcomes",
                },
                "side": {
                    "type": "string",
                    "enum": ["BUY", "SELL"],
                },
                "price": {
                    "type": "number",
                    "description": "Zielpreis (0.01 bis 0.99)",
                },
                "amount_usd": {
                    "type": "number",
                    "description": "Handelsbetrag in USD",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Begründung für den Trade",
                },
                "confidence": {
                    "type": "number",
                    "description": "Konfidenz (0.0 bis 1.0)",
                },
            },
            "required": ["token_id", "side", "price", "amount_usd", "reasoning", "confidence"],
        },
    },
    {
        "name": "cancel_order",
        "description": "Storniert eine offene Order.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "Die Order-ID die storniert werden soll",
                },
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "get_trade_history",
        "description": "Gibt die letzten Trades zurück um Performance zu analysieren.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Anzahl der Trades (Standard 20)",
                    "default": 20,
                },
            },
        },
    },
]

# OpenAI/Ollama-Format der Tools (wird einmalig konvertiert)
_OLLAMA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        },
    }
    for t in TRADING_TOOLS
]


SYSTEM_PROMPT = """Du bist ein professioneller Prediction-Market-Trader auf Polymarket.com.

Deine Aufgabe ist es, Märkte zu analysieren und profitable Trades zu finden.

## Deine Trading-Philosophie:
1. **Research-basiert**: Analysiere Märkte sorgfältig. Nutze dein Wissen über aktuelle Ereignisse,
   Statistiken und Wahrscheinlichkeiten.
2. **Value-orientiert**: Kaufe nur wenn der Marktpreis UNTER deiner geschätzten Wahrscheinlichkeit liegt
   (positiver Expected Value).
3. **Risikobewusst**: Achte immer auf Positionsgrössen und Diversifikation.
4. **Diszipliniert**: Überschreite niemals die konfigurierten Limits.

## Workflow bei jeder Trading-Session:
1. Lade zuerst den Portfolio-Status (Guthaben, Positionen)
2. Durchsuche aktive Märkte nach Opportunities
3. Analysiere vielversprechende Märkte im Detail
4. Vergleiche Marktpreise mit deiner eigenen Wahrscheinlichkeitseinschätzung
5. Platziere Trades NUR wenn:
   - Deine geschätzte Wahrscheinlichkeit > Marktpreis + 5% (für BUY)
   - Konfidenz ≥ {min_confidence}
   - Positionsgrösse ≤ ${max_position_size}
   - Maximale Positionen nicht überschritten
6. Gib eine strukturierte Zusammenfassung deiner Entscheidungen

## Wichtige Regeln:
- Sei KONSERVATIV bei unklaren Situationen
- Bei DRY_RUN={dry_run} werden keine echten Trades ausgeführt
- Begründe jeden Trade klar und nachvollziehbar
- Achte auf die Liquidität: Meide Märkte mit < $1000 Volumen

Aktuelles Datum: {current_date}
Verfügbares Budget pro Trade: max ${max_position_size}
Gesamtrisiko-Limit: ${max_portfolio_risk}
"""


class ClaudeTrader:
    """
    Autonomer AI Trading Agent.
    Unterstützt Anthropic Claude (API) und Ollama (lokal) als LLM-Backend.
    """

    def __init__(self, polymarket: PolymarketClient, risk_manager: RiskManager):
        self.polymarket = polymarket
        self.risk_manager = risk_manager
        self.provider = api_config.llm_provider.lower()
        self.conversation_history: list[dict] = []
        self.trade_log: list[dict] = []

        if self.provider == "ollama":
            from openai import OpenAI
            self.client = OpenAI(
                base_url=api_config.ollama_url.rstrip("/") + "/v1",
                api_key="ollama",
            )
            self.model = api_config.ollama_model
            logger.info(f"[cyan]LLM-Backend: Ollama[/] | Modell: {self.model} | URL: {api_config.ollama_url}")
        else:
            import anthropic
            self.client = anthropic.Anthropic(api_key=api_config.anthropic_api_key)
            self.model = "claude-opus-4-6"
            logger.info(f"[cyan]LLM-Backend: Anthropic[/] | Modell: {self.model}")

    def _get_system_prompt(self) -> str:
        from datetime import date
        return SYSTEM_PROMPT.format(
            min_confidence=trading_config.min_confidence_threshold,
            max_position_size=trading_config.max_position_size,
            max_portfolio_risk=trading_config.max_portfolio_risk,
            dry_run=trading_config.dry_run,
            current_date=date.today().isoformat(),
        )

    # -------------------------------------------------------------------------
    # LLM-Abstraktion: ein einheitlicher Aufruf, zwei Backends
    # -------------------------------------------------------------------------

    def _llm_step(self, messages: list[dict]) -> tuple[str | None, list[dict]]:
        """
        Ruft das LLM einmal auf.

        Returns:
            (text, tool_calls)
            - text: Abschluss-Text wenn keine Tools mehr gerufen werden, sonst None
            - tool_calls: Liste von {"name": str, "input": dict, "id": str}
        """
        if self.provider == "ollama":
            return self._llm_step_ollama(messages)
        return self._llm_step_anthropic(messages)

    def _llm_step_anthropic(self, messages: list[dict]) -> tuple[str | None, list[dict]]:
        import anthropic
        response = self.client.messages.create(
            model=self.model,
            max_tokens=8096,
            system=self._get_system_prompt(),
            tools=TRADING_TOOLS,
            messages=messages,
        )
        tool_calls = [b for b in response.content if b.type == "tool_use"]
        if not tool_calls:
            text = " ".join(b.text for b in response.content if hasattr(b, "text"))
            return text, []
        return None, [{"name": tc.name, "input": tc.input, "id": tc.id} for tc in tool_calls]

    def _llm_step_ollama(self, messages: list[dict]) -> tuple[str | None, list[dict]]:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=_OLLAMA_TOOLS,
        )
        msg = response.choices[0].message
        if not msg.tool_calls:
            return msg.content or "", []
        tool_calls = [
            {
                "name": tc.function.name,
                "input": json.loads(tc.function.arguments),
                "id": tc.id,
            }
            for tc in msg.tool_calls
        ]
        return None, tool_calls

    # -------------------------------------------------------------------------
    # Message-History-Abstraktion
    # -------------------------------------------------------------------------

    def _make_assistant_msg(self, tool_calls: list[dict]) -> dict:
        """Erstellt eine Assistenten-Nachricht mit Tool-Calls im richtigen Format."""
        if self.provider == "ollama":
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["input"], ensure_ascii=False),
                        },
                    }
                    for tc in tool_calls
                ],
            }
        # Anthropic: muss das echte response.content Objekt sein → wird direkt
        # in run_trading_session / chat eingefügt, dieser Pfad wird nicht genutzt
        return {}

    def _make_tool_results_msg(self, tool_calls: list[dict], results: list[str]) -> dict | list[dict]:
        """Erstellt die Tool-Result-Nachrichten im richtigen Format."""
        if self.provider == "ollama":
            # OpenAI: eine Message pro Tool-Result
            return [
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
                for tc, result in zip(tool_calls, results)
            ]
        # Anthropic: eine user-Message mit allen tool_results
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": result,
                }
                for tc, result in zip(tool_calls, results)
            ],
        }

    # -------------------------------------------------------------------------
    # Tool-Ausführung
    # -------------------------------------------------------------------------

    def _execute_tool(self, tool_name: str, tool_input: dict) -> Any:
        """Führt ein Tool aus das das LLM angefordert hat."""
        logger.debug(f"Tool aufgerufen: {tool_name}({json.dumps(tool_input, ensure_ascii=False)[:200]})")

        if tool_name == "get_active_markets":
            markets = self.polymarket.get_markets(
                limit=tool_input.get("limit", 20),
                min_volume=tool_input.get("min_volume", 1000.0),
            )
            if "category" in tool_input and tool_input["category"]:
                cat = tool_input["category"].lower()
                markets = [m for m in markets if cat in (m.get("category", "")).lower()]
            return self._format_markets_for_llm(markets)

        elif tool_name == "get_market_details":
            market = self.polymarket.get_market_by_id(tool_input["market_id"])
            if not market:
                return {"error": "Markt nicht gefunden"}
            for outcome in market.get("outcomes", []):
                if outcome.get("token_id"):
                    book = self.polymarket.get_orderbook(outcome["token_id"])
                    outcome["orderbook"] = {
                        "best_bid": book.get("bids", [{}])[0].get("price", 0) if book.get("bids") else 0,
                        "best_ask": book.get("asks", [{}])[0].get("price", 0) if book.get("asks") else 0,
                        "bid_depth": len(book.get("bids", [])),
                        "ask_depth": len(book.get("asks", [])),
                    }
            return market

        elif tool_name == "get_portfolio_status":
            balance = self.polymarket.get_balance()
            positions = self.polymarket.get_positions()
            open_orders = self.polymarket.get_open_orders()
            portfolio_summary = self.risk_manager.get_portfolio_summary(balance, positions)
            return {
                "balance_usdc": balance,
                "open_positions": positions,
                "open_orders_count": len(open_orders),
                "portfolio_summary": portfolio_summary,
                "dry_run_mode": trading_config.dry_run,
            }

        elif tool_name == "place_trade":
            confidence = float(tool_input.get("confidence", 0))
            amount = float(tool_input["amount_usd"])
            risk_ok, reason = self.risk_manager.check_trade(
                amount_usd=amount,
                confidence=confidence,
                token_id=tool_input["token_id"],
            )
            if not risk_ok:
                logger.warning(f"[yellow]Trade vom Risk-Manager abgelehnt:[/] {reason}")
                return {"status": "REJECTED", "reason": reason}
            result = self.polymarket.place_market_order(
                token_id=tool_input["token_id"],
                side=tool_input["side"],
                amount_usd=amount,
                market_question=tool_input.get("market_question", ""),
            )
            self.trade_log.append({
                "timestamp": __import__("datetime").datetime.now().isoformat(),
                "type": "MARKET",
                "side": tool_input["side"],
                "amount_usd": amount,
                "token_id": tool_input["token_id"],
                "reasoning": tool_input.get("reasoning", ""),
                "confidence": confidence,
                "result": result,
            })
            self.risk_manager.record_trade(amount, tool_input["token_id"])
            return result

        elif tool_name == "place_limit_order":
            confidence = float(tool_input.get("confidence", 0))
            amount = float(tool_input["amount_usd"])
            price = float(tool_input["price"])
            risk_ok, reason = self.risk_manager.check_trade(amount, confidence, tool_input["token_id"])
            if not risk_ok:
                return {"status": "REJECTED", "reason": reason}
            result = self.polymarket.place_limit_order(
                token_id=tool_input["token_id"],
                side=tool_input["side"],
                price=price,
                size_usd=amount,
            )
            self.trade_log.append({
                "timestamp": __import__("datetime").datetime.now().isoformat(),
                "type": "LIMIT",
                "side": tool_input["side"],
                "price": price,
                "amount_usd": amount,
                "token_id": tool_input["token_id"],
                "reasoning": tool_input.get("reasoning", ""),
                "confidence": confidence,
                "result": result,
            })
            self.risk_manager.record_trade(amount, tool_input["token_id"])
            return result

        elif tool_name == "cancel_order":
            success = self.polymarket.cancel_order(tool_input["order_id"])
            return {"success": success}

        elif tool_name == "get_trade_history":
            return self.polymarket.get_trade_history(limit=tool_input.get("limit", 20))

        else:
            return {"error": f"Unbekanntes Tool: {tool_name}"}

    def _format_markets_for_llm(self, markets: list[dict]) -> list[dict]:
        """Komprimiert Marktdaten für das LLM (weniger Token)."""
        formatted = []
        for m in markets:
            outcomes_summary = []
            for o in m.get("outcomes", []):
                outcomes_summary.append({
                    "name": o["name"],
                    "token_id": o["token_id"],
                    "price": round(o["price"], 4),
                    "implied_prob_pct": f"{o['implied_prob'] * 100:.1f}%",
                })
            formatted.append({
                "id": m["id"],
                "question": m["question"],
                "category": m.get("category", ""),
                "end_date": m.get("end_date", ""),
                "volume_usd": f"${m['volume']:,.0f}",
                "liquidity_usd": f"${m['liquidity']:,.0f}",
                "outcomes": outcomes_summary,
            })
        return formatted

    # -------------------------------------------------------------------------
    # Haupt-Trading-Schleife (provider-agnostisch)
    # -------------------------------------------------------------------------

    def run_trading_session(self, user_instruction: str = "") -> str:
        """Führt eine vollständige Trading-Session durch."""
        if not user_instruction:
            user_instruction = (
                "Analysiere den Markt und führe vielversprechende Trades durch. "
                "Prüfe zuerst das Portfolio, dann suche nach Opportunities."
            )

        logger.info(f"[cyan]Trading-Session gestartet[/] | Provider: {self.provider} | Modell: {self.model}")
        logger.info(f"Anweisung: {user_instruction[:100]}")

        if self.provider == "ollama":
            messages = [
                {"role": "system", "content": self._get_system_prompt()},
                {"role": "user", "content": user_instruction},
            ]
        else:
            messages = [{"role": "user", "content": user_instruction}]

        max_iterations = 20
        for iteration in range(1, max_iterations + 1):
            logger.debug(f"Iteration {iteration}/{max_iterations}")

            if self.provider == "anthropic":
                # Anthropic braucht das echte response-Objekt für den History-Eintrag
                import anthropic
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=8096,
                    system=self._get_system_prompt(),
                    tools=TRADING_TOOLS,
                    messages=messages,
                )
                tool_blocks = [b for b in response.content if b.type == "tool_use"]
                if not tool_blocks:
                    return " ".join(b.text for b in response.content if hasattr(b, "text"))

                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for tc in tool_blocks:
                    result = self._execute_tool(tc.name, tc.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    })
                messages.append({"role": "user", "content": tool_results})

            else:  # ollama
                text, tool_calls = self._llm_step_ollama(messages)
                if not tool_calls:
                    logger.info("[green]Trading-Session beendet[/]")
                    return text or ""

                # Assistent-Nachricht mit Tool-Calls zur History
                messages.append(self._make_assistant_msg(tool_calls))

                # Tools ausführen und Results anhängen
                results = [
                    json.dumps(self._execute_tool(tc["name"], tc["input"]), ensure_ascii=False, default=str)
                    for tc in tool_calls
                ]
                tool_msgs = self._make_tool_results_msg(tool_calls, results)
                if isinstance(tool_msgs, list):
                    messages.extend(tool_msgs)
                else:
                    messages.append(tool_msgs)

        logger.warning("Maximale Iterationen erreicht")
        return "Session beendet (max. Iterationen erreicht)"

    def chat(self, message: str) -> str:
        """Interaktiver Chat-Modus mit persistentem Konversations-Kontext."""
        # History initialisieren (einmalig System-Prompt für Ollama)
        if not self.conversation_history and self.provider == "ollama":
            self.conversation_history.append(
                {"role": "system", "content": self._get_system_prompt()}
            )

        self.conversation_history.append({"role": "user", "content": message})

        max_iterations = 15
        for _ in range(max_iterations):
            if self.provider == "anthropic":
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=self._get_system_prompt(),
                    tools=TRADING_TOOLS,
                    messages=self.conversation_history,
                )
                tool_blocks = [b for b in response.content if b.type == "tool_use"]
                if not tool_blocks:
                    self.conversation_history.append({"role": "assistant", "content": response.content})
                    return " ".join(b.text for b in response.content if hasattr(b, "text"))

                self.conversation_history.append({"role": "assistant", "content": response.content})
                tool_results = []
                for tc in tool_blocks:
                    result = self._execute_tool(tc.name, tc.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    })
                self.conversation_history.append({"role": "user", "content": tool_results})

            else:  # ollama
                text, tool_calls = self._llm_step_ollama(self.conversation_history)
                if not tool_calls:
                    self.conversation_history.append({"role": "assistant", "content": text})
                    return text or ""

                self.conversation_history.append(self._make_assistant_msg(tool_calls))
                results = [
                    json.dumps(self._execute_tool(tc["name"], tc["input"]), ensure_ascii=False, default=str)
                    for tc in tool_calls
                ]
                tool_msgs = self._make_tool_results_msg(tool_calls, results)
                if isinstance(tool_msgs, list):
                    self.conversation_history.extend(tool_msgs)
                else:
                    self.conversation_history.append(tool_msgs)

        return "Antwort konnte nicht vollständig generiert werden."

    def get_trade_summary(self) -> dict:
        """Gibt eine Zusammenfassung aller Trades dieser Session zurück."""
        total_trades = len(self.trade_log)
        total_volume = sum(t["amount_usd"] for t in self.trade_log)
        rejected = sum(1 for t in self.trade_log if t.get("result", {}).get("status") == "REJECTED")
        return {
            "total_trades": total_trades,
            "executed": total_trades - rejected,
            "rejected": rejected,
            "total_volume_usd": total_volume,
            "trades": self.trade_log,
        }
