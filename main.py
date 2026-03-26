#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║  Bitunix → Discord Signal Bot                                ║
║                                                              ║
║  Monitoriza operaciones en Bitunix en tiempo real            ║
║  y las publica automáticamente en Discord.                   ║
╚══════════════════════════════════════════════════════════════╝
"""

import asyncio
import os
import signal
import sys

from dotenv import load_dotenv

load_dotenv()

from bitunix_rest import BitunixREST
from bitunix_ws import BitunixWS
from discord_sender import DiscordSender
from event_processor import EventProcessor


async def main():
    # ── Validar configuración ─────────────────────────────────────────────
    required = ["BITUNIX_API_KEY", "BITUNIX_SECRET_KEY", "DISCORD_WEBHOOK_URL"]
    missing  = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"❌ Variables de entorno faltantes: {', '.join(missing)}")
        print("   Copia .env.example a .env y rellénalo.")
        sys.exit(1)

    # ── Inicializar componentes ───────────────────────────────────────────
    rest      = BitunixREST()
    discord   = DiscordSender()
    processor = EventProcessor(rest=rest, discord=discord)

    ws = BitunixWS(
        on_order_event=processor.handle_order,
        on_position_event=processor.handle_position,
    )

    # ── Mensaje de inicio ─────────────────────────────────────────────────
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║        🤖 Bitunix → Discord Signal Bot                     ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()

    balance = await rest.get_balance()
    print(f"💰 Balance USDT disponible: {balance:.2f}")
    print()

    await discord.send_bot_status(
        f"🟢 Bot iniciado correctamente\n"
        f"💰 Balance: **{balance:.2f} USDT**\n"
        f"📡 Escuchando operaciones en tiempo real…"
    )

    # ── Manejo de señales para cierre limpio ──────────────────────────────
    loop = asyncio.get_running_loop()

    def _shutdown():
        print("\n🛑 Apagando bot…")
        asyncio.ensure_future(ws.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            # Windows no soporta add_signal_handler
            pass

    # ── Ejecutar WebSocket ────────────────────────────────────────────────
    try:
        await ws.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        await discord.send_bot_status("🔴 Bot desconectado")
        await rest.close()
        await discord.close()
        print("👋 Bot detenido.")


if __name__ == "__main__":
    asyncio.run(main())
