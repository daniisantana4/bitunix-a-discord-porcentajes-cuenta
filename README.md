# Bitunix → Discord Bot Señales

Bot que monitoriza en tiempo real las operaciones realizadas en Bitunix (futuros)
y publica automáticamente las señales en el canal de Discord con toda la información
calculada (par, dirección, apalancamiento, precio de entrada, PnL, etc.).

## Arquitectura

```
Bitunix (WebSocket privado)
    │
    ├── Order Channel   ─── orden creada / ejecutada / cancelada
    ├── Position Channel ─── posición abierta / actualizada / cerrada
    └── Balance Channel  ─── cambios de balance
    │
    ▼
  monitor_ws.py  (escucha en tiempo real)
    │
    ▼
  discord_sender.py  (formatea y envía embeds)
    │
    ▼
  Discord Webhook → Canal de señales
```

## Requisitos

```bash
pip install websockets aiohttp python-dotenv requests
```

## Configuración

Copia `.env.example` a `.env` y rellena:

```bash
cp .env.example .env
```

| Variable | Descripción |
|---|---|
| `BITUNIX_API_KEY` | API Key de Bitunix |
| `BITUNIX_SECRET_KEY` | Secret Key de Bitunix |
| `DISCORD_WEBHOOK_URL` | URL del webhook de Discord |
| `YOUTUBER_NAME` | Nombre que aparece en los embeds (opcional) |

### Crear el Webhook de Discord

1. Ve a tu servidor de Discord → Canal donde quieres las señales
2. Editar canal → Integraciones → Webhooks → Nuevo webhook
3. Copia la URL del webhook y pégala en `.env`

## Ejecución

```bash
python main.py
```

## Archivos

| Archivo | Función |
|---|---|
| `main.py` | Punto de entrada, orquesta todo |
| `bitunix_ws.py` | Conexión WebSocket autenticada a Bitunix |
| `bitunix_rest.py` | Cliente REST para consultas complementarias (balance, ticker…) |
| `discord_sender.py` | Formateo y envío de embeds a Discord vía webhook |
| `event_processor.py` | Lógica de negocio: interpreta eventos y decide qué publicar |
| `.env.example` | Plantilla de variables de entorno |
