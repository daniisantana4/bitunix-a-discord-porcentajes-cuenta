"""
Lógica de negocio: interpreta los eventos del WebSocket de Bitunix.

POLLING: Cada 3s comprueba vía REST los TP/SL de órdenes pendientes
y posiciones abiertas. Esto cubre los casos en los que Bitunix no envía
evento WebSocket (ej: SL añadido a orden pendiente, arrastrar TP/SL).
"""

import asyncio
import json
import time as _time
from bitunix_rest import BitunixREST
from discord_sender import DiscordSender

POLL_INTERVAL = 3  # segundos


class EventProcessor:

    def __init__(self, rest: BitunixREST, discord: DiscordSender):
        self.rest    = rest
        self.discord = discord
        self._known_positions: dict[str, dict] = {}
        self._known_orders: dict[str, str] = {}

        # Cache TP/SL de POSICIONES (vía REST /tpsl/get_pending_orders)
        self._cached_tp_sl: dict[str, dict[str, dict]] = {}

        # Cache TP/SL de ÓRDENES PENDIENTES (vía Order Channel + REST)
        self._order_tp_sl: dict[str, dict] = {}

        # Estado agregado de TP/SL de órdenes pendientes (por valor, no ID)
        self._cached_pending_agg: dict[str, dict] = {}

        self._order_buffer: dict[str, dict] = {}
        self._recent_closes: dict[str, float] = {}

        # Símbolos activos (posiciones + órdenes) para polling
        self._active_symbols: set[str] = set()

    # ══════════════════════════════════════════════════════════════════════
    #  POLLING (se lanza desde main.py como tarea en background)
    # ══════════════════════════════════════════════════════════════════════

    async def poll_loop(self):
        """Comprueba TP/SL cada N segundos para TODOS los símbolos activos."""
        first_run = True
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            try:
                symbols = list(self._active_symbols)
                for symbol in symbols:
                    if first_run:
                        # Primera pasada: solo llenar caches sin publicar
                        await self._init_caches(symbol)
                    else:
                        side, leverage, qty = self._get_position_info(symbol)
                        if side:
                            await self._check_position_tp_sl(symbol, side, leverage, qty)
                        await self._check_pending_orders_tp_sl_rest(symbol)
                first_run = False
            except Exception as e:
                print(f"   ❌ Error en polling: {e}")

    def _get_position_info(self, symbol: str):
        for pid, info in self._known_positions.items():
            if info.get("symbol") == symbol:
                return info.get("side", ""), info.get("leverage", "1"), info.get("qty", "0")
        return "", "1", "0"

    async def _init_caches(self, symbol: str):
        """Llena caches silenciosamente (sin publicar) al arrancar."""
        # Cache de TP/SL de posiciones
        await self._refresh_tp_sl_cache(symbol)
        # Cache de TP/SL de órdenes pendientes
        orders = await self.rest.get_pending_orders(symbol)
        current_tps: set[str] = set()
        current_sls: set[str] = set()
        for order in orders:
            oid = order.get("orderId", "")
            if not oid:
                continue
            tp = _first_valid(order.get("tpPrice"), order.get("tpOrderPrice"))
            sl = _first_valid(order.get("slPrice"), order.get("slOrderPrice"))
            if tp:
                current_tps.add(tp)
            if sl:
                current_sls.add(sl)
            self._order_tp_sl[oid] = {
                "tpPrice": tp, "slPrice": sl,
                "symbol": symbol, "side": order.get("side", ""),
                "leverage": order.get("leverage", "1"),
                "price": order.get("price", "0"),
            }
        cache_key = f"_pending_{symbol}"
        self._cached_pending_agg[cache_key] = {"tps": current_tps, "sls": current_sls}
        print(f"   [Init] {symbol}: {len(orders)} órdenes, TPs={current_tps}, SLs={current_sls}")

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
        trade_side = (data.get("tradeSide") or "").upper()

        tp_price = _first_valid(data.get("tpPrice"), data.get("tpOrderPrice"))
        sl_price = _first_valid(data.get("slPrice"), data.get("slOrderPrice"))

        prev_status = self._known_orders.get(order_id, "")
        self._known_orders[order_id] = status

        self._active_symbols.add(symbol)

        print(f"   [Order] event={event} status={status} prev={prev_status} "
              f"symbol={symbol} side={side} type={order_type} "
              f"tradeSide='{trade_side}' tp={tp_price} sl={sl_price}")

        # ── tradeSide=CLOSE explícito → suprimir ──────────────────────
        if trade_side == "CLOSE":
            if status in ("FILLED", "CANCELED", "PART_FILLED_CANCELED"):
                self._cleanup_order(order_id)
            return

        # ── Orden NUEVA ───────────────────────────────────────────────
        if status == "NEW" and prev_status not in ("NEW", "PART_FILLED", "FILLED"):
            if order_type.upper() == "LIMIT" and trade_side != "CLOSE":
                # Solo guardar en cache — el polling detectará TP/SL
                self._order_tp_sl[order_id] = {
                    "tpPrice": tp_price, "slPrice": sl_price,
                    "symbol": symbol, "side": side,
                    "leverage": leverage, "price": price,
                }
                self._sync_pending_agg(symbol)
                await self._buffer_order_placed(order_id, data)

        # ── Orden EJECUTADA ───────────────────────────────────────────
        elif status == "FILLED" and prev_status != "FILLED":
            self._cancel_buffered_order(order_id)
            if self._is_recent_close(symbol):
                self._cleanup_order(order_id)
                return
            # NO publicar nada aquí — el Position Channel OPEN se encarga
            self._cleanup_order(order_id)

        # ── Parcialmente ejecutada ────────────────────────────────────
        elif status == "PART_FILLED" and prev_status not in ("PART_FILLED", "FILLED"):
            self._cancel_buffered_order(order_id)
            # NO publicar — el Position Channel UPDATE se encarga

        # ── Orden CANCELADA ───────────────────────────────────────────
        elif status in ("CANCELED", "PART_FILLED_CANCELED"):
            self._cancel_buffered_order(order_id)
            if prev_status not in ("CANCELED", "PART_FILLED_CANCELED"):
                if not self._is_recent_close(symbol):
                    await self.discord.send_order_cancelled(
                        symbol=symbol, side=side, price=price, qty=qty,
                    )
            self._cleanup_order(order_id)

        if status in ("FILLED", "CANCELED", "PART_FILLED_CANCELED"):
            self._known_orders.pop(order_id, None)

    def _cleanup_order(self, order_id: str):
        info = self._order_tp_sl.pop(order_id, {})
        self._known_orders.pop(order_id, None)
        symbol = info.get("symbol", "")
        if symbol:
            self._sync_pending_agg(symbol)

    def _sync_pending_agg(self, symbol: str):
        """Reconstruye el cache agregado a partir de _order_tp_sl."""
        cache_key = f"_pending_{symbol}"
        tps: set[str] = set()
        sls: set[str] = set()
        for info in self._order_tp_sl.values():
            if info.get("symbol") != symbol:
                continue
            tp = info.get("tpPrice", "")
            sl = info.get("slPrice", "")
            if tp:
                tps.add(tp)
            if sl:
                sls.add(sl)
        self._cached_pending_agg[cache_key] = {"tps": tps, "sls": sls}

    # ── TP/SL de órdenes pendientes (via REST polling) ────────────────
    # Comparamos por VALOR (precio) no por orderId, porque Bitunix
    # recrea la orden (nuevo ID) al modificar TP → si comparamos por ID,
    # vemos "SL eliminado + Nuevo SL" aunque el SL no cambió.

    async def _check_pending_orders_tp_sl_rest(self, symbol: str):
        """Consulta órdenes pendientes por REST y detecta cambios TP/SL."""
        orders = await self.rest.get_pending_orders(symbol)

        # Construir estado actual agregado: conjuntos de precios TP y SL
        current_tps: set[str] = set()
        current_sls: set[str] = set()
        order_info = {"side": "", "leverage": "1", "price": "0"}

        for order in orders:
            oid = order.get("orderId", "")
            if not oid:
                continue

            tp = _first_valid(order.get("tpPrice"), order.get("tpOrderPrice"))
            sl = _first_valid(order.get("slPrice"), order.get("slOrderPrice"))
            side     = order.get("side", "")
            leverage = order.get("leverage", "1")
            price    = order.get("price", "0")

            if tp:
                current_tps.add(tp)
            if sl:
                current_sls.add(sl)

            order_info = {"side": side, "leverage": leverage, "price": price}

            # Actualizar cache por orderId
            self._order_tp_sl[oid] = {
                "tpPrice": tp, "slPrice": sl,
                "symbol": symbol, "side": side,
                "leverage": leverage, "price": price,
            }

        # Limpiar orderIds que ya no existen en REST
        current_oids = {o.get("orderId", "") for o in orders if o.get("orderId")}
        stale = [k for k, v in self._order_tp_sl.items()
                 if v.get("symbol") == symbol and k not in current_oids]
        for k in stale:
            self._order_tp_sl.pop(k, None)

        # Obtener estado anterior agregado
        cache_key = f"_pending_{symbol}"
        prev_tps: set[str] = self._cached_pending_agg.get(cache_key, {}).get("tps", set())
        prev_sls: set[str] = self._cached_pending_agg.get(cache_key, {}).get("sls", set())

        # Guardar estado actual
        self._cached_pending_agg[cache_key] = {"tps": current_tps.copy(), "sls": current_sls.copy()}

        side     = order_info["side"]
        leverage = order_info["leverage"]
        entry    = order_info["price"]

        # ── Detectar cambios en TPs ───────────────────────────────────
        added_tps   = current_tps - prev_tps
        removed_tps = prev_tps - current_tps

        # Si se quitó 1 y se puso 1 → es una modificación (arrastre)
        if len(added_tps) == 1 and len(removed_tps) == 1:
            new_tp = added_tps.pop()
            await self.discord.send_tp_sl_update(
                symbol=symbol, side=side, leverage=leverage,
                tp_price=new_tp, pct_tp="100%",
                entry_price=entry,
            )
        else:
            for tp in added_tps:
                await self.discord.send_tp_new(
                    symbol=symbol, side=side, tp_price=tp,
                    pct_position="100%", leverage=leverage,
                    entry_price=entry,
                )
            for tp in removed_tps:
                await self.discord.send_tp_cancelled(
                    symbol=symbol, side=side, tp_price=tp, pct_position="",
                )

        # ── Detectar cambios en SLs ───────────────────────────────────
        added_sls   = current_sls - prev_sls
        removed_sls = prev_sls - current_sls

        if len(added_sls) == 1 and len(removed_sls) == 1:
            new_sl = added_sls.pop()
            await self.discord.send_tp_sl_update(
                symbol=symbol, side=side, leverage=leverage,
                sl_price=new_sl, pct_sl="100%",
                entry_price=entry,
            )
        else:
            for sl in added_sls:
                await self.discord.send_sl_new(
                    symbol=symbol, side=side, sl_price=sl,
                    pct_position="100%", leverage=leverage,
                    entry_price=entry,
                )
            for sl in removed_sls:
                await self.discord.send_sl_cancelled(
                    symbol=symbol, side=side, sl_price=sl, pct_position="",
                )

    # ── Buffer de órdenes ─────────────────────────────────────────────

    async def _buffer_order_placed(self, order_id: str, data: dict):
        async def _delayed_publish():
            await asyncio.sleep(2)
            buf = self._order_buffer.get(order_id)
            if not buf or buf.get("cancelled"):
                self._order_buffer.pop(order_id, None)
                return
            symbol = data.get("symbol", "")
            if self._is_recent_close(symbol):
                self._order_buffer.pop(order_id, None)
                return
            side     = data.get("side", "")
            price    = data.get("price", "0")
            qty      = data.get("qty", "0")
            leverage = data.get("leverage", "1")
            balance  = await self.rest.get_balance()
            await self.discord.send_order_placed(
                symbol=symbol, side=side,
                order_type=data.get("type", "LIMIT"),
                price=price, qty=qty, leverage=leverage,
                trade_side="OPEN", balance=balance,
            )
            # TP/SL se detectan via polling REST, no aquí
            self._order_buffer.pop(order_id, None)

        task = asyncio.create_task(_delayed_publish())
        self._order_buffer[order_id] = {"task": task, "data": data, "cancelled": False}

    def _cancel_buffered_order(self, order_id: str):
        buf = self._order_buffer.get(order_id)
        if buf:
            buf["cancelled"] = True
            buf["task"].cancel()
            self._order_buffer.pop(order_id, None)

    def _mark_recent_close(self, symbol: str):
        self._recent_closes[symbol] = _time.time()

    def _is_recent_close(self, symbol: str) -> bool:
        return (_time.time() - self._recent_closes.get(symbol, 0)) < 5

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

        self._active_symbols.add(symbol)

        print(f"   [Position] event={event} symbol={symbol} side={side} "
              f"qty={qty} margin={margin} realized={realized}")

        if event == "OPEN":
            self._known_positions[position_id] = {
                "symbol": symbol, "side": side, "leverage": leverage,
                "qty": qty, "margin": margin,
            }
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
            await self._refresh_tp_sl_cache(symbol)

        elif event == "UPDATE":
            prev = self._known_positions.get(position_id, {})
            prev_qty = float(prev.get("qty", "0"))
            curr_qty = float(qty)
            self._known_positions[position_id] = {
                "symbol": symbol, "side": side, "leverage": leverage,
                "qty": qty, "margin": margin,
            }

            # El polling se encarga de TP/SL, pero chequeamos aquí también
            tp_sl_changed = await self._check_position_tp_sl(symbol, side, leverage, qty)

            if curr_qty < prev_qty and prev_qty > 0:
                self._mark_recent_close(symbol)
                closed_qty = prev_qty - curr_qty
                pct_closed = f"{(closed_qty / prev_qty * 100):.0f}%"
                try:
                    closed_margin = float(prev.get("margin", "0")) * (closed_qty / prev_qty)
                except (ValueError, ZeroDivisionError):
                    closed_margin = 0
                await self.discord.send_position_partial_close(
                    symbol=symbol, side=side, leverage=leverage,
                    pct_closed=pct_closed, realized_pnl=float(realized),
                    margin=closed_margin,
                )
            elif curr_qty > prev_qty and not tp_sl_changed:
                # Promediado: se ha añadido más capital a la posición
                added_qty = curr_qty - prev_qty
                entry_price = await self.rest.get_ticker_price(symbol)
                balance = await self.rest.get_balance()
                try:
                    margin_f = float(margin)
                    pct_account = f"{(margin_f / (balance + margin_f) * 100):.1f}%"
                except (ValueError, ZeroDivisionError):
                    pct_account = "N/A"

                await self.discord.send_position_add(
                    symbol=symbol, side=side, leverage=leverage,
                    added_qty=str(added_qty), total_qty=qty,
                    entry_price=str(entry_price), margin=margin,
                    pct_account=pct_account,
                )

        elif event == "CLOSE":
            self._mark_recent_close(symbol)
            cached = self._known_positions.pop(position_id, {})
            try:
                pos_margin = float(cached.get("margin", margin))
            except (ValueError, TypeError):
                pos_margin = 0.0
            await self.discord.send_position_close(
                symbol=symbol, side=side,
                realized_pnl=float(realized),
                margin=pos_margin, leverage=leverage,
            )
            self._cached_tp_sl.pop(symbol, None)

            # Si ya no hay posiciones ni órdenes para este símbolo, dejar de pollear
            has_pos = any(i.get("symbol") == symbol for i in self._known_positions.values())
            has_ord = any(i.get("symbol") == symbol for i in self._order_tp_sl.values())
            if not has_pos and not has_ord:
                self._active_symbols.discard(symbol)

    # ══════════════════════════════════════════════════════════════════════
    #  TP/SL CHANNEL (backup, dispara chequeo inmediato)
    # ══════════════════════════════════════════════════════════════════════

    async def handle_tp_sl(self, data: dict):
        symbol = data.get("symbol", "")
        print(f"   [TP/SL Channel] event={data.get('event')} symbol={symbol}")
        if symbol:
            self._active_symbols.add(symbol)
            # Forzar chequeo inmediato
            await asyncio.sleep(1)
            side, leverage, qty = self._get_position_info(symbol)
            if side:
                await self._check_position_tp_sl(symbol, side, leverage, qty)
            await self._check_pending_orders_tp_sl_rest(symbol)

    # ══════════════════════════════════════════════════════════════════════
    #  TP/SL DE POSICIONES (vía REST)
    # ══════════════════════════════════════════════════════════════════════

    async def _check_position_tp_sl(self, symbol: str, side: str,
                                     leverage: str, position_qty: str) -> bool:
        current_orders = await self.rest.get_pending_tp_sl_orders(symbol)

        current: dict[str, dict] = {}
        for o in current_orders:
            oid = o.get("id", o.get("orderId", ""))
            if not oid:
                continue
            current[oid] = {
                "tpPrice": o.get("tpPrice", ""),
                "tpQty":   o.get("tpQty", ""),
                "slPrice": o.get("slPrice", ""),
                "slQty":   o.get("slQty", ""),
            }

        prev = self._cached_tp_sl.get(symbol, {})
        self._cached_tp_sl[symbol] = current

        had_changes = False
        pos_qty = float(position_qty) if position_qty else 0
        entry_price = await self._get_entry_price(symbol)

        # Nuevos
        for oid in set(current.keys()) - set(prev.keys()):
            o = current[oid]
            had_changes = True
            if o.get("tpPrice"):
                pct = self._calc_remaining_pct(oid, current, pos_qty, "tp")
                await self.discord.send_tp_new(
                    symbol=symbol, side=side, tp_price=o["tpPrice"],
                    pct_position=pct, leverage=leverage,
                    entry_price=entry_price,
                )
            if o.get("slPrice"):
                pct = self._calc_remaining_pct(oid, current, pos_qty, "sl")
                await self.discord.send_sl_new(
                    symbol=symbol, side=side, sl_price=o["slPrice"],
                    pct_position=pct, leverage=leverage,
                    entry_price=entry_price,
                )

        # Eliminados
        for oid in set(prev.keys()) - set(current.keys()):
            o = prev[oid]
            had_changes = True
            if o.get("tpPrice"):
                await self.discord.send_tp_cancelled(
                    symbol=symbol, side=side, tp_price=o["tpPrice"], pct_position="",
                )
            if o.get("slPrice"):
                await self.discord.send_sl_cancelled(
                    symbol=symbol, side=side, sl_price=o["slPrice"], pct_position="",
                )

        # Modificados
        for oid in set(current.keys()) & set(prev.keys()):
            c = current[oid]
            p = prev[oid]
            if c == p:
                continue
            had_changes = True

            if c.get("tpPrice") != p.get("tpPrice") or c.get("tpQty") != p.get("tpQty"):
                if c.get("tpPrice"):
                    pct = self._calc_remaining_pct(oid, current, pos_qty, "tp")
                    await self.discord.send_tp_sl_update(
                        symbol=symbol, side=side, leverage=leverage,
                        tp_price=c["tpPrice"], pct_tp=pct,
                        entry_price=entry_price,
                    )

            if c.get("slPrice") != p.get("slPrice") or c.get("slQty") != p.get("slQty"):
                if c.get("slPrice"):
                    pct = self._calc_remaining_pct(oid, current, pos_qty, "sl")
                    await self.discord.send_tp_sl_update(
                        symbol=symbol, side=side, leverage=leverage,
                        sl_price=c["slPrice"], pct_sl=pct,
                        entry_price=entry_price,
                    )

        return had_changes

    def _calc_remaining_pct(self, oid: str, all_orders: dict,
                             pos_qty: float, kind: str) -> str:
        """
        % sobre lo restante, EXCLUYENDO TPs/SLs que cubren el 100% de la posición.
        Así un TP parcial tras un TP total muestra el % correcto.
        """
        qty_key   = "tpQty" if kind == "tp" else "slQty"
        price_key = "tpPrice" if kind == "tp" else "slPrice"
        try:
            this_qty = float(all_orders[oid].get(qty_key, "0"))

            # Sumar "otros" TPs/SLs, pero EXCLUIR los que cubren el 100%
            other_qty = 0
            for k, v in all_orders.items():
                if k == oid or not v.get(price_key):
                    continue
                other_q = float(v.get(qty_key, "0"))
                # Si cubre >= 95% de la posición, es un TP/SL "total" → excluir
                if pos_qty > 0 and (other_q / pos_qty) >= 0.95:
                    continue
                other_qty += other_q

            remaining = pos_qty - other_qty
            if remaining > 0 and this_qty > 0:
                pct = (this_qty / remaining * 100)
                # Si es >= 95%, mostrar como 100%
                if pct >= 95:
                    return "100%"
                return f"{pct:.0f}%"
        except (ValueError, ZeroDivisionError):
            pass
        return "N/A"

    async def _get_entry_price(self, symbol: str) -> str:
        price = await self.rest.get_ticker_price(symbol)
        return str(price) if price > 0 else ""

    async def _refresh_tp_sl_cache(self, symbol: str):
        orders = await self.rest.get_pending_tp_sl_orders(symbol)
        current: dict[str, dict] = {}
        for o in orders:
            oid = o.get("id", o.get("orderId", ""))
            if not oid:
                continue
            current[oid] = {
                "tpPrice": o.get("tpPrice", ""),
                "tpQty":   o.get("tpQty", ""),
                "slPrice": o.get("slPrice", ""),
                "slQty":   o.get("slQty", ""),
            }
        self._cached_tp_sl[symbol] = current

    # ── helpers ───────────────────────────────────────────────────────


def _first_valid(*values) -> str:
    for v in values:
        if v and str(v).strip() and str(v).strip() != "0":
            return str(v).strip()
    return ""
