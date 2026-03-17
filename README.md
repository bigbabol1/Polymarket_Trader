# Polymarket AI Trader 🤖

Autonomes Trading-System für [Polymarket.com](https://polymarket.com) powered by **Claude AI**.

Claude analysiert Prediction-Markets, bewertet Wahrscheinlichkeiten und führt Trades vollständig autonom aus.

---

## Features

- **Autonomes Trading**: Claude analysiert Märkte eigenständig und entscheidet über Trades
- **Interaktiver Chat**: Stelle Claude direkte Fragen und gib Handelsanweisungen
- **Risk-Management**: Automatische Positionsgrößen- und Portfolio-Limits
- **Dry-Run Modus**: Teste alles sicher ohne echte Trades
- **Scheduled Trading**: Automatische Sessions in konfigurierbaren Intervallen
- **Detailliertes Logging**: Alle Entscheidungen und Trades werden protokolliert

---

## Setup

### 1. Python-Abhängigkeiten installieren

```bash
pip install -r requirements.txt
```

### 2. Konfiguration

```bash
cp .env.example .env
```

Bearbeite `.env` und trage deine Werte ein:

| Variable | Beschreibung |
|----------|-------------|
| `ANTHROPIC_API_KEY` | API-Key von [console.anthropic.com](https://console.anthropic.com) |
| `POLYGON_PRIVATE_KEY` | Privater Schlüssel deiner Polygon-Wallet |
| `CLOB_API_KEY` | Polymarket CLOB API-Key (aus deinem Account) |
| `CLOB_SECRET` | Polymarket CLOB Secret |
| `CLOB_PASS_PHRASE` | Polymarket CLOB Passphrase |
| `DRY_RUN` | `true` = kein echtes Trading (Standard), `false` = echte Trades |

### 3. Polymarket API-Keys erstellen

1. Gehe zu [polymarket.com](https://polymarket.com) und melde dich an
2. Verbinde deine Polygon-Wallet
3. Gehe zu Einstellungen → API-Keys
4. Erstelle einen neuen API-Key und kopiere Key, Secret und Passphrase in `.env`

---

## Verwendung

### Autonome Trading-Session

Claude analysiert Märkte selbstständig und führt Trades aus:

```bash
python main.py trade
```

Mit eigener Anweisung:

```bash
python main.py trade --instruction "Fokussiere auf Krypto und Politik-Märkte"
```

### Interaktiver Chat-Modus

Stelle Claude Fragen und gib direkte Handelsanweisungen:

```bash
python main.py chat
```

Beispiele:
- *"Zeig mir mein Portfolio"*
- *"Analysiere die 5 besten Krypto-Märkte"*
- *"Kaufe für $5 den wahrscheinlichsten Ausgang von Markt XYZ"*
- *"Was ist deine Einschätzung zum US-Wahlergebnis-Markt?"*

### Portfolio-Status

```bash
python main.py status
```

### Märkte anzeigen

```bash
python main.py markets --limit 20 --min-volume 5000
python main.py markets --category crypto
```

### Einzelnen Markt analysieren

```bash
python main.py analyze <MARKET_ID>
```

### Automatisches Scheduled Trading

```bash
# Alle 15 Minuten eine Session (aus .env)
python main.py schedule-trading

# Alle 30 Minuten, maximal 10 Sessions
python main.py schedule-trading --interval 30 --max-sessions 10
```

---

## Konfiguration

Alle Trading-Parameter in `.env`:

```env
# Maximale USD pro Trade
MAX_POSITION_SIZE=10.0

# Maximales Gesamtrisiko
MAX_PORTFOLIO_RISK=100.0

# Minimale Konfidenz (0.0-1.0) für Trades
MIN_CONFIDENCE_THRESHOLD=0.7

# Maximale offene Positionen gleichzeitig
MAX_OPEN_POSITIONS=5

# Intervall für automatisches Trading (Minuten)
TRADING_INTERVAL_MINUTES=15

# DRY_RUN=true für sichere Simulation
DRY_RUN=true
```

---

## Wie Claude entscheidet

Claude verwendet folgende Strategie:

1. **Portfolio prüfen**: Aktuelles Guthaben und offene Positionen laden
2. **Märkte scannen**: Aktive Märkte nach Volumen und Kategorie filtern
3. **Value-Analyse**: Vergleich von Marktpreisen mit eigener Wahrscheinlichkeitsschätzung
4. **Trade-Entscheidung**: Nur kaufen wenn `eigene_prob > marktpreis + 5%`
5. **Risk-Check**: Positionsgrösse und Portfolio-Limits prüfen
6. **Ausführung**: Trade platzieren und protokollieren

### Expected Value Beispiel

```
Markt: "Wird Bitcoin Ende 2025 über $100k sein?"
Marktpreis YES: 0.45 (45%)
Claudes Einschätzung: 55% Wahrscheinlichkeit
Expected Value: +10% → BUY ✓
```

---

## Sicherheit

- **Privaten Schlüssel niemals teilen** oder in Git committen
- `.env` ist in `.gitignore` (bitte prüfen!)
- Starte immer mit `DRY_RUN=true` zum Testen
- Setze konservative Limits (`MAX_POSITION_SIZE`, `MAX_PORTFOLIO_RISK`)
- Claude lehnt Trades mit zu niedriger Konfidenz automatisch ab

---

## Architektur

```
main.py                 # CLI-Einstiegspunkt (typer)
config.py               # Zentrale Konfiguration
core/
  polymarket.py         # Polymarket CLOB & Gamma API Client
  claude_agent.py       # Claude AI Agent mit Tool Use
  risk_manager.py       # Risikomanagement & Limits
utils/
  logger.py             # Rich-formatiertes Logging
logs/                   # Trade-Logs (automatisch erstellt)
```

---

## Haftungsausschluss

Dieses Tool ist für Bildungszwecke und experimentelles Trading. Prediction Markets beinhalten Risiken. Investiere nur was du bereit bist zu verlieren. Der Autor übernimmt keine Haftung für finanzielle Verluste.
