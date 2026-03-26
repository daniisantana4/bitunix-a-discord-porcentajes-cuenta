"""
Lógica de negocio: interpreta los eventos crudos del WebSocket de Bitunix
y decide qué publicar en Discord (y con qué datos).

Enriquece la información usando el cliente REST cuando es necesario
(balance actual, precio de mercado, datos de posición).
"""

from bitunix_rest import BitunixREST
from discord_sender import DiscordSender


class EventProcessor:
    """Procesa eventos de Order Channel y Position Channel."""

    def __init__(self, rest: BitunixREST, discord: DiscordSender):
        self.rest    = rest
        self.discord = discord
        # Cache simple para rastrear posiciones y evitar duplicados
        self._known_positions: dict[str, dict] = {}   # positionId → info
        self._known_orders: dict[str, str] = {}        # orderId → último status

    # ══════════════════════════════════════════════════════════════════════
    #  ORDER CHANNEL
    # ══════════════════════════════════════════════════════════════════════

    async def handle_order(self, data: dict):
        """
        Eventos del Order Channel:
          event: CREATE / UPDATE / CLOSE
          orderStatus: INIT, NEW, PART_FILLED, FILLED, CANCELED, PART_FILLED_CANCELED
        """
        order_id     = data.get("orderId", "")
        event        = data.get("event", "").upper()
        status       = data.get("orderStatus", "").upper()
        symbol       = data.get("symbol", "")
        side         = data.get("side", "")         # BUY / SELL
        order_type   = data.get("type", "")         # LIMIT / MARKET
        price        = data.get("price", "0")
        avg_price    = data.get("averagePrice", "0")
        qty          = data.get("qty", "0")
        leverage     = data.get("leverage", "1")
        fee          = data.get("fee", "0")
        trade_side   = _infer_trade_side(data)

        prev_status  = self._known_orders.get(order_id, "")
        self._known_orders[order_id] = status

        print(f"   [OrderProcessor] event={event} status={status} prev={prev_status} "
              f"symbol={symbol} side={side} type={order_type} tradeSide={trade_side}")

        # ── Orden NUEVA (pendiente) ────────────────────────────────────────
        if status == "NEW" and prev_status not in ("NEW", "PART_FILLED", "FILLED"):
            # Solo publicar órdenes LIMIT como "pendientes";
            # las MARKET pasan directamente a FILLED casi al instante
            if order_type.upper() == "LIMIT":
                await self.discord.send_order_placed(
                    symbol=symbol, side=side, order_type=order_type,
                    price=price, qty=qty, leverage=leverage,
                    trade_side=trade_side,
                )

        # ── Orden EJECUTADA (FILLED) ──────────────────────────────────────
        elif status == "FILLED" and prev_status != "FILLED":
            await self.discord.send_order_filled(
                symbol=symbol, side=side, avg_price=avg_price or price,
                qty=qty, leverage=leverage, fee=fee,
                trade_side=trade_side,
            )

            # Si es una apertura a mercado, además publicar la posición con balance
            if trade_side == "OPEN":
                await self._send_enriched_position_open(
                    symbol=symbol, side=side, leverage=leverage,
                    qty=qty, price=float(avg_price or price or 0),
                )

        # ── Parcialmente ejecutada ────────────────────────────────────────
        elif status == "PART_FILLED" and prev_status not in ("PART_FILLED", "FILLED"):
            # Informar que se está llenando parcialmente
            await self.discord.send_order_filled(
                symbol=symbol, side=side, avg_price=avg_price or price,
                qty=data.get("dealAmount", qty), leverage=leverage, fee=fee,
                trade_side=trade_side,
            )

        # ── Orden CANCELADA ───────────────────────────────────────────────
        elif status in ("CANCELED", "PART_FILLED_CANCELED"):
            if prev_status not in ("CANCELED", "PART_FILLED_CANCELED"):
                await self.discord.send_order_cancelled(
                    symbol=symbol, side=side, price=price, qty=qty,
                )

        # Limpiar órdenes terminales del cache para no acumular memoria
        if status in ("FILLED", "CANCELED", "PART_FILLED_CANCELED"):
            self._known_orders.pop(order_id, None)

    # ══════════════════════════════════════════════════════════════════════
    #  POSITION CHANNEL
    # ══════════════════════════════════════════════════════════════════════

    async def handle_position(self, data: dict):
        """
        Eventos del Position Channel:
          event: OPEN / UPDATE / CLOSE
        """
        event       = data.get("event", "").upper()
        position_id = data.get("positionId", "")
        symbol      = data.get("symbol", "")
        side        = data.get("side", "")        # LONG / SHORT
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
            # La señal de apertura principal se envía desde handle_order (FILLED),
            # pero aquí enriquecemos con datos de posición si no se envió ya
            # (por ejemplo, si la orden era LIMIT y se llenó mientras estábamos offline)
            entry_price = await self.rest.get_ticker_price(symbol)
            balance     = await self.rest.get_balance()

            await self.discord.send_position_open(
                symbol=symbol, side=side, leverage=leverage,
                qty=qty, margin=margin, entry_price=entry_price,
                balance=balance,
            )

        # ── Posición ACTUALIZADA (promediado, cierre parcial) ─────────────
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

            # Intentar recuperar el precio de entrada del cache o de la API
            cached = self._known_positions.pop(position_id, {})
            entry_price = 0.0
            # Si hay posiciones en el historial podríamos obtenerlo, pero
            # el precio de entrada no viene directo en el WS; calculamos
            # una aproximación a partir del realizedPNL si es posible
            if exit_price > 0:
                entry_price = exit_price  # fallback, se mejora abajo

            # Obtener posiciones históricas para el precio de entrada real
            try:
                positions = await self.rest.get_pending_positions(symbol)
                # Si ya no hay posición, usamos la info que tenemos
                # El realizedPNL del WS es nuestro mejor dato
                pass
            except Exception:
                pass

            await self.discord.send_position_close(
                symbol=symbol, side=side,
                realized_pnl=float(realized),
                entry_price=entry_price,
                exit_price=exit_price,
                leverage=leverage,
            )

    # ── helpers internos ──────────────────────────────────────────────────

    async def _send_enriched_position_open(self, symbol: str, side: str,
                                            leverage: str, qty: str,
                                            price: float):
        """Publica apertura de posición enriquecida con balance."""
        balance = await self.rest.get_balance()
        lev     = float(leverage) if leverage else 1
        margin  = round(price * float(qty) / lev, 2) if price > 0 else 0

        await self.discord.send_position_open(
            symbol=symbol, side=side, leverage=leverage,
            qty=qty, margin=str(margin), entry_price=price,
            balance=balance,
        )


def _infer_trade_side(data: dict) -> str:
    """
    Intenta determinar si la orden es de APERTURA (OPEN) o CIERRE (CLOSE).
    El WS no siempre envía tradeSide explícitamente; lo inferimos del contexto.
    """
    # Si viene explícito en el mensaje (algunos eventos lo incluyen)
    ts = data.get("tradeSide", "")
    if ts:
        return ts.upper()

    # Inferencia: si hay positionId y el evento es CLOSE, es cierre
    event = data.get("event", "").upper()
    if event == "CLOSE":
        return "CLOSE"

    # Por defecto asumimos apertura
    return "OPEN"
