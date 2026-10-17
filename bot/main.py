"""Punto de entrada del bot. En local corre en modo polling.

Uso: python -m bot.main
"""

import asyncio
import logging
import re
import secrets
from datetime import date
from decimal import Decimal
from html import escape

from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonCommands,
                      MenuButtonWebApp, Update, WebAppInfo)
from telegram.constants import ChatAction
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler,
                          filters)

from bot import interprete
from core import config, db, finanzas, parser
from core.finanzas import formatear_clp
from dashboard import web as dashboard_web

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]

# "test" o "#prueba" en el mensaje -> movimiento de prueba (no cuenta en saldos ni resúmenes).
_RE_PRUEBA = re.compile(r"(?<!\w)(#?test|#prueba)(?!\w)", re.IGNORECASE)


def _es_prueba(texto: str) -> bool:
    return bool(_RE_PRUEBA.search(texto or ""))


def _sin_marca_prueba(texto: str) -> str:
    return re.sub(r"\s{2,}", " ", _RE_PRUEBA.sub("", texto)).strip(" ,.:-")

AYUDA = (
    "✍️ <b>Escríbeme como en tus notas</b>\n"
    "   <i>hoy gasté 3500 en uber</i>\n"
    "   <i>salimos a comer, 50 lucas a medias</i>\n"
    "   <i>juan me transfirió 30000 de una venta</i>\n"
    "Yo te muestro lo que caché y tú confirmas o corriges 👌\n\n"
    "📌 <b>Comandos</b>\n"
    "/saldo — quién le debe a quién\n"
    "/resumen — gastos del mes\n"
    "/gasto <code>monto descripción</code> — registro rápido sin IA\n"
    "/ingreso <code>monto descripción</code> — registro rápido sin IA\n\n"
    "🧪 Si escribes <b>test</b> en el mensaje, lo anoto como prueba: no cuenta en saldos ni resúmenes. "
    "Los borras todos con /borrar_pruebas"
)


# ---------- utilidades ----------

async def _usuario_o_aviso(update: Update) -> dict | None:
    usuario = finanzas.obtener_usuario(update.effective_user.id)
    if usuario is None:
        await update.effective_message.reply_text("¡Wena! Primero tira un /start para registrarte 🙌")
    return usuario


def _formatear_fecha(fecha: date) -> str:
    hoy = finanzas.hoy()
    if fecha == hoy:
        return "hoy"
    if (hoy - fecha).days == 1:
        return "ayer"
    return f"{fecha.day} de {MESES[fecha.month - 1]}"


def _describir(mov: dict, usuario: dict, pareja: dict | None) -> str:
    """Texto de un movimiento para mostrar al usuario (HTML)."""
    categoria = finanzas.categoria_por_nombre(mov["categoria"], mov["tipo"])
    signo = "💸" if mov["tipo"] == "gasto" else "💰"
    lineas = [
        f"{signo} <b>{'Gasto' if mov['tipo'] == 'gasto' else 'Ingreso'} de {formatear_clp(mov['monto'])}</b>"
        + (f" {mov['moneda']}" if mov["moneda"] != "CLP" else ""),
        f"{categoria['emoji']} {escape(categoria['nombre'])} · {_formatear_fecha(mov['fecha'])}",
    ]
    if mov["descripcion"]:
        lineas.append(f"📝 {escape(mov['descripcion'])}")
    pct = mov["porcentaje_pagador"]
    if pct is not None:
        nombre_pareja = escape(pareja["nombre"]) if pareja else "tu pareja"
        pct_pareja = Decimal(100) - pct
        lineas.append(f"👥 Compartido: tú {pct:g}% · {nombre_pareja} {pct_pareja:g}% "
                      f"({nombre_pareja} te debe {formatear_clp(mov['monto'] * pct_pareja / 100)})")
    elif mov["tipo"] == "gasto":
        lineas.append(f"👤 Individual, pagado por {escape(usuario['nombre'])}")
    else:
        lineas.append(f"👤 Ingreso de {escape(usuario['nombre'])}")
    if mov.get("es_prueba"):
        lineas.append("🧪 <b>PRUEBA</b>: no cuenta en saldos ni resúmenes")
    return "\n".join(lineas)


