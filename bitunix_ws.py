"""
Conexión WebSocket autenticada a los canales privados de Bitunix.
Escucha Order Channel y Position Channel en tiempo real.
"""

import asyncio
import hashlib
import json
import os
import time

import websockets

from dotenv import load_dotenv

load_dotenv()

WS_URL = "wss://fapi.bitunix.com/private/"

# Intervalo de ping para mantener la conexión viva (segundos)
PING_INTERVAL = 25


class BitunixWS:
    """Gestiona la conexión WebSocket privada con Bitunix."""

    def __init__(self, on_order_event, on_position_event):
        """
        Args:
            on_order_event:    async callback(data: dict) — se invoca en cada evento del Order Channel
            on_position_event: async callback(data: dict) — se invoca en cada evento del Position Channel
        """
        self.api_key    = os.getenv("BITUNIX_API_KEY")
        self.secret_key = os.getenv("BITUNIX_SECRET_KEY")
        self._on_order    = on_order_event
        self._on_position = on_position_event
        self._ws = None
        self._running = False

    # ── firma para WebSocket (según la doc de Bitunix) ─────────────────────
    def _ws_sign(self, nonce: str, timestamp: int) -> str:
        """
        La firma del WS es diferente de la REST:
        1) digest = SHA256(nonce + timestamp + apiKey)
        2) sign   = SHA256(digest + secretKey)
        No incluye queryParams ni body.
        """
        digest = hashlib.sha256(
            (nonce + str(timestamp) + self.api_key).encode("utf-8")
        ).hexdigest()
        sign = hashlib.sha256(
            (digest + self.secret_key).encode("utf-8")
        ).hexdigest()
        return sign

    # ── login ──────────────────────────────────────────────────────────────
    def _build_login_msg(self) -> str:
        timestamp = int(time.time() * 1000)
        nonce     = os.urandom(16).hex()[:32]
        sign      = self._ws_sign(nonce, timestamp)

        return json.dumps({
            "op": "login",
            "args": [{
                "apiKey":    self.api_key,
                "timestamp": timestamp,
                "nonce":     nonce,
                "sign":      sign,
            }]
        })

    # ── suscripción a canales ──────────────────────────────────────────────
    @staticmethod
    def _build_subscribe_msg() -> str:
        return json.dumps({
            "op": "subscribe",
            "args": [
                {"ch": "order"},
                {"ch": "position"},
            ]
        })

    # ── ping keep-alive ───────────────────────────────────────────────────
    async def _ping_loop(self):
        """Envía pings periódicos para que Bitunix no cierre la conexión."""
        while self._running and self._ws:
            try:
                msg = json.dumps({"op": "ping", "ping": int(time.time())})
                await self._ws.send(msg)
            except Exception:
                break
            await asyncio.sleep(PING_INTERVAL)

    # ── bucle principal ───────────────────────────────────────────────────
    async def run_forever(self):
        """Conecta, autentica, suscribe y escucha indefinidamente con reconexión."""
        self._running = True

        while self._running:
            try:
                print(f"🔌 Conectando a {WS_URL} …")
                async with websockets.connect(
                    WS_URL,
                    ping_interval=None,   # gestionamos ping manualmente
                    ping_timeout=None,
                    close_timeout=10,
                ) as ws:
                    self._ws = ws
                    print("✅ WebSocket conectado")

                    # 1) Login
                    await ws.send(self._build_login_msg())
                    login_resp = await asyncio.wait_for(ws.recv(), timeout=10)
                    print(f"🔑 Login response: {login_resp}")

                    # 2) Suscribir canales
                    await ws.send(self._build_subscribe_msg())
                    sub_resp = await asyncio.wait_for(ws.recv(), timeout=10)
                    print(f"📡 Subscribe response: {sub_resp}")

                    # 3) Lanzar ping en background
                    ping_task = asyncio.create_task(self._ping_loop())

                    # 4) Escuchar mensajes
                    try:
                        async for raw_msg in ws:
                            await self._handle_message(raw_msg)
                    finally:
                        ping_task.cancel()

            except (websockets.ConnectionClosed, ConnectionError) as e:
                print(f"⚠️  Conexión cerrada: {e}")
            except asyncio.TimeoutError:
                print("⚠️  Timeout durante login/subscribe")
            except Exception as e:
                print(f"❌ Error WS inesperado: {e}")

            if self._running:
                wait = 5
                print(f"🔄 Reconectando en {wait}s …")
                await asyncio.sleep(wait)

    # ── dispatcher ────────────────────────────────────────────────────────
    async def _handle_message(self, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        # Ignorar pongs
        if msg.get("op") == "ping":
            return

        ch = msg.get("ch", "")

        if ch == "order":
            data = msg.get("data", {})
            print(f"📨 Order event: {data.get('event')} | {data.get('symbol')} | "
                  f"status={data.get('orderStatus')} | side={data.get('side')}")
            try:
                await self._on_order(data)
            except Exception as e:
                print(f"❌ Error en on_order callback: {e}")

        elif ch == "position":
            data = msg.get("data", {})
            print(f"📨 Position event: {data.get('event')} | {data.get('symbol')} | "
                  f"side={data.get('side')}")
            try:
                await self._on_position(data)
            except Exception as e:
                print(f"❌ Error en on_position callback: {e}")

    # ── parada ────────────────────────────────────────────────────────────
    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()
