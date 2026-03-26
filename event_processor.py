"""
Lógica de negocio: interpreta los eventos crudos del WebSocket de Bitunix
y decide qué publicar en Discord.
"""

from bitunix_rest import BitunixREST
from discord_sender import DiscordSender


class EventProcessor:
    """Procesa eventos de Order Channel, Position Channel y TP/SL Channel."""

    def __init__(self, rest: BitunixREST, discord: DiscordSender):
        self.rest    = rest
        self.discord = discord
        self._known_positions: dict[str, dict] = {}   # positionId → info
        self._known_orders: dict[str, str] = {}        # orderId → último status
        self._known_tp_sl: dict[str, dict] = {}        # orderId → último estado TP/SL
        self._recent_open_signals: set[str] = set()

    # ══════════════════════════════════════════════════════════════════════
    #  ORDER CHANNEL
    # ══════════════════════════════════════════════════════════════════════

    async def handle_order(self, data: dict):
        order_id   = data.get("orderId", "")
        event      = data.get("event", "").upper()
        status     = data.get("orderStatus", "").upper()
        symbol     = data.get("symbol", "")
        side       = data.get("side", "")
        order_type = data.get("type", "")
        price      = data.get("price", "0")
        avg_price  = data.get("averagePrice", "0")
        qty        = data.get("qty", "0")
        leverage   = data.get("leverage", "1")
        fee        = data.get("fee", "0")
        trade_side = _infer_trade_side(data)

        prev_status = self._known_orders.get(order_id, "")
        self._known_orders[order_id] = status

        print(f"   [OrderProcessor] event={event} status={status} prev={prev_status} "
              f"symbol={symbol} side={side} type={order_type} tradeSide={trade_side}")

        # ── Orden NUEVA (pendiente) ───────────────────────────────────────
        if status == "NEW" and prev_status not in ("NEW", "PART_FILLED", "FILLED"):
            if order_type.upper() == "LIMIT":
                balance = await self.rest.get_balance()
                await self.discord.send_order_placed(
                    symbol=symbol, side=side, order_type=order_type,
                    price=price, qty=qty, leverage=leverage,
                    trade_side=trade_side, balance=balance,
                )

        # ── Orden EJECUTADA (FILLED) ──────────────────────────────────────
        elif status == "FILLED" and prev_status != "FILLED":
            balance = await self.rest.get_balance()
            await self.discord.send_order_filled(
                symbol=symbol, side=side, avg_price=avg_price or price,
                qty=qty, leverage=leverage, fee=fee,
                trade_side=trade_side, balance=balance,
            )
            if trade_side == "OPEN":
                await self._send_enriched_position_open(
                    symbol=symbol, side=side, leverage=leverage,
                    qty=qty, price=float(avg_price or price or 0),
                )

        # ── Parcialmente ejecutada ────────────────────────────────────────
        elif status == "PART_FILLED" and prev_status not in ("PART_FILLED", "FILLED"):
            balance = await self.rest.get_balance()
            await self.discord.send_order_filled(
                symbol=symbol, side=side, avg_price=avg_price or price,
                qty=data.get("dealAmount", qty), leverage=leverage, fee=fee,
                trade_side=trade_side, balance=balance,
            )

        # ── Orden CANCELADA ───────────────────────────────────────────────
        elif status in ("CANCELED", "PART_FILLED_CANCELED"):
            if prev_status not in ("CANCELED", "PART_FILLED_CANCELED"):
                await self.discord.send_order_cancelled(
                    symbol=symbol, side=side, price=price, qty=qty,
                )

        if status in ("FILLED", "CANCELED", "PART_FILLED_CANCELED"):
            self._known_orders.pop(order_id, None)

    # ══════════════════════════════════════════════════════════════════════
    #  POSITION CHANNEL
    # ══════════════════════════════════════════════════════════════════════

    async def handle_position(self, data: dict):
        event       = data.get("event", "").upper()
        position_id = data.get("positionId", "")
        symbol      = data.get("symbol", "")
        side        = data.get("side", "")
        leverage    = data.get("leverage", "1")
        qty         = data.get("qty", "0")
        margin      = data.get("margin", "0")
        realized    = data.get("realizedPNL", "0")
        unrealized  = data.get("unrealizedPNL", "0")

        print(f"   [PosProcessor] event={event} symbol={symbol} side={side} "
              f"qty={qty} realizedPNL={realized}")

        # ── Posición ABIERTA ──────────────────────────────────────────────
        if event == "OPEN":
            self._known_positions[position_id] = {
                "symbol": symbol, "side": side, "leverage": leverage,
                "qty": qty, "margin": margin,
            }
            dedup_key = f"{symbol}:{side}"
            if dedup_key in self._recent_open_signals:
                self._recent_open_signals.discard(dedup_key)
                print(f"   [PosProcessor] ⏭️  Apertura ya enviada desde Order Channel, skip")
                return

            entry_price = await self.rest.get_ticker_price(symbol)
            available   = await self.rest.get_balance()
            try:
                balance_total = available + float(margin)
            except (ValueError, TypeError):
                balance_total = available

            await self.discord.send_position_open(
                symbol=symbol, side=side, leverage=leverage,
                qty=qty, margin=margin, entry_price=entry_price,
                balance=balance_total,
            )

        # ── Posición ACTUALIZADA ──────────────────────────────────────────
        elif event == "UPDATE":
            self._known_positions[position_id] = {
                "symbol": symbol, "side": side, "leverage": leverage,
                "qty": qty, "margin": margin,
            }
            await self.discord.send_position_update(
                symbol=symbol, side=side, qty=qty,
                unrealized_pnl=unrealized, margin=margin,
                leverage=leverage,
            )

        # ── Posición CERRADA ──────────────────────────────────────────────
        elif event == "CLOSE":
            exit_price = await self.rest.get_ticker_price(symbol)
            self._known_positions.pop(position_id, {})

            await self.discord.send_position_close(
                symbol=symbol, side=side,
                realized_pnl=float(realized),
                entry_price=exit_price,
                exit_price=exit_price,
                leverage=leverage,
            )

    # ══════════════════════════════════════════════════════════════════════
    #  TP/SL CHANNEL
    # ══════════════════════════════════════════════════════════════════════

    async def handle_tp_sl(self, data: dict):
        """
        Eventos del TP/SL Channel:
          event: CREATE / UPDATE / CLOSE
          status: INIT, NEW, PART_FILLED, CANCELED, FILLED
        """
        event       = data.get("event", "").upper()
        status      = data.get("status", "").upper()
        order_id    = data.get("orderId", "")
        position_id = data.get("positionId", "")
        symbol      = data.get("symbol", "")
        side        = data.get("side", "")
        leverage    = data.get("leverage", "1")
        tp_price    = data.get("tpPrice", "")
        tp_qty      = data.get("tpQty", "")
        sl_price    = data.get("slPrice", "")
        sl_qty      = data.get("slQty", "")

        prev = self._known_tp_sl.get(order_id)
        self._known_tp_sl[order_id] = {
            "event": event, "status": status,
            "tpPrice": tp_price, "slPrice": sl_price,
        }

        # Obtener la cantidad total de la posición para calcular %
        position_qty = await self._get_position_qty(position_id, symbol)

        print(f"   [TP/SL Processor] event={event} status={status} "
              f"tp={tp_price}×{tp_qty} sl={sl_price}×{sl_qty} posQty={position_qty}")

        # ── NUEVO TP/SL (CREATE + NEW) ────────────────────────────────────
        if event == "CREATE" or (status == "NEW" and prev is None):
            if tp_price and tp_qty:
                await self.discord.send_tp_new(
                    symbol=symbol, side=side, tp_price=tp_price,
                    tp_qty=tp_qty, position_qty=position_qty,
                    leverage=leverage,
                )
            if sl_price and sl_qty:
                await self.discord.send_sl_new(
                    symbol=symbol, side=side, sl_price=sl_price,
                    sl_qty=sl_qty, position_qty=position_qty,
                    leverage=leverage,
                )

        # ── ACTUALIZACIÓN TP/SL ───────────────────────────────────────────
        elif event == "UPDATE":
            changed = False
            if prev:
                changed = (prev.get("tpPrice") != tp_price or
                           prev.get("slPrice") != sl_price)
            if changed or not prev:
                await self.discord.send_tp_sl_update(
                    symbol=symbol, side=side, leverage=leverage,
                    tp_price=tp_price, tp_qty=tp_qty,
                    sl_price=sl_price, sl_qty=sl_qty,
                    position_qty=position_qty,
                )

        # ── CANCELADO ─────────────────────────────────────────────────────
        elif status in ("CANCELED",):
            await self.discord.send_tp_sl_cancelled(
                symbol=symbol, side=side,
                tp_price=tp_price, sl_price=sl_price,
            )
            self._known_tp_sl.pop(order_id, None)

        # ── EJECUTADO (el TP o SL se ha disparado) ────────────────────────
        elif status == "FILLED":
            # La ejecución real la manejará el Order/Position Channel,
            # aquí solo limpiamos el cache
            self._known_tp_sl.pop(order_id, None)

    # ── helpers internos ──────────────────────────────────────────────────

    async def _get_position_qty(self, position_id: str, symbol: str) -> str:
        """Obtiene la cantidad total de la posición (cache o API)."""
        if position_id in self._known_positions:
            return self._known_positions[position_id].get("qty", "0")
        # Consultar API
        positions = await self.rest.get_pending_positions(symbol)
        for pos in positions:
            if pos.get("positionId") == position_id:
                self._known_positions[position_id] = pos
                return pos.get("qty", "0")
        # Si no hay posición específica, devolver la primera del símbolo
        if positions:
            return positions[0].get("qty", "0")
        return "0"

    async def _send_enriched_position_open(self, symbol: str, side: str,
                                            leverage: str, qty: str,
                                            price: float):
        dedup_key = f"{symbol}:{side}"
        self._recent_open_signals.add(dedup_key)

        available = await self.rest.get_balance()
        lev       = float(leverage) if leverage else 1
        margin    = round(price * float(qty) / lev, 2) if price > 0 else 0
        balance_total = available + margin

        await self.discord.send_position_open(
            symbol=symbol, side=side, leverage=leverage,
            qty=qty, margin=str(margin), entry_price=price,
            balance=balance_total,
        )


def _infer_trade_side(data: dict) -> str:
    ts = data.get("tradeSide", "")
    if ts:
        return ts.upper()
    event = data.get("event", "").upper()
    if event == "CLOSE":
        return "CLOSE"
    return "OPEN"
