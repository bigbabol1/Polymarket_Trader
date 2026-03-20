"""
Debug-Script: Zeigt exakt welche HTTP-Anfragen py_clob_client sendet
und prüft die Wallet-Konfiguration.
"""
import os
import json
from dotenv import load_dotenv

load_dotenv()

PRIVATE_KEY = os.getenv("POLYGON_PRIVATE_KEY", "")
FUNDER = os.getenv("FUNDER_ADDRESS", "")

# ---------------------------------------------------------------
# 1. Wallet-Infos aus dem Private Key ableiten
# ---------------------------------------------------------------
print("=" * 60)
print("1. WALLET-DIAGNOSE")
print("=" * 60)

try:
    from eth_account import Account
    acct = Account.from_key(PRIVATE_KEY)
    print(f"Signer-Adresse (aus Private Key): {acct.address}")
    print(f"FUNDER_ADDRESS (aus .env):         {FUNDER or '(nicht gesetzt!)'}")
    print(f"Übereinstimmung:                   {'JA' if acct.address.lower() == FUNDER.lower() else 'NEIN — das ist korrekt für Proxy-Wallets'}")
except Exception as e:
    print(f"Fehler beim Key-Check: {e}")

# ---------------------------------------------------------------
# 2. HTTP-Requests abfangen und anzeigen
# ---------------------------------------------------------------
print("\n" + "=" * 60)
print("2. HTTP-REQUEST ANALYSE (create_api_key mit signature_type=2)")
print("=" * 60)

import requests as req_module
original_request = req_module.Session.request

captured_requests = []

def debug_request(self, method, url, **kwargs):
    info = {
        "method": method,
        "url": url,
        "headers": dict(kwargs.get("headers") or {}),
        "data": kwargs.get("data"),
        "json_body": kwargs.get("json"),
    }
    captured_requests.append(info)
    print(f"\n>>> {method} {url}")
    poly_headers = {k: v for k, v in info["headers"].items() if "POLY" in k.upper()}
    if poly_headers:
        print("Polymarket-Headers:")
        for k, v in poly_headers.items():
            print(f"  {k}: {v}")
    else:
        print("(keine POLY_* Header gefunden)")

    resp = original_request(self, method, url, **kwargs)
    print(f"<<< Status: {resp.status_code}")
    try:
        print(f"    Response: {resp.json()}")
    except Exception:
        print(f"    Response: {resp.text[:300]}")
    return resp

req_module.Session.request = debug_request

# ---------------------------------------------------------------
# 3. Versuche create_api_key mit allen Varianten
# ---------------------------------------------------------------
from py_clob_client.client import ClobClient

configs = [
    {"name": "signature_type=2 + funder", "kwargs": {"signature_type": 2, "funder": FUNDER}},
    {"name": "signature_type=0 (EOA, kein funder)", "kwargs": {"signature_type": 0}},
]

for cfg in configs:
    if not FUNDER and "funder" in cfg["kwargs"]:
        print(f"\n[SKIP] {cfg['name']} — FUNDER_ADDRESS nicht in .env gesetzt")
        continue

    print(f"\n{'=' * 60}")
    print(f"Teste: {cfg['name']}")
    print("=" * 60)
    try:
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=PRIVATE_KEY,
            chain_id=137,
            **cfg["kwargs"],
        )
        creds = client.create_api_key()
        print(f"\n✓ ERFOLG!")
        print(f"  CLOB_API_KEY={creds.api_key}")
        print(f"  CLOB_SECRET={creds.api_secret}")
        print(f"  CLOB_PASS_PHRASE={creds.api_passphrase}")
        break
    except Exception as e:
        print(f"\n✗ Fehler: {e}")

# ---------------------------------------------------------------
# 4. derive_api_key testen (kein Server-Call)
# ---------------------------------------------------------------
print("\n" + "=" * 60)
print("3. derive_api_key TEST (deterministisch, kein POST)")
print("=" * 60)

for cfg in configs:
    if not FUNDER and "funder" in cfg["kwargs"]:
        continue
    try:
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=PRIVATE_KEY,
            chain_id=137,
            **cfg["kwargs"],
        )
        creds = client.derive_api_key()
        print(f"\nVariante '{cfg['name']}':")
        print(f"  CLOB_API_KEY={creds.api_key}")
        print(f"  CLOB_SECRET={creds.api_secret}")
        print(f"  CLOB_PASS_PHRASE={creds.api_passphrase}")
    except Exception as e:
        print(f"\nVariante '{cfg['name']}': Fehler — {e}")
