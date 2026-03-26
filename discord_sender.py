"""
Envía mensajes formateados (embeds) a Discord usando un Webhook.
No necesita bot token ni discord.py — solo una URL de webhook.
"""

import os
from datetime import datetime, timezone

import aiohttp
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_URL   = os.getenv("DISCORD_WEBHOOK_URL", "")
YOUTUBER_NAME = os.getenv("YOUTUBER_NAME", "BitunixBot")
AVATAR_URL    = os.getenv("AVATAR_URL", "")

# Colores para embeds (decimal)
COLOR_LONG_OPEN   = 0x00E676   # verde brillante
COLOR_SHORT_OPEN  = 0xFF1744   # rojo brillante
COLOR_CLOSE_WIN   = 0x00BFA5   # verde-teal
COLOR_CLOSE_LOSS  = 0xFF5252   # rojo
COLOR_ORDER_NEW   = 0x448AFF   # azul
COLOR_ORDER_CANCEL = 0x9E9E9E  # gris
COLOR_TP_SL       = 0xFFAB00   # naranja/ámbar
COLOR_INFO        = 0x7C4DFF   # púrpura


class DiscordSender:
    """Envía embeds a Discord a través de un webhook."""

    def __init__(self, webhook_url: str = ""):
        self.webhook_url = webhook_url or WEBHOOK_URL
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ── enviar embed genérico ─────────────────────────────────────────────
    async def send_embed(self, embed: dict):
        """Envía un embed al webhook de Discord."""
        if not self.webhook_url:
            print("⚠️  DISCORD_WEBHOOK_URL no configurado — embed NO enviado")
            return

        payload = {
            "username":   YOUTUBER_NAME,
            "embeds":     [embed],
        }
        if AVATAR_URL:
            payload["avatar_url"] = AVATAR_URL

        session = await self._get_session()
        try:
            resp = await session.post(self.webhook_url, json=payload,
                                      timeout=aiohttp.ClientTimeout(total=10))
            if resp.status in (200, 204):
                print(f"   ✅ Embed enviado a Discord")
            else:
                body = await resp.text()
                print(f"   ❌ Discord webhook HTTP {resp.status}: {body[:200]}")
        except Exception as e:
            print(f"   ❌ Error enviando a Discord: {e}")

    # ══════════════════════════════════════════════════════════════════════
    #  EMBEDS ESPECÍFICOS
    # ══════════════════════════════════════════════════════════════════════

    async def send_position_open(self, symbol: str, side: str, leverage: str,
                                  qty: str, margin: str, entry_price: float,
                                  balance: float):
        """Posición abierta — LONG o SHORT."""
        is_long = side.upper() in ("BUY", "LONG")
        emoji   = "🟢" if is_long else "🔴"
        label   = "LONG" if is_long else "SHORT"
        color   = COLOR_LONG_OPEN if is_long else COLOR_SHORT_OPEN

        pair_display = _format_pair(symbol)

        embed = {
            "title": f"{emoji} {label}  —  {pair_display}",
            "color": color,
            "fields": [
                {"name": "📊 Par",          "value": f"`{pair_display}`",        "inline": True},
                {"name": "📐 Apalancamiento","value": f"`x{leverage}`",          "inline": True},
                {"name": "💰 Precio entrada","value": f"`{entry_price}`",        "inline": True},
                {"name": "📦 Cantidad",      "value": f"`{qty}`",               "inline": True},
                {"name": "🏦 Margen",        "value": f"`{margin} USDT`",       "inline": True},
                {"name": "💵 Balance",        "value": f"`{balance:.2f} USDT`",  "inline": True},
            ],
            "footer": {"text": f"{YOUTUBER_NAME} • Señal automática"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    async def send_position_close(self, symbol: str, side: str,
                                   realized_pnl: float, entry_price: float,
                                   exit_price: float, leverage: str):
        """Posición cerrada — muestra PnL."""
        is_win = realized_pnl >= 0
        emoji  = "✅" if is_win else "❌"
        color  = COLOR_CLOSE_WIN if is_win else COLOR_CLOSE_LOSS
        pnl_str = f"+{realized_pnl:.2f}" if is_win else f"{realized_pnl:.2f}"

        pair_display = _format_pair(symbol)
        direction    = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"

        # Calcular % de PnL respecto al margen estimado
        pnl_pct = ""
        try:
            lev = float(leverage) if leverage else 1
            if entry_price > 0:
                raw_pct = ((exit_price - entry_price) / entry_price) * 100 * lev
                if direction == "SHORT":
                    raw_pct = -raw_pct
                pnl_pct = f" ({raw_pct:+.2f}%)"
        except (ValueError, ZeroDivisionError):
            pass

        embed = {
            "title": f"{emoji} CIERRE {direction}  —  {pair_display}",
            "color": color,
            "fields": [
                {"name": "📊 Par",            "value": f"`{pair_display}`",          "inline": True},
                {"name": "📐 Apalancamiento", "value": f"`x{leverage}`",             "inline": True},
                {"name": "💰 Entrada",         "value": f"`{entry_price}`",          "inline": True},
                {"name": "🏁 Salida",          "value": f"`{exit_price}`",           "inline": True},
                {"name": "💵 PnL",             "value": f"`{pnl_str} USDT{pnl_pct}`","inline": True},
                {"name": "📈 Resultado",       "value": "GANANCIA ✅" if is_win else "PÉRDIDA ❌", "inline": True},
            ],
            "footer": {"text": f"{YOUTUBER_NAME} • Señal automática"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    async def send_order_placed(self, symbol: str, side: str, order_type: str,
                                 price: str, qty: str, leverage: str,
                                 trade_side: str):
        """Orden creada (pendiente)."""
        is_long   = side.upper() == "BUY"
        emoji     = "🟢" if is_long else "🔴"
        direction = "LONG" if is_long else "SHORT"
        action    = "APERTURA" if trade_side.upper() == "OPEN" else "CIERRE"

        pair_display = _format_pair(symbol)

        embed = {
            "title": f"📝 Orden {order_type} — {action} {direction}",
            "color": COLOR_ORDER_NEW,
            "fields": [
                {"name": "📊 Par",           "value": f"`{pair_display}`",  "inline": True},
                {"name": f"{emoji} Dirección","value": f"`{direction}`",    "inline": True},
                {"name": "📐 Apalancamiento","value": f"`x{leverage}`",    "inline": True},
                {"name": "💰 Precio",        "value": f"`{price}`",        "inline": True},
                {"name": "📦 Cantidad",      "value": f"`{qty}`",          "inline": True},
                {"name": "📋 Tipo",          "value": f"`{order_type}`",   "inline": True},
            ],
            "footer": {"text": f"{YOUTUBER_NAME} • Orden pendiente"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    async def send_order_filled(self, symbol: str, side: str, avg_price: str,
                                 qty: str, leverage: str, fee: str,
                                 trade_side: str):
        """Orden ejecutada completamente."""
        is_long   = side.upper() == "BUY"
        emoji     = "🟢" if is_long else "🔴"
        direction = "LONG" if is_long else "SHORT"
        action    = "APERTURA" if trade_side.upper() == "OPEN" else "CIERRE"

        pair_display = _format_pair(symbol)

        embed = {
            "title": f"⚡ Orden Ejecutada — {action} {direction}",
            "color": COLOR_LONG_OPEN if is_long else COLOR_SHORT_OPEN,
            "fields": [
                {"name": "📊 Par",           "value": f"`{pair_display}`",  "inline": True},
                {"name": f"{emoji} Dirección","value": f"`{direction}`",    "inline": True},
                {"name": "📐 Apalancamiento","value": f"`x{leverage}`",    "inline": True},
                {"name": "💰 Precio medio",  "value": f"`{avg_price}`",    "inline": True},
                {"name": "📦 Cantidad",      "value": f"`{qty}`",          "inline": True},
                {"name": "💸 Comisión",      "value": f"`{fee} USDT`",     "inline": True},
            ],
            "footer": {"text": f"{YOUTUBER_NAME} • Ejecutada"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    async def send_order_cancelled(self, symbol: str, side: str, price: str,
                                    qty: str):
        """Orden cancelada."""
        pair_display = _format_pair(symbol)
        direction    = "LONG" if side.upper() == "BUY" else "SHORT"

        embed = {
            "title": f"🚫 Orden Cancelada — {pair_display}",
            "color": COLOR_ORDER_CANCEL,
            "fields": [
                {"name": "📊 Par",       "value": f"`{pair_display}`", "inline": True},
                {"name": "📐 Dirección", "value": f"`{direction}`",    "inline": True},
                {"name": "💰 Precio",    "value": f"`{price}`",        "inline": True},
                {"name": "📦 Cantidad",  "value": f"`{qty}`",          "inline": True},
            ],
            "footer": {"text": f"{YOUTUBER_NAME} • Cancelada"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    async def send_position_update(self, symbol: str, side: str, qty: str,
                                    unrealized_pnl: str, margin: str,
                                    leverage: str):
        """Posición actualizada (ej. promediada, parcialmente cerrada)."""
        pair_display = _format_pair(symbol)
        direction    = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"
        emoji        = "🟢" if direction == "LONG" else "🔴"

        embed = {
            "title": f"🔄 Posición Actualizada — {pair_display}",
            "color": COLOR_INFO,
            "fields": [
                {"name": f"{emoji} Dirección",  "value": f"`{direction}`",          "inline": True},
                {"name": "📐 Apalancamiento",   "value": f"`x{leverage}`",          "inline": True},
                {"name": "📦 Cantidad actual",   "value": f"`{qty}`",               "inline": True},
                {"name": "🏦 Margen",            "value": f"`{margin} USDT`",       "inline": True},
                {"name": "📈 PnL no realizado",  "value": f"`{unrealized_pnl} USDT`","inline": True},
            ],
            "footer": {"text": f"{YOUTUBER_NAME} • Actualización"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    async def send_bot_status(self, message: str):
        """Mensaje de estado del bot (inicio, reconexión, etc.)."""
        embed = {
            "title": "🤖 Estado del Bot",
            "description": message,
            "color": COLOR_INFO,
            "footer": {"text": YOUTUBER_NAME},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)


# ── utilidades ────────────────────────────────────────────────────────────

def _format_pair(symbol: str) -> str:
    """BTCUSDT → BTC/USDT"""
    s = symbol.upper()
    for quote in ("USDT", "USDC", "BUSD", "USD"):
        if s.endswith(quote):
            base = s[: -len(quote)]
            return f"{base}/{quote}"
    return s