def _guardar(mov: dict, usuario: dict, pareja: dict | None, mensaje_original: str) -> int:
    repartos = None
    if mov["porcentaje_pagador"] is not None:
        repartos = {usuario["id"]: mov["porcentaje_pagador"],
                    pareja["id"]: Decimal(100) - mov["porcentaje_pagador"]}
    categoria = finanzas.categoria_por_nombre(mov["categoria"], mov["tipo"])
    return finanzas.registrar_movimiento(
        tipo=mov["tipo"],
        monto=mov["monto"],
        moneda=mov["moneda"],
        categoria_id=categoria["id"],
        pagado_por=usuario["id"],
        descripcion=mov["descripcion"],
        mensaje_original=mensaje_original,
        repartos=repartos,
        fecha=mov["fecha"],
        es_prueba=mov.get("es_prueba", False),
    )


def _problema(movimientos: list[dict], pareja: dict | None) -> str | None:
    """Motivo por el que no se pueden guardar estos movimientos, o None si están OK."""
    if any(m["moneda"] != "CLP" for m in movimientos):
        return "Pucha, por ahora solo anoto en pesos chilenos. Dólares y pesos mexicanos vienen pronto ✈️"
    if any(m["porcentaje_pagador"] is not None for m in movimientos) and pareja is None:
        return "Tu pareja todavía no se registra (tiene que tirar un /start), así que aún no puedo dividir gastos 🤷"
    return None


def _boton_deshacer(ids: list[int]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        "↩️ Deshacer", callback_data="deshacer:" + ",".join(map(str, ids)))]])


# ---------- comandos ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usuario_tg = update.effective_user
    usuario = db.registrar_usuario(usuario_tg.id, usuario_tg.first_name)
    await update.message.reply_text(f"¡Wena {escape(usuario['nombre'])}! Ya quedaste registrado 💸\n\n{AYUDA}",
                                    parse_mode="HTML")


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(AYUDA, parse_mode="HTML")


async def _registro_rapido(update: Update, context: ContextTypes.DEFAULT_TYPE, tipo: str) -> None:
    """/gasto y /ingreso: registro directo sin IA (el usuario ya escribió los datos explícitos)."""
    usuario = await _usuario_o_aviso(update)
    if usuario is None:
        return
    es_prueba = _es_prueba(update.message.text)
    args = [a for a in context.args if not _RE_PRUEBA.fullmatch(a)]
    try:
        datos = parser.interpretar(args, finanzas.nombres_categorias(tipo),
                                   permitir_reparto=(tipo == "gasto"))
    except parser.ErrorFormato as e:
        await update.message.reply_text(f"{e}\nEjemplo: /{tipo} 15000 super lider")
        return

    mov = {
        "tipo": tipo,
        "monto": datos.monto,
        "moneda": datos.moneda,
        "categoria": datos.categoria or ("Otros" if tipo == "gasto" else "Otros ingresos"),
        "descripcion": datos.descripcion,
        "fecha": finanzas.hoy(),
        "porcentaje_pagador": datos.porcentaje_pagador,
        "es_prueba": es_prueba,
    }
    pareja = finanzas.otro_usuario(usuario["id"])
    if problema := _problema([mov], pareja):
        await update.message.reply_text(problema)
        return

    movimiento_id = _guardar(mov, usuario, pareja, update.message.text)
    await update.message.reply_text("✅ ¡Listo, anotado!\n\n" + _describir(mov, usuario, pareja),
                                    parse_mode="HTML", reply_markup=_boton_deshacer([movimiento_id]))


async def gasto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _registro_rapido(update, context, "gasto")


