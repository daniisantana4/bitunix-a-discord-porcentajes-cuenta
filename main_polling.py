"""
╔══════════════════════════════════════════════════════════════╗
║  ALTERNATIVA: Monitor por Polling (sin WebSocket)            ║
║                                                              ║
║  Si el WebSocket no es estable o tu plan de API no lo        ║
║  soporta, este script hace lo mismo consultando la API       ║
║  REST cada pocos segundos.                                   ║
║                                                              ║
║  Uso:  python main_polling.py                                ║
╚══════════════════════════════════════════════════════════════╝
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from bitunix_rest import BitunixREST
from discord_sender import DiscordSender

# Intervalo de polling en segundos
POLL_INTERVAL = 3


class PollingMonitor:
    """Monitoriza posiciones y órdenes mediante polling REST."""

    def __init__(self):
        self.rest    = BitunixREST()
        self.discord = DiscordSender()

        # Estado anterior para detectar cambios
        self._prev_positions: dict[str, dict] = {}   # positionId → datos
        self._prev_orders: dict[str, str] = {}        # orderId → status
        self._first_run = True

    async def run(self):
        """Bucle principal de polling."""
        print("📡 Monitor por polling iniciado")
        print(f"   Intervalo: cada {POLL_INTERVAL}s")

        balance = await self.rest.get_balance()
        print(f"💰 Balance: {balance:.2f} USDT\n")

        await self.discord.send_bot_status(
            f"🟢 Bot (polling) iniciado\n"
            f"💰 Balance: **{balance:.2f} USDT**\n"
            f"🔄 Consultando cada {POLL_INTERVAL}s…"
        )

        while True:
            try:
                await self._poll_positions()
                await self._poll_orders()
                self._first_run = False
            except Exception as e:
                print(f"❌ Error en ciclo de polling: {e}")

            await asyncio.sleep(POLL_INTERVAL)

    # ── Polling de posiciones ─────────────────────────────────────────────
    async def _poll_positions(self):
        positions = await self.rest.get_pending_positions()
        current_ids = set()

        for pos in positions:
            pid  = pos.get("positionId", "")
            current_ids.add(pid)

            if pid not in self._prev_positions:
                # Posición NUEVA detectada
                if not self._first_run:
                    symbol   = pos.get("symbol", "")
                    side     = pos.get("side", "")
                    leverage = pos.get("leverage", "1")
                    qty      = pos.get("qty", "0")
                    margin   = pos.get("margin", "0")
                    price    = await self.rest.get_ticker_price(symbol)
                    balance  = await self.rest.get_balance()

                    print(f"🆕 Nueva posición: {symbol} {side}")
                    await self.discord.send_position_open(
                        symbol=symbol, side=side, leverage=leverage,
                        qty=qty, margin=margin, entry_price=price,
                        balance=balance,
                    )

            else:
                # Posición EXISTENTE — comprobar si la qty cambió (promediado / cierre parcial)
                prev_qty = self._prev_positions[pid].get("qty", "0")
                curr_qty = pos.get("qty", "0")
                if prev_qty != curr_qty and not self._first_run:
                    print(f"🔄 Posición actualizada: {pos.get('symbol')} qty {prev_qty} → {curr_qty}")
                    await self.discord.send_position_update(
                        symbol=pos.get("symbol", ""),
                        side=pos.get("side", ""),
                        qty=curr_qty,
                        unrealized_pnl=pos.get("unrealizedPNL", "0"),
                        margin=pos.get("margin", "0"),
                        leverage=pos.get("leverage", "1"),
                    )

            self._prev_positions[pid] = pos

        # Detectar posiciones CERRADAS (estaban antes pero ya no están)
        closed_ids = set(self._prev_positions.keys()) - current_ids
        for pid in closed_ids:
            if not self._first_run:
                prev  = self._prev_positions[pid]
                symbol = prev.get("symbol", "")
                side   = prev.get("side", "")
                price  = await self.rest.get_ticker_price(symbol)
                realized = float(prev.get("realizedPNL", "0"))

                print(f"🏁 Posición cerrada: {symbol} PnL={realized}")
                await self.discord.send_position_close(
                    symbol=symbol, side=side,
                    realized_pnl=realized,
                    entry_price=0,  # no disponible directamente
                    exit_price=price,
                    leverage=prev.get("leverage", "1"),
                )
            del self._prev_positions[pid]

    # ── Polling de órdenes ────────────────────────────────────────────────
    async def _poll_orders(self):
        orders = await self.rest.get_pending_orders()
        current_ids = set()

        for order in orders:
            oid    = order.get("orderId", "")
            status = order.get("orderStatus", "")
            current_ids.add(oid)

            prev_status = self._prev_orders.get(oid, "")

            if oid not in self._prev_orders and not self._first_run:
                # Orden NUEVA
                if order.get("orderType", "").upper() == "LIMIT":
                    print(f"📝 Nueva orden: {order.get('symbol')} {order.get('side')} @ {order.get('price')}")
                    await self.discord.send_order_placed(
                        symbol=order.get("symbol", ""),
                        side=order.get("side", ""),
                        order_type=order.get("orderType", ""),
                        price=order.get("price", "0"),
                        qty=order.get("qty", "0"),
                        leverage=order.get("leverage", "1"),
                        trade_side=order.get("tradeSide", "OPEN"),
                    )

            self._prev_orders[oid] = status

        # Órdenes que desaparecieron = ejecutadas o canceladas
        disappeared = set(self._prev_orders.keys()) - current_ids
        for oid in disappeared:
            del self._prev_orders[oid]
            # No podemos distinguir fácilmente ejecutada vs cancelada solo con polling,
            # pero las posiciones nuevas/cerradas se detectan en _poll_positions


async def main():
    required = ["BITUNIX_API_KEY", "BITUNIX_SECRET_KEY", "DISCORD_WEBHOOK_URL"]
    missing  = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"❌ Variables de entorno faltantes: {', '.join(missing)}")
        sys.exit(1)

    print("╔══════════════════════════════════════════════════════════════╗")
    print("║    🤖 Bitunix → Discord Signal Bot (Polling Mode)          ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()

    monitor = PollingMonitor()
    try:
        await monitor.run()
    except KeyboardInterrupt:
        pass
    finally:
        await monitor.discord.send_bot_status("🔴 Bot desconectado")
        await monitor.rest.close()
        await monitor.discord.close()
        print("👋 Bot detenido.")


if __name__ == "__main__":
    asyncio.run(main())
