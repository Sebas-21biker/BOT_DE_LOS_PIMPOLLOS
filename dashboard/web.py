"""Servidor del dashboard (Mini App de Telegram).

La página se abre dentro de Telegram, que le entrega `initData` firmado con el token del bot.
Cada llamada a la API valida esa firma y la lista blanca antes de devolver datos.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

from core import config, db, finanzas, reportes

log = logging.getLogger(__name__)

ESTATICOS = Path(__file__).resolve().parent / "static"
MAX_EDAD_INIT_DATA = 24 * 3600  # segundos


def validar_init_data(init_data: str, token: str, max_edad: int = MAX_EDAD_INIT_DATA) -> dict | None:
    """Devuelve el usuario de Telegram si la firma de `initData` es válida, si no None.

    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    try:
        datos = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    firma = datos.pop("hash", None)
    if not firma:
        return None
    a_verificar = "\n".join(f"{k}={v}" for k, v in sorted(datos.items()))
    clave = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    esperada = hmac.new(clave, a_verificar.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(esperada, firma):
        return None
    if time.time() - int(datos.get("auth_date", 0)) > max_edad:
        return None
    try:
        return json.loads(datos["user"])
    except (KeyError, ValueError):
        return None


def _a_json(valor):
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, date):
        return valor.isoformat()
    raise TypeError(f"No serializable: {type(valor)}")


@web.middleware
async def _autenticar(request: web.Request, handler):
    if not request.path.startswith("/api/"):
        return await handler(request)
    encabezado = request.headers.get("Authorization", "")
    usuario_tg = validar_init_data(encabezado.removeprefix("tma "), config.telegram_bot_token())
    if usuario_tg is None:
        raise web.HTTPUnauthorized(text="No autorizado")
    usuario = await asyncio.to_thread(finanzas.obtener_usuario, usuario_tg["id"])
    if usuario is None:
        raise web.HTTPForbidden(text="Primero usa /start en el bot")
    request["usuario"] = usuario
    return await handler(request)


async def _index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(ESTATICOS / "index.html", headers={"Cache-Control": "no-cache"})


async def _api_dashboard(request: web.Request) -> web.Response:
    q = request.query
    try:
        mes = date.fromisoformat(q["mes"] + "-01") if q.get("mes") else finanzas.hoy()
        persona = int(q["persona"]) if q.get("persona", "todos") != "todos" else None
    except ValueError:
        raise web.HTTPBadRequest(text="Parámetros inválidos")
    pruebas = q.get("pruebas") == "1"

    datos = await asyncio.to_thread(reportes.datos_dashboard, request["usuario"]["id"], mes, persona, pruebas)
    datos["yo"] = request["usuario"]["id"]
    return web.json_response(datos, dumps=lambda d: json.dumps(d, default=_a_json, ensure_ascii=False))


VENTANA_DUPLICADOS = 180  # segundos: el mismo aviso repetido en este lapso se ignora


async def _pago_automatico(request: web.Request) -> web.Response:
    """POST /auto/{token}: el celular avisa un pago. Cuerpo JSON {"texto": "..."} (o {"monto", "comercio"})
    o texto plano. Se responde al tiro y el bot le pregunta al usuario si lo anota."""
    usuario = await asyncio.to_thread(db.usuario_por_token, request.match_info["token"])
    if usuario is None:
        raise web.HTTPNotFound(text="Token inválido")
    crudo = await request.text()
    origen, id_externo = "atajo", None
    try:
        cuerpo = json.loads(crudo)
        texto = cuerpo.get("texto") or " ".join(str(cuerpo.get(k, "")) for k in ("monto", "comercio", "detalle"))
        origen = str(cuerpo.get("origen") or "atajo")
        id_externo = cuerpo.get("id")  # el script de Gmail manda el id del correo
    except (ValueError, AttributeError):
        texto = crudo
    texto = " ".join(str(texto).split())[:1500 if origen == "correo" else 500]
    if not texto:
        raise web.HTTPBadRequest(text="Falta el texto del pago")

    recientes: dict = request.app["recientes"]
    ahora = time.time()
    for clave, momento in list(recientes.items()):
        if ahora - momento > VENTANA_DUPLICADOS:
            del recientes[clave]
    clave = (usuario["id"], id_externo or texto.lower())
    if clave in recientes:
        return web.json_response({"ok": True, "duplicado": True})
    recientes[clave] = ahora

    asyncio.create_task(request.app["al_recibir_pago"](usuario, texto, origen))
    return web.json_response({"ok": True})


def crear_app(al_recibir_pago=None) -> web.Application:
    app = web.Application(middlewares=[_autenticar])
    app["al_recibir_pago"] = al_recibir_pago
    app["recientes"] = {}
    app.router.add_get("/", _index)
    app.router.add_get("/api/dashboard", _api_dashboard)
    if al_recibir_pago is not None:
        app.router.add_post("/auto/{token}", _pago_automatico)
    return app


async def iniciar(puerto: int, al_recibir_pago=None) -> web.AppRunner:
    runner = web.AppRunner(crear_app(al_recibir_pago), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", puerto).start()
    log.info("Dashboard escuchando en el puerto %s", puerto)
    return runner