async def ingreso(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _registro_rapido(update, context, "ingreso")


async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _usuario_o_aviso(update) is None:
        return
    filas = finanzas.balances()
    if len(filas) < 2:
        await update.message.reply_text("Falta que tu pareja se registre con /start 🙏")
        return
    acreedor = max(filas, key=lambda f: f["balance"])
    deudor = min(filas, key=lambda f: f["balance"])
    if round(acreedor["balance"]) == 0:
        await update.message.reply_text("⚖️ ¡Están a mano! Nadie le debe a nadie 🙌")
        return
    await update.message.reply_text(
        f"⚖️ {deudor['nombre']} le debe {formatear_clp(acreedor['balance'])} a {acreedor['nombre']}"
    )


async def resumen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usuario = await _usuario_o_aviso(update)
    if usuario is None:
        return
    datos = finanzas.resumen_mes(usuario["id"])
    mes = datos["mes"]
    lineas = [f"📊 <b>Resumen de {MESES[mes.month - 1]} {mes.year}</b>", ""]
    if datos["gastos"]:
        total = sum(g["total"] for g in datos["gastos"])
        lineas.append(f"<b>Gastos: {formatear_clp(total)}</b>")
        lineas += [f"{g['emoji']} {escape(g['categoria'])}: {formatear_clp(g['total'])}" for g in datos["gastos"]]
        lineas.append("")
        lineas += [f"👤 {escape(p['nombre'])} pagó {formatear_clp(p['total'])}" for p in datos["por_persona"]]
    else:
        lineas.append("Sin gastos este mes todavía.")
    lineas += ["", f"💼 Tus ingresos del mes: {formatear_clp(datos['mis_ingresos'])}"]
    await update.message.reply_text("\n".join(lineas), parse_mode="HTML")


async def borrar_pruebas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _usuario_o_aviso(update) is None:
        return
    n = finanzas.borrar_pruebas()
    if n:
        await update.message.reply_text(f"🧹 Listo, borré {n} movimiento{'s' if n != 1 else ''} de prueba.")
    else:
        await update.message.reply_text("No había movimientos de prueba 👌")


# ---------- lenguaje natural ----------

async def texto_libre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usuario = await _usuario_o_aviso(update)
    if usuario is None:
        return
    texto = update.message.text
    pendientes: dict = context.user_data.setdefault("pendientes", {})

    # ¿Es la corrección de algo que el bot mostró antes?
    anterior = None
    es_prueba = _es_prueba(texto)
    pid_corrigiendo = context.user_data.pop("corrigiendo", None)
    if pid_corrigiendo and pid_corrigiendo in pendientes:
        previo = pendientes.pop(pid_corrigiendo)
        anterior = (previo["mensaje"], previo["crudo"])
        es_prueba = es_prueba or previo["es_prueba"]

    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        datos = await interprete.interpretar(
            _sin_marca_prueba(texto), finanzas.hoy(),
            finanzas.nombres_categorias("gasto"), finanzas.nombres_categorias("ingreso"),
            anterior=(_sin_marca_prueba(anterior[0]), anterior[1]) if anterior else None,
        )
    except interprete.ErrorInterprete as e:
        await update.message.reply_text(str(e))
        return

    if datos["intencion"] == "consulta":
        await update.message.reply_text(
            "Pronto voy a poder cachar ese tipo de preguntas 🤓. Por mientras, tira /saldo o /resumen.")
        return
    if datos["intencion"] != "registrar":
        await update.message.reply_text(escape(datos["respuesta"]) or "Mmm, no pillé ningún gasto o ingreso ahí 🤔\n"
                                        "Prueba algo como: <i>hoy gasté 3500 en uber</i>", parse_mode="HTML")
        return

    pareja = finanzas.otro_usuario(usuario["id"])
    if problema := _problema(datos["movimientos"], pareja):
        await update.message.reply_text(problema)
        return

    mensaje_original = f"{anterior[0]} | corrección: {texto}" if anterior else texto
    pid = secrets.token_hex(4)
    for m in datos["movimientos"]:
        m["es_prueba"] = es_prueba
    pendientes[pid] = {"movimientos": datos["movimientos"], "mensaje": mensaje_original, "crudo": datos["crudo"],
                       "es_prueba": es_prueba}

    cuerpo = "\n\n".join(_describir(m, usuario, pareja) for m in datos["movimientos"])
    botones = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirmar", callback_data=f"conf:{pid}"),
        InlineKeyboardButton("✏️ Corregir", callback_data=f"corr:{pid}"),
        InlineKeyboardButton("❌ Cancelar", callback_data=f"canc:{pid}"),
    ]])
    await update.message.reply_text(f"¿Lo anoto así? 👇\n\n{cuerpo}", parse_mode="HTML", reply_markup=botones)


