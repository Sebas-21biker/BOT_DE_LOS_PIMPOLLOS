"""Punto de entrada del bot: arma los handlers y corre bot + dashboard + recordatorios.

Uso: python -m bot.main
"""

import asyncio
import logging
from html import escape

from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonCommands,
                      MenuButtonWebApp, Update, WebAppInfo)
from telegram.constants import ChatAction
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler,
                          filters)

from bot import comun, hogar, interprete, plata
from core import config, db, finanzas, metas
from dashboard import web as dashboard_web

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

AYUDA = (
    "✍️ <b>Escríbeme normal, como en tus notas:</b>\n\n"
    "💸 <i>hoy gasté 3500 en uber</i> · <i>cena 40 lucas a medias</i>\n"
    "🤝 <i>le transferí 25 lucas a la Cami</i> (para saldar)\n"
    "🎯 <i>queremos juntar 2 millones para México en julio</i> · <i>aparté 50 lucas para México</i>\n"
    "🛒 <i>agrega leche y huevos a la lista</i>\n"
    "⏰ <i>recuérdame pagar la luz el 5 a las 10</i> · <i>el 3 de marzo es el cumple de la Cami</i>\n"
    "📍 <i>anota el restaurante Liguria para ir</i> · <i>¿qué hacemos hoy?</i>\n"
    "📌 <i>la clave del wifi es pimpollo123</i> · <i>¿cuál era la clave del wifi?</i>\n\n"
    "📋 <b>Comandos</b>\n"
    "/saldo · /resumen · /metas · /lista · /recordatorios · /panoramas · /notas · /dashboard\n"
    "/gasto, /ingreso y /pague para anotar rápido sin IA\n\n"
    "🧪 Si escribes <b>test</b> en un gasto o pago, lo anoto como prueba (no cuenta). "
    "Los borras con /borrar_pruebas"
)


# ---------- comandos generales ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usuario_tg = update.effective_user
    usuario = db.registrar_usuario(usuario_tg.id, usuario_tg.first_name)
    await update.message.reply_text(f"¡Wena {escape(usuario['nombre'])}! Ya quedaste registrado 🐣\n\n{AYUDA}",
                                    parse_mode="HTML")


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(AYUDA, parse_mode="HTML")


async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = config.dashboard_url()
    if not url:
        await update.message.reply_text("El dashboard todavía no tiene dirección pública 🤷")
        return
    if comun.es_grupo(update):
        # En grupos Telegram no permite botones web_app: se usa el enlace directo a la Mini App principal.
        boton = InlineKeyboardButton("📊 Abrir dashboard", url=f"https://t.me/{context.bot.username}?startapp")
    else:
        boton = InlineKeyboardButton("📊 Abrir dashboard", web_app=WebAppInfo(url))
    await update.message.reply_text("Ahí está, ábrelo al tiro 👇", reply_markup=InlineKeyboardMarkup([[boton]]))


# ---------- texto libre ----------

async def texto_libre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usuario = await comun.usuario_o_aviso(update)
    if usuario is None:
        return
    texto = update.message.text
    grupo = comun.es_grupo(update)

    # ¿Responde a una pregunta del bot (monto de un aporte, total de la compra...)?
    contexto = None
    if esperando := context.user_data.pop("esperando", None):
        resultado = await hogar.manejar_esperando(update, context, usuario, esperando)
        if resultado is None:
            return
        contexto = resultado["contexto"]

    # ¿Es la corrección de una propuesta anterior?
    prueba = comun.es_prueba(texto)
    anterior = None
    if previo := plata.tomar_para_corregir(context):
        anterior = (comun.sin_marca_prueba(previo["mensaje"]), previo["crudo"])
        prueba = prueba or previo["es_prueba"]

    if not grupo:
        await update.message.chat.send_action(ChatAction.TYPING)
    try:
        datos = await interprete.interpretar(
            comun.sin_marca_prueba(texto), finanzas.ahora(),
            finanzas.nombres_categorias("gasto"), finanzas.nombres_categorias("ingreso"),
            [m["nombre"] for m in metas.listar()],
            anterior=anterior, contexto=contexto,
        )
    except interprete.ErrorInterprete as e:
        await update.message.reply_text(str(e))
        return

    intencion = datos["intencion"]
    if intencion in ("movimientos", "pago_pareja"):
        mensaje_original = f"{previo['mensaje']} | corrección: {texto}" if anterior else texto
        await plata.proponer(update, context, usuario, datos, mensaje_original, prueba)
    elif await hogar.manejar_intencion(update, context, usuario, datos):
        pass
    elif grupo:
        return  # en el grupo no se responde a la conversación normal
    elif intencion == "consulta":
        await update.message.reply_text(
            "Pronto voy a poder cachar ese tipo de preguntas 🤓. Por mientras, tira /saldo, /resumen o /dashboard.")
    else:
        await update.message.reply_text(escape(datos["respuesta"]) or "Mmm, no caché qué necesitas 🤔 Mira /ayuda",
                                        parse_mode="HTML")


