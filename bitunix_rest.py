"""
Cliente REST de Bitunix — reutiliza la lógica de firma del bot original.
Se usa para consultas complementarias (balance, ticker, posiciones abiertas)
que enriquecen la información antes de publicar en Discord.
"""

import hashlib
import time
import os
import json
import aiohttp

from dotenv import load_dotenv

load_dotenv()


class BitunixREST:
    def __init__(self):
        self.api_key    = os.getenv("BITUNIX_API_KEY")
        self.secret_key = os.getenv("BITUNIX_SECRET_KEY")
        self.base_url   = "https://fapi.bitunix.com"
        self._session: aiohttp.ClientSession | None = None

    # ── sesión compartida ──────────────────────────────────────────────────
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ── firma (idéntica al bot original) ───────────────────────────────────
    def _generate_signature(self, nonce: str, timestamp: str,
                            query_params_str: str = "", body_str: str = "") -> str:
        inner  = nonce + timestamp + self.api_key + query_params_str + body_str
        digest = hashlib.sha256(inner.encode("utf-8")).hexdigest()
        sign   = hashlib.sha256((digest + self.secret_key).encode("utf-8")).hexdigest()
        return sign

    # ── petición genérica (versión async) ──────────────────────────────────
    async def _request(self, method: str, path: str,
                       params: dict | None = None,
                       body: dict | None = None) -> dict | None:
        timestamp = str(int(time.time() * 1000))
        nonce     = os.urandom(16).hex()[:32]

        query_params_sign = ""
        if params:
            query_params_sign = "".join(f"{k}{v}" for k, v in sorted(params.items()))

        body_str = ""
        if body:
            body_str = json.dumps(body, separators=(",", ":"))

        sign = self._generate_signature(nonce, timestamp, query_params_sign, body_str)

        headers = {
            "api-key":      self.api_key,
            "sign":         sign,
            "nonce":        nonce,
            "timestamp":    timestamp,
            "Content-Type": "application/json",
            "Accept":       "application/json",
        }

        url = self.base_url + path
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))

        session = await self._get_session()

        try:
            if method == "GET":
                resp = await session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15))
            else:
                resp = await session.post(url, data=body_str, headers=headers,
                                          timeout=aiohttp.ClientTimeout(total=15))

            if resp.status == 403:
                print(f"   🚫 WAF 403 en {path}")
                return None

            data = await resp.json()
            return data

        except Exception as e:
            print(f"   ❌ REST Error ({path}): {e}")
            return None

    # ── endpoints útiles ───────────────────────────────────────────────────

    async def get_balance(self) -> float:
        """Balance disponible en USDT."""
        data = await self._request("GET", "/api/v1/futures/account",
                                   params={"marginCoin": "USDT"})
        if not data or str(data.get("code")) != "0":
            return 0.0
        items = data.get("data", {})
        if isinstance(items, list):
            items = items[0] if items else {}
        return float(items.get("available", 0))

    async def get_ticker_price(self, symbol: str) -> float:
        """Último precio de un par."""
        data = await self._request("GET", "/api/v1/futures/market/tickers",
                                   params={"symbol": symbol})
        if not data or str(data.get("code")) != "0":
            return 0.0
        raw = data.get("data", [])
        items = raw if isinstance(raw, list) else [raw]
        ticker = next((t for t in items if t.get("symbol") == symbol), None)
        if not ticker:
            return 0.0
        return float(ticker.get("lastPrice", 0))

    async def get_pending_positions(self, symbol: str = "") -> list:
        """Posiciones abiertas (opcionalmente filtradas por símbolo)."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = await self._request("GET",
                                   "/api/v1/futures/position/get_pending_positions",
                                   params=params)
        if not data or str(data.get("code")) != "0":
            return []
        raw = data.get("data", [])
        if isinstance(raw, dict):
            return raw.get("positionList", [])
        return raw

    async def get_pending_tp_sl_orders(self, symbol: str = "") -> list:
        """TP/SL pendientes para un símbolo."""
        params = {"limit": "100"}
        if symbol:
            params["symbol"] = symbol
        data = await self._request("GET",
                                   "/api/v1/futures/tpsl/get_pending_orders",
                                   params=params)
        if not data or str(data.get("code")) != "0":
            return []
        raw = data.get("data", [])
        if isinstance(raw, dict):
            return raw.get("orderList", raw.get("list", []))
        return raw if isinstance(raw, list) else []

    async def get_pending_orders(self, symbol: str = "") -> list:
        """Órdenes pendientes."""
        params = {"limit": "50"}
        if symbol:
            params["symbol"] = symbol
        data = await self._request("GET",
                                   "/api/v1/futures/trade/get_pending_orders",
                                   params=params)
        if not data or str(data.get("code")) != "0":
            return []
        raw = data.get("data", {})
        if isinstance(raw, dict):
            return raw.get("orderList", [])
        return raw
