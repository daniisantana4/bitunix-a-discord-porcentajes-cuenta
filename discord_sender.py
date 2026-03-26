"""
Envía mensajes formateados (embeds) a Discord usando un Webhook.
No necesita bot token ni discord.py — solo una URL de webhook.
"""

import os
from datetime import datetime, timezone

import aiohttp
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_URL    = os.getenv("DISCORD_WEBHOOK_URL", "")
YOUTUBER_NAME  = os.getenv("YOUTUBER_NAME", "BitunixBot")
AVATAR_URL     = os.getenv("AVATAR_URL", "")
COPY_TRADE_URL = os.getenv("COPY_TRADE_URL", "")
REFERRAL_URL   = os.getenv("REFERRAL_URL", "")

# Colores para embeds
COLOR_LONG_OPEN    = 0x00E676
COLOR_SHORT_OPEN   = 0xFF1744
COLOR_CLOSE_WIN    = 0x00BFA5
COLOR_CLOSE_LOSS   = 0xFF5252
COLOR_ORDER_NEW    = 0x448AFF
COLOR_ORDER_CANCEL = 0x9E9E9E
COLOR_TP           = 0x00E676
COLOR_SL           = 0xFF5252
COLOR_TP_SL_UPDATE = 0xFFAB00
COLOR_INFO         = 0x7C4DFF


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

    async def send_embed(self, embed: dict):
        if not self.webhook_url:
            print("⚠️  DISCORD_WEBHOOK_URL no configurado")
            return
        payload = {"username": YOUTUBER_NAME, "embeds": [embed]}
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
    #  APERTURA DE POSICIÓN
    # ══════════════════════════════════════════════════════════════════════

    async def send_position_open(self, symbol: str, side: str, leverage: str,
                                  qty: str, margin: str, entry_price: float,
                                  balance: float):
        is_long = side.upper() in ("BUY", "LONG")
        emoji   = "🟢" if is_long else "🔴"
        label   = "LONG" if is_long else "SHORT"
        color   = COLOR_LONG_OPEN if is_long else COLOR_SHORT_OPEN
        pair    = _format_pair(symbol)

        try:
            pct_str = f"{(float(margin) / balance * 100):.1f}%" if balance > 0 else "N/A"
        except (ValueError, ZeroDivisionError):
            pct_str = "N/A"

        embed = {
            "title": f"{emoji} {label}  —  {pair}",
            "description": _build_links(symbol),
            "color": color,
            "fields": [
                {"name": "📊 Par",            "value": f"`{pair}`",          "inline": True},
                {"name": "📐 Apalancamiento", "value": f"`x{leverage}`",    "inline": True},
                {"name": "💰 Precio entrada", "value": f"`{entry_price}`",  "inline": True},
                {"name": "📦 Cantidad",       "value": f"`{qty}`",          "inline": True},
                {"name": "🏦 Margen",         "value": f"`{margin} USDT`",  "inline": True},
                {"name": "📊 % de cuenta",    "value": f"`{pct_str}`",      "inline": True},
            ],
            "footer": {"text": f"{YOUTUBER_NAME} • Señal automática"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    # ══════════════════════════════════════════════════════════════════════
    #  CIERRE DE POSICIÓN
    # ══════════════════════════════════════════════════════════════════════

    async def send_position_close(self, symbol: str, side: str,
                                   realized_pnl: float, entry_price: float,
                                   exit_price: float, leverage: str):
        is_win    = realized_pnl >= 0
        emoji     = "✅" if is_win else "❌"
        color     = COLOR_CLOSE_WIN if is_win else COLOR_CLOSE_LOSS
        pnl_str   = f"+{realized_pnl:.2f}" if is_win else f"{realized_pnl:.2f}"
        pair      = _format_pair(symbol)
        direction = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"

        pnl_pct = ""
        try:
            lev = float(leverage) if leverage else 1
            if entry_price > 0:
                raw = ((exit_price - entry_price) / entry_price) * 100 * lev
                if direction == "SHORT":
                    raw = -raw
                pnl_pct = f" ({raw:+.2f}%)"
        except (ValueError, ZeroDivisionError):
            pass

        embed = {
            "title": f"{emoji} CIERRE {direction}  —  {pair}",
            "description": _build_links(symbol),
            "color": color,
            "fields": [
                {"name": "📊 Par",            "value": f"`{pair}`",                    "inline": True},
                {"name": "📐 Apalancamiento", "value": f"`x{leverage}`",               "inline": True},
                {"name": "💰 Entrada",        "value": f"`{entry_price}`",             "inline": True},
                {"name": "🏁 Salida",         "value": f"`{exit_price}`",              "inline": True},
                {"name": "💵 PnL",            "value": f"`{pnl_str} USDT{pnl_pct}`",  "inline": True},
                {"name": "📈 Resultado",      "value": "GANANCIA ✅" if is_win else "PÉRDIDA ❌", "inline": True},
            ],
            "footer": {"text": f"{YOUTUBER_NAME} • Señal automática"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    # ══════════════════════════════════════════════════════════════════════
    #  ORDEN PENDIENTE
    # ══════════════════════════════════════════════════════════════════════

    async def send_order_placed(self, symbol: str, side: str, order_type: str,
                                 price: str, qty: str, leverage: str,
                                 trade_side: str, balance: float = 0):
        is_long   = side.upper() == "BUY"
        emoji     = "🟢" if is_long else "🔴"
        direction = "LONG" if is_long else "SHORT"
        action    = "APERTURA" if trade_side.upper() == "OPEN" else "CIERRE"
        pair      = _format_pair(symbol)

        try:
            p   = float(price) if price else 0
            q   = float(qty) if qty else 0
            lev = float(leverage) if leverage else 1
            margin     = round(p * q / lev, 2) if p > 0 else 0
            pct_cuenta = f"{(margin / balance * 100):.1f}%" if balance > 0 else "N/A"
            margin_str = f"{margin} USDT"
        except (ValueError, ZeroDivisionError):
            margin_str = "N/A"
            pct_cuenta = "N/A"

        embed = {
            "title": f"📝 Orden {order_type} — {action} {direction}",
            "description": _build_links(symbol),
            "color": COLOR_ORDER_NEW,
            "fields": [
                {"name": "📊 Par",            "value": f"`{pair}`",          "inline": True},
                {"name": f"{emoji} Dirección", "value": f"`{direction}`",    "inline": True},
                {"name": "📐 Apalancamiento", "value": f"`x{leverage}`",    "inline": True},
                {"name": "💰 Precio",         "value": f"`{price}`",        "inline": True},
                {"name": "📦 Cantidad",       "value": f"`{qty}`",          "inline": True},
                {"name": "🏦 Margen",         "value": f"`{margin_str}`",   "inline": True},
                {"name": "📊 % de cuenta",    "value": f"`{pct_cuenta}`",   "inline": True},
            ],
            "footer": {"text": f"{YOUTUBER_NAME} • Orden pendiente"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    # ══════════════════════════════════════════════════════════════════════
    #  ORDEN EJECUTADA
    # ══════════════════════════════════════════════════════════════════════

    async def send_order_filled(self, symbol: str, side: str, avg_price: str,
                                 qty: str, leverage: str, fee: str,
                                 trade_side: str, balance: float = 0):
        is_long   = side.upper() == "BUY"
        emoji     = "🟢" if is_long else "🔴"
        direction = "LONG" if is_long else "SHORT"
        action    = "APERTURA" if trade_side.upper() == "OPEN" else "CIERRE"
        pair      = _format_pair(symbol)

        try:
            p   = float(avg_price) if avg_price else 0
            q   = float(qty) if qty else 0
            lev = float(leverage) if leverage else 1
            margin     = round(p * q / lev, 2) if p > 0 else 0
            pct_cuenta = f"{(margin / balance * 100):.1f}%" if balance > 0 else "N/A"
            margin_str = f"{margin} USDT"
        except (ValueError, ZeroDivisionError):
            margin_str = "N/A"
            pct_cuenta = "N/A"

        embed = {
            "title": f"⚡ Orden Ejecutada — {action} {direction}",
            "description": _build_links(symbol),
            "color": COLOR_LONG_OPEN if is_long else COLOR_SHORT_OPEN,
            "fields": [
                {"name": "📊 Par",            "value": f"`{pair}`",          "inline": True},
                {"name": f"{emoji} Dirección", "value": f"`{direction}`",    "inline": True},
                {"name": "📐 Apalancamiento", "value": f"`x{leverage}`",    "inline": True},
                {"name": "💰 Precio medio",   "value": f"`{avg_price}`",    "inline": True},
                {"name": "📦 Cantidad",       "value": f"`{qty}`",          "inline": True},
                {"name": "💸 Comisión",       "value": f"`{fee} USDT`",     "inline": True},
                {"name": "🏦 Margen",         "value": f"`{margin_str}`",   "inline": True},
                {"name": "📊 % de cuenta",    "value": f"`{pct_cuenta}`",   "inline": True},
            ],
            "footer": {"text": f"{YOUTUBER_NAME} • Ejecutada"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    # ══════════════════════════════════════════════════════════════════════
    #  ORDEN CANCELADA
    # ══════════════════════════════════════════════════════════════════════

    async def send_order_cancelled(self, symbol: str, side: str, price: str,
                                    qty: str):
        pair      = _format_pair(symbol)
        direction = "LONG" if side.upper() == "BUY" else "SHORT"

        embed = {
            "title": f"🚫 Orden Cancelada — {pair}",
            "description": _build_links(symbol),
            "color": COLOR_ORDER_CANCEL,
            "fields": [
                {"name": "📊 Par",       "value": f"`{pair}`",       "inline": True},
                {"name": "📐 Dirección", "value": f"`{direction}`",  "inline": True},
                {"name": "💰 Precio",    "value": f"`{price}`",      "inline": True},
                {"name": "📦 Cantidad",  "value": f"`{qty}`",        "inline": True},
            ],
            "footer": {"text": f"{YOUTUBER_NAME} • Cancelada"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    # ══════════════════════════════════════════════════════════════════════
    #  NUEVO TP
    # ══════════════════════════════════════════════════════════════════════

    async def send_tp_new(self, symbol: str, side: str, tp_price: str,
                           tp_qty: str, position_qty: str, leverage: str):
        pair      = _format_pair(symbol)
        direction = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"
        pct_pos   = _calc_position_pct(tp_qty, position_qty)

        embed = {
            "title": f"🎯 Nuevo TP  —  {pair}",
            "description": _build_links(symbol),
            "color": COLOR_TP,
            "fields": [
                {"name": "📊 Par",              "value": f"`{pair}`",       "inline": True},
                {"name": "📐 Dirección",        "value": f"`{direction}`",  "inline": True},
                {"name": "📐 Apalancamiento",   "value": f"`x{leverage}`",  "inline": True},
                {"name": "💰 Precio TP",        "value": f"`{tp_price}`",   "inline": True},
                {"name": "📦 Cantidad TP",      "value": f"`{tp_qty}`",     "inline": True},
                {"name": "📊 % de la posición", "value": f"`{pct_pos}`",    "inline": True},
            ],
            "footer": {"text": f"{YOUTUBER_NAME} • Take Profit"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    # ══════════════════════════════════════════════════════════════════════
    #  NUEVO SL
    # ══════════════════════════════════════════════════════════════════════

    async def send_sl_new(self, symbol: str, side: str, sl_price: str,
                           sl_qty: str, position_qty: str, leverage: str):
        pair      = _format_pair(symbol)
        direction = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"
        pct_pos   = _calc_position_pct(sl_qty, position_qty)

        embed = {
            "title": f"🛑 Nuevo SL  —  {pair}",
            "description": _build_links(symbol),
            "color": COLOR_SL,
            "fields": [
                {"name": "📊 Par",              "value": f"`{pair}`",       "inline": True},
                {"name": "📐 Dirección",        "value": f"`{direction}`",  "inline": True},
                {"name": "📐 Apalancamiento",   "value": f"`x{leverage}`",  "inline": True},
                {"name": "💰 Precio SL",        "value": f"`{sl_price}`",   "inline": True},
                {"name": "📦 Cantidad SL",      "value": f"`{sl_qty}`",     "inline": True},
                {"name": "📊 % de la posición", "value": f"`{pct_pos}`",    "inline": True},
            ],
            "footer": {"text": f"{YOUTUBER_NAME} • Stop Loss"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    # ══════════════════════════════════════════════════════════════════════
    #  ACTUALIZACIÓN TP/SL
    # ══════════════════════════════════════════════════════════════════════

    async def send_tp_sl_update(self, symbol: str, side: str, leverage: str,
                                 tp_price: str = "", tp_qty: str = "",
                                 sl_price: str = "", sl_qty: str = "",
                                 position_qty: str = ""):
        pair      = _format_pair(symbol)
        direction = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"

        fields = [
            {"name": "📊 Par",            "value": f"`{pair}`",       "inline": True},
            {"name": "📐 Dirección",      "value": f"`{direction}`",  "inline": True},
            {"name": "📐 Apalancamiento", "value": f"`x{leverage}`",  "inline": True},
        ]
        if tp_price:
            pct = _calc_position_pct(tp_qty, position_qty)
            fields.append({"name": "🎯 Nuevo precio TP", "value": f"`{tp_price}`", "inline": True})
            fields.append({"name": "📦 Cantidad TP",     "value": f"`{tp_qty}`",   "inline": True})
            fields.append({"name": "📊 % de la posición","value": f"`{pct}`",      "inline": True})
        if sl_price:
            pct = _calc_position_pct(sl_qty, position_qty)
            fields.append({"name": "🛑 Nuevo precio SL", "value": f"`{sl_price}`", "inline": True})
            fields.append({"name": "📦 Cantidad SL",     "value": f"`{sl_qty}`",   "inline": True})
            fields.append({"name": "📊 % de la posición","value": f"`{pct}`",      "inline": True})

        embed = {
            "title": f"✏️ Actualización TP/SL  —  {pair}",
            "description": _build_links(symbol),
            "color": COLOR_TP_SL_UPDATE,
            "fields": fields,
            "footer": {"text": f"{YOUTUBER_NAME} • Modificación TP/SL"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    # ══════════════════════════════════════════════════════════════════════
    #  TP/SL CANCELADO
    # ══════════════════════════════════════════════════════════════════════

    async def send_tp_sl_cancelled(self, symbol: str, side: str,
                                    tp_price: str = "", sl_price: str = ""):
        pair      = _format_pair(symbol)
        direction = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"

        fields = [
            {"name": "📊 Par",       "value": f"`{pair}`",       "inline": True},
            {"name": "📐 Dirección", "value": f"`{direction}`",  "inline": True},
        ]
        if tp_price:
            fields.append({"name": "🎯 TP cancelado", "value": f"`{tp_price}`", "inline": True})
        if sl_price:
            fields.append({"name": "🛑 SL cancelado", "value": f"`{sl_price}`", "inline": True})

        embed = {
            "title": f"🚫 TP/SL Cancelado  —  {pair}",
            "description": _build_links(symbol),
            "color": COLOR_ORDER_CANCEL,
            "fields": fields,
            "footer": {"text": f"{YOUTUBER_NAME} • Cancelado"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    # ══════════════════════════════════════════════════════════════════════
    #  POSICIÓN ACTUALIZADA (promediado, cierre parcial)
    # ══════════════════════════════════════════════════════════════════════

    async def send_position_update(self, symbol: str, side: str, qty: str,
                                    unrealized_pnl: str, margin: str,
                                    leverage: str):
        pair      = _format_pair(symbol)
        direction = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"
        emoji     = "🟢" if direction == "LONG" else "🔴"

        embed = {
            "title": f"🔄 Posición Actualizada — {pair}",
            "description": _build_links(symbol),
            "color": COLOR_INFO,
            "fields": [
                {"name": f"{emoji} Dirección",  "value": f"`{direction}`",           "inline": True},
                {"name": "📐 Apalancamiento",   "value": f"`x{leverage}`",           "inline": True},
                {"name": "📦 Cantidad actual",   "value": f"`{qty}`",                "inline": True},
                {"name": "🏦 Margen",            "value": f"`{margin} USDT`",        "inline": True},
                {"name": "📈 PnL no realizado",  "value": f"`{unrealized_pnl} USDT`","inline": True},
            ],
            "footer": {"text": f"{YOUTUBER_NAME} • Actualización"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    # ══════════════════════════════════════════════════════════════════════
    #  ESTADO DEL BOT
    # ══════════════════════════════════════════════════════════════════════

    async def send_bot_status(self, message: str):
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
    s = symbol.upper()
    for quote in ("USDT", "USDC", "BUSD", "USD"):
        if s.endswith(quote):
            return f"{s[:-len(quote)]}/{quote}"
    return s


def _calc_position_pct(qty_str: str, total_qty_str: str) -> str:
    try:
        qty   = float(qty_str) if qty_str else 0
        total = float(total_qty_str) if total_qty_str else 0
        if total > 0 and qty > 0:
            return f"{(qty / total * 100):.0f}%"
    except (ValueError, ZeroDivisionError):
        pass
    return "N/A"


def _build_links(symbol: str) -> str:
    symbol_clean = symbol.upper().replace("/", "")
    links = []
    trade_url = f"https://www.bitunix.com/contract-trade/{symbol_clean}"
    links.append(f"[📈 Operar {_format_pair(symbol)}]({trade_url})")
    if COPY_TRADE_URL:
        links.append(f"[🔄 Copy Trading]({COPY_TRADE_URL})")
    if REFERRAL_URL:
        links.append(f"[🎁 Registro]({REFERRAL_URL})")
    return " • ".join(links)