async def botones_pendiente(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    accion, pid = query.data.split(":")
    pendientes: dict = context.user_data.get("pendientes", {})
    pendiente = pendientes.get(pid)
    if pendiente is None:
        await query.answer("Esto ya caducó, mándamelo de nuevo 🙏", show_alert=True)
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if accion == "canc":
        pendientes.pop(pid)
        await query.answer("Cancelado")
        await query.edit_message_text("❌ Ya, lo dejo pasar. No anoté nada 👌")
        return

    if accion == "corr":
        context.user_data["corrigiendo"] = pid
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("✏️ Dale, ¿qué cambio? Escríbelo normal, tipo: "
                                       "<i>eran 4500</i>, <i>fue ayer</i> o <i>era a medias</i>.",
                                       parse_mode="HTML")
        return

    # Confirmar
    usuario = finanzas.obtener_usuario(query.from_user.id)
    pareja = finanzas.otro_usuario(usuario["id"])
    if problema := _problema(pendiente["movimientos"], pareja):
        await query.answer(problema, show_alert=True)
        return
    pendientes.pop(pid)
    ids = [_guardar(m, usuario, pareja, pendiente["mensaje"]) for m in pendiente["movimientos"]]
    await query.answer("Guardado")
    cuerpo = "\n\n".join(_describir(m, usuario, pareja) for m in pendiente["movimientos"])
    await query.edit_message_text(f"✅ ¡Listo, anotado!\n\n{cuerpo}", parse_mode="HTML", reply_markup=_boton_deshacer(ids))


async def deshacer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    usuario = finanzas.obtener_usuario(query.from_user.id)
    ids = [int(x) for x in query.data.split(":")[1].split(",")]
    borrados = sum(finanzas.borrar_movimiento(i, usuario["id"]) for i in ids) if usuario else 0
    if borrados:
        await query.answer("Borrado")
        await query.edit_message_text(f"🗑️ Borrado, como si nada\n\n{query.message.text}", reply_markup=None)
    else:
        await query.answer("No lo pude borrar (ya no existe o no lo anotaste tú)", show_alert=True)


# ---------- dashboard ----------

async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = config.dashboard_url()
    if not url:
        await update.message.reply_text("El dashboard todavía no tiene dirección pública 🤷")
        return
    boton = InlineKeyboardMarkup([[InlineKeyboardButton("📊 Abrir dashboard", web_app=WebAppInfo(url))]])
    await update.message.reply_text("Ahí está, ábrelo al tiro 👇", reply_markup=boton)


# ---------- arranque ----------

async def _configurar_menu(app: Application) -> None:
    comandos = [
        BotCommand("saldo", "Quién le debe a quién"),
        BotCommand("resumen", "Resumen del mes"),
        BotCommand("gasto", "Registro rápido de gasto"),
        BotCommand("ingreso", "Registro rápido de ingreso"),
        BotCommand("borrar_pruebas", "Borrar movimientos de prueba"),
        BotCommand("ayuda", "Cómo usar el bot"),
    ]
    url = config.dashboard_url()
    if url:
        comandos.insert(0, BotCommand("dashboard", "Ver gráficos"))
        # Botón fijo al lado del cuadro de texto que abre la Mini App.
        await app.bot.set_chat_menu_button(menu_button=MenuButtonWebApp("📊 Dashboard", WebAppInfo(url)))
    else:
        await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    await app.bot.set_my_commands(comandos)


def _crear_app() -> Application:
    autorizados = filters.User(user_id=config.allowed_telegram_ids())

    app = Application.builder().token(config.telegram_bot_token()).build()
    app.add_handler(CommandHandler("start", start, filters=autorizados))
    app.add_handler(CommandHandler("ayuda", ayuda, filters=autorizados))
    app.add_handler(CommandHandler("gasto", gasto, filters=autorizados))
    app.add_handler(CommandHandler("ingreso", ingreso, filters=autorizados))
    app.add_handler(CommandHandler("saldo", saldo, filters=autorizados))
    app.add_handler(CommandHandler("resumen", resumen, filters=autorizados))
    app.add_handler(CommandHandler("borrar_pruebas", borrar_pruebas, filters=autorizados))
    app.add_handler(CommandHandler("dashboard", dashboard, filters=autorizados))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & autorizados, texto_libre))
    app.add_handler(CallbackQueryHandler(botones_pendiente, pattern=r"^(conf|corr|canc):[0-9a-f]+$"))
    app.add_handler(CallbackQueryHandler(deshacer, pattern=r"^deshacer:[\d,]+$"))
    return app


async def _correr() -> None:
    """Bot (polling) y servidor del dashboard en el mismo proceso."""
    app = _crear_app()
    async with app:
        await _configurar_menu(app)
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        runner = await dashboard_web.iniciar(config.puerto_web())
        log.info("Bot iniciado en modo polling (modelo: %s, dashboard: %s)",
                 config.claude_model(), config.dashboard_url() or "sin URL pública")
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
            await app.updater.stop()
            await app.stop()


def main() -> None:
    try:
        asyncio.run(_correr())
    except KeyboardInterrupt:
        log.info("Bot detenido")


if __name__ == "__main__":
    main()