# ---------- arranque ----------

COMANDOS = [
    BotCommand("saldo", "Quién le debe a quién"),
    BotCommand("resumen", "Resumen del mes"),
    BotCommand("metas", "Metas de ahorro"),
    BotCommand("lista", "Lista de compras"),
    BotCommand("recordatorios", "Próximos recordatorios"),
    BotCommand("panoramas", "Panoramas pendientes"),
    BotCommand("notas", "Datos de la casa"),
    BotCommand("pague", "Anotar que le pagaste a tu pareja"),
    BotCommand("gasto", "Registro rápido de gasto"),
    BotCommand("ingreso", "Registro rápido de ingreso"),
    BotCommand("borrar_pruebas", "Borrar registros de prueba"),
    BotCommand("ayuda", "Cómo usar el bot"),
]


async def _configurar_menu(app: Application) -> None:
    comandos = list(COMANDOS)
    url = config.dashboard_url()
    if url:
        comandos.insert(0, BotCommand("dashboard", "Ver gráficos"))
        # Botón fijo al lado del cuadro de texto que abre la Mini App (solo en chats privados).
        await app.bot.set_chat_menu_button(menu_button=MenuButtonWebApp("📊 Dashboard", WebAppInfo(url)))
    else:
        await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    await app.bot.set_my_commands(comandos)


def _crear_app() -> Application:
    autorizados = filters.User(user_id=config.allowed_telegram_ids())
    comandos = {
        "start": start, "ayuda": ayuda, "dashboard": dashboard,
        "gasto": plata.gasto, "ingreso": plata.ingreso, "pague": plata.pague,
        "saldo": plata.saldo, "resumen": plata.resumen, "borrar_pruebas": plata.borrar_pruebas,
        "metas": hogar.cmd_metas, "lista": hogar.cmd_lista, "recordatorios": hogar.cmd_recordatorios,
        "panoramas": hogar.cmd_panoramas, "notas": hogar.cmd_notas,
    }

    app = Application.builder().token(config.telegram_bot_token()).build()
    for nombre, handler in comandos.items():
        app.add_handler(CommandHandler(nombre, handler, filters=autorizados))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & autorizados, texto_libre))
    app.add_handler(CallbackQueryHandler(plata.botones_propuesta, pattern=r"^(conf|corr|canc):[0-9a-f]+$"))
    app.add_handler(CallbackQueryHandler(plata.deshacer, pattern=r"^(deshacer|dpago):[\d,]+$"))
    app.add_handler(CallbackQueryHandler(hogar.cb_meta, pattern=r"^meta:"))
    app.add_handler(CallbackQueryHandler(hogar.cb_lista, pattern=r"^lista:"))
    app.add_handler(CallbackQueryHandler(hogar.cb_panorama, pattern=r"^pan:"))
    app.add_handler(CallbackQueryHandler(hogar.cb_borrar, pattern=r"^borrar:"))
    return app


async def _correr() -> None:
    """Bot (polling), servidor del dashboard y recordatorios en el mismo proceso."""
    app = _crear_app()
    async with app:
        await _configurar_menu(app)
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        runner = await dashboard_web.iniciar(config.puerto_web())
        tarea_recordatorios = asyncio.create_task(hogar.bucle_recordatorios(app.bot))
        log.info("Bot iniciado en modo polling (modelo: %s, dashboard: %s)",
                 config.claude_model(), config.dashboard_url() or "sin URL pública")
        try:
            await asyncio.Event().wait()
        finally:
            tarea_recordatorios.cancel()
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
