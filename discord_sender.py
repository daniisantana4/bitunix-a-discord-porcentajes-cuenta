"""
Envía mensajes formateados (embeds) a Discord usando un Webhook.
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
    #  CIERRE TOTAL
    # ══════════════════════════════════════════════════════════════════════

    async def send_position_close(self, symbol: str, side: str,
                                   realized_pnl: float, margin: float,
                                   leverage: str):
        is_win    = realized_pnl >= 0
        emoji     = "✅" if is_win else "❌"
        color     = COLOR_CLOSE_WIN if is_win else COLOR_CLOSE_LOSS
        pnl_str   = f"+{realized_pnl:.2f}" if is_win else f"{realized_pnl:.2f}"
        pair      = _format_pair(symbol)
        direction = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"
        pnl_pct = ""
        try:
            if margin > 0:
                pnl_pct = f" ({(realized_pnl / margin) * 100:+.2f}%)"
        except (ValueError, ZeroDivisionError):
            pass
        embed = {
            "title": f"{emoji} CIERRE {direction}  —  {pair}",
            "description": _build_links(symbol),
            "color": color,
            "fields": [
                {"name": "📊 Par",            "value": f"`{pair}`",                    "inline": True},
                {"name": "📐 Apalancamiento", "value": f"`x{leverage}`",               "inline": True},
                {"name": "📦 % cerrado",      "value": f"`100%`",                      "inline": True},
                {"name": "🏦 Margen",         "value": f"`{margin:.2f} USDT`",         "inline": True},
                {"name": "💵 PnL",            "value": f"`{pnl_str} USDT{pnl_pct}`",  "inline": True},
                {"name": "📈 Resultado",      "value": "GANANCIA ✅" if is_win else "PÉRDIDA ❌", "inline": True},
            ],
            "footer": {"text": f"{YOUTUBER_NAME} • Señal automática"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    # ══════════════════════════════════════════════════════════════════════
    #  CIERRE PARCIAL
    # ══════════════════════════════════════════════════════════════════════

    async def send_position_partial_close(self, symbol: str, side: str,
                                           leverage: str, pct_closed: str,
                                           realized_pnl: float, margin: float):
        is_win    = realized_pnl >= 0
        emoji     = "✅" if is_win else "❌"
        color     = COLOR_CLOSE_WIN if is_win else COLOR_CLOSE_LOSS
        pnl_str   = f"+{realized_pnl:.2f}" if is_win else f"{realized_pnl:.2f}"
        pair      = _format_pair(symbol)
        direction = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"
        pnl_pct = ""
        try:
            if margin > 0:
                pnl_pct = f" ({(realized_pnl / margin) * 100:+.2f}%)"
        except (ValueError, ZeroDivisionError):
            pass
        embed = {
            "title": f"{emoji} CIERRE PARCIAL {direction}  —  {pair}",
            "description": _build_links(symbol),
            "color": color,
            "fields": [
                {"name": "📊 Par",             "value": f"`{pair}`",                    "inline": True},
                {"name": "📐 Apalancamiento",  "value": f"`x{leverage}`",               "inline": True},
                {"name": "📦 % cerrado",       "value": f"`{pct_closed}`",              "inline": True},
                {"name": "🏦 Margen cerrado",  "value": f"`{margin:.2f} USDT`",         "inline": True},
                {"name": "💵 PnL",             "value": f"`{pnl_str} USDT{pnl_pct}`",  "inline": True},
                {"name": "📈 Resultado",       "value": "GANANCIA ✅" if is_win else "PÉRDIDA ❌", "inline": True},
            ],
            "footer": {"text": f"{YOUTUBER_NAME} • Señal automática"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    # ══════════════════════════════════════════════════════════════════════
    #  COMPRA/VENTA A MERCADO (promediado / DCA)
    # ══════════════════════════════════════════════════════════════════════

    async def send_position_add(self, symbol: str, side: str, leverage: str,
                                 added_qty: str, total_qty: str,
                                 entry_price: str, margin: str,
                                 pct_account: str):
        is_long   = side.upper() in ("BUY", "LONG")
        action    = "Compra" if is_long else "Venta"
        emoji     = "🟢" if is_long else "🔴"
        direction = "LONG" if is_long else "SHORT"
        pair      = _format_pair(symbol)
        color     = COLOR_LONG_OPEN if is_long else COLOR_SHORT_OPEN

        embed = {
            "title": f"{emoji} {action} a mercado  —  {pair}",
            "description": _build_links(symbol),
            "color": color,
            "fields": [
                {"name": "📊 Par",                 "value": f"`{pair}`",            "inline": True},
                {"name": f"{emoji} Dirección",      "value": f"`{direction}`",       "inline": True},
                {"name": "📐 Apalancamiento",      "value": f"`x{leverage}`",        "inline": True},
                {"name": "💰 Nuevo precio entrada", "value": f"`{entry_price}`",     "inline": True},
                {"name": "📦 Cantidad añadida",     "value": f"`{added_qty}`",       "inline": True},
                {"name": "📦 Cantidad total",       "value": f"`{total_qty}`",       "inline": True},
                {"name": "🏦 Margen total",         "value": f"`{margin} USDT`",     "inline": True},
                {"name": "📊 % de cuenta",          "value": f"`{pct_account}`",     "inline": True},
            ],
            "footer": {"text": f"{YOUTUBER_NAME} • Promediado"},
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
            p = float(price) if price else 0
            q = float(qty) if qty else 0
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
            p = float(avg_price) if avg_price else 0
            q = float(qty) if qty else 0
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
    #  NUEVO TP  (con precio de la posición/orden)
    # ══════════════════════════════════════════════════════════════════════

    async def send_tp_new(self, symbol: str, side: str, tp_price: str,
                           pct_position: str, leverage: str,
                           entry_price: str = ""):
        pair      = _format_pair(symbol)
        direction = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"
        fields = [
            {"name": "📊 Par",              "value": f"`{pair}`",          "inline": True},
            {"name": "📐 Dirección",        "value": f"`{direction}`",     "inline": True},
            {"name": "📐 Apalancamiento",   "value": f"`x{leverage}`",     "inline": True},
        ]
        if entry_price:
            fields.append({"name": "💰 Precio posición", "value": f"`{entry_price}`", "inline": True})
        fields.extend([
            {"name": "🎯 Precio TP",        "value": f"`{tp_price}`",      "inline": True},
            {"name": "📊 % de la posición", "value": f"`{pct_position}`",  "inline": True},
        ])
        embed = {
            "title": f"🎯 Nuevo TP  —  {pair}",
            "description": _build_links(symbol),
            "color": COLOR_TP,
            "fields": fields,
            "footer": {"text": f"{YOUTUBER_NAME} • Take Profit"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    # ══════════════════════════════════════════════════════════════════════
    #  NUEVO SL  (con precio de la posición/orden)
    # ══════════════════════════════════════════════════════════════════════

    async def send_sl_new(self, symbol: str, side: str, sl_price: str,
                           pct_position: str, leverage: str,
                           entry_price: str = ""):
        pair      = _format_pair(symbol)
        direction = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"
        fields = [
            {"name": "📊 Par",              "value": f"`{pair}`",          "inline": True},
            {"name": "📐 Dirección",        "value": f"`{direction}`",     "inline": True},
            {"name": "📐 Apalancamiento",   "value": f"`x{leverage}`",     "inline": True},
        ]
        if entry_price:
            fields.append({"name": "💰 Precio posición", "value": f"`{entry_price}`", "inline": True})
        fields.extend([
            {"name": "🛑 Precio SL",        "value": f"`{sl_price}`",      "inline": True},
            {"name": "📊 % de la posición", "value": f"`{pct_position}`",  "inline": True},
        ])
        embed = {
            "title": f"🛑 Nuevo SL  —  {pair}",
            "description": _build_links(symbol),
            "color": COLOR_SL,
            "fields": fields,
            "footer": {"text": f"{YOUTUBER_NAME} • Stop Loss"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    # ══════════════════════════════════════════════════════════════════════
    #  ACTUALIZACIÓN TP/SL  (con precio de la posición/orden)
    # ══════════════════════════════════════════════════════════════════════

    async def send_tp_sl_update(self, symbol: str, side: str, leverage: str,
                                 tp_price: str = "", pct_tp: str = "",
                                 sl_price: str = "", pct_sl: str = "",
                                 entry_price: str = ""):
        pair      = _format_pair(symbol)
        direction = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"
        fields = [
            {"name": "📊 Par",            "value": f"`{pair}`",       "inline": True},
            {"name": "📐 Dirección",      "value": f"`{direction}`",  "inline": True},
            {"name": "📐 Apalancamiento", "value": f"`x{leverage}`",  "inline": True},
        ]
        if entry_price:
            fields.append({"name": "💰 Precio posición", "value": f"`{entry_price}`", "inline": True})
        if tp_price:
            fields.append({"name": "🎯 Nuevo precio TP", "value": f"`{tp_price}`",  "inline": True})
            if pct_tp:
                fields.append({"name": "📊 % de la posición","value": f"`{pct_tp}`", "inline": True})
        if sl_price:
            fields.append({"name": "🛑 Nuevo precio SL", "value": f"`{sl_price}`",  "inline": True})
            if pct_sl:
                fields.append({"name": "📊 % de la posición","value": f"`{pct_sl}`", "inline": True})
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
    #  TP / SL ELIMINADO
    # ══════════════════════════════════════════════════════════════════════

    async def send_tp_cancelled(self, symbol: str, side: str, tp_price: str,
                                 pct_position: str):
        pair      = _format_pair(symbol)
        direction = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"
        fields = [
            {"name": "📊 Par",       "value": f"`{pair}`",       "inline": True},
            {"name": "📐 Dirección", "value": f"`{direction}`",  "inline": True},
            {"name": "🎯 Precio TP", "value": f"`{tp_price}`",   "inline": True},
        ]
        if pct_position:
            fields.append({"name": "📊 % de la posición", "value": f"`{pct_position}`", "inline": True})
        embed = {
            "title": f"🚫 TP Eliminado  —  {pair}",
            "description": _build_links(symbol),
            "color": COLOR_ORDER_CANCEL,
            "fields": fields,
            "footer": {"text": f"{YOUTUBER_NAME} • TP Eliminado"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    async def send_sl_cancelled(self, symbol: str, side: str, sl_price: str,
                                 pct_position: str):
        pair      = _format_pair(symbol)
        direction = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"
        fields = [
            {"name": "📊 Par",       "value": f"`{pair}`",       "inline": True},
            {"name": "📐 Dirección", "value": f"`{direction}`",  "inline": True},
            {"name": "🛑 Precio SL", "value": f"`{sl_price}`",   "inline": True},
        ]
        if pct_position:
            fields.append({"name": "📊 % de la posición", "value": f"`{pct_position}`", "inline": True})
        embed = {
            "title": f"🚫 SL Eliminado  —  {pair}",
            "description": _build_links(symbol),
            "color": COLOR_ORDER_CANCEL,
            "fields": fields,
            "footer": {"text": f"{YOUTUBER_NAME} • SL Eliminado"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.send_embed(embed)

    # ══════════════════════════════════════════════════════════════════════
    #  POSICIÓN ACTUALIZADA
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


def _format_pair(symbol: str) -> str:
    s = symbol.upper()
    for quote in ("USDT", "USDC", "BUSD", "USD"):
        if s.endswith(quote):
            return f"{s[:-len(quote)]}/{quote}"
    return s

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
