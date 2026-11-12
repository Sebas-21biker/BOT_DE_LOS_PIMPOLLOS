"""Punto de entrada del bot: arma los handlers y corre bot + dashboard + recordatorios.

Uso: python -m bot.main
"""

import asyncio
import logging
import signal
from html import escape

from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonCommands,
                      MenuButtonWebApp, Update, WebAppInfo)
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler,
                          filters)

from bot import automatico, comun, compromisos, consultas, conversacion, historial, hogar, interprete, pareja, plata
from core import config, db, estado, finanzas, metas
from core import hogar as hogar_core
from dashboard import web as dashboard_web

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

_AYUDA = (
    "✍️ <b>Escríbeme normal, como en tus notas:</b>\n\n"
    "💸 <i>hoy gasté 3500 en uber</i> · <i>cena 40 lucas a medias</i>\n"
    "🤝 <i>le transferí 25 lucas a {pareja}</i> (para saldar)\n"
    "💳 <i>compré una tele en 600 lucas en 6 cuotas</i> · <i>mi tarjeta factura el 25 y se paga el 5</i>\n"
    "📌 <i>mi sueldo es 1.200.000 y me llega el 30</i> (te pregunto cada mes)\n"
    "🎯 <i>queremos juntar 2 millones para México en julio</i> · <i>aparté 50 lucas para México</i>\n"
    "🛒 <i>agrega leche y huevos a la lista</i>\n"
    "⏰ <i>recuérdame pagar la luz el 5 a las 10</i> · <i>el 3 de marzo es el cumple de {pareja}</i>\n"
    "📅 <i>el sábado vamos al asado de Juan a las 14</i> · <i>¿qué planes tenemos?</i>\n"
    "📍 <i>anota el restaurante Liguria para ir</i> · <i>¿qué hacemos hoy?</i>\n"
    "📌 <i>la clave del wifi es pimpollo123</i> · <i>¿cuál era la clave del wifi?</i>\n"
    "❓ <i>¿cuánto gastamos en uber este año?</i> · <i>¿en qué gastamos más este mes?</i>\n"
    "💝 <i>a {pareja} le encanta el sushi</i> · <i>¿qué le gusta a {pareja}?</i>\n"
    "💡 <i>recomiéndanos algo para el sábado</i> · <i>ideas de regalo para {pareja}</i>\n"
    "📷 Mándame la foto de una boleta y la anoto\n\n"
    "📋 <b>Comandos</b>\n"
    "/panel · /dashboard · /ultimos · /saldo · /resumen · /semana · /cuotas · /fijos · /metas · /lista · /tareas · /menu\n"
    "/recordatorios · /planes · /panoramas · /notas · /perfil · /ideas · /pregunta · /feriados · /invitar\n"
    "🙋 /iniciativa: cuánto me meto solo en la charla del grupo (y avisos de sorpresas por privado)\n"
    "📱 /automatico (pagos con el celular) y 🏦 /correo (correos del banco) para anotar solos, con tu confirmación\n"
    "/gasto, /ingreso y /pague para anotar rápido sin IA\n"
    "✏️ /nombre Ricardo: cómo quieres que te llame\n\n"
    "🧪 Si escribes <b>test</b> en un gasto o pago, lo anoto como prueba (no cuenta). "
    "Los borras con /borrar_pruebas"
)


def texto_ayuda(usuario: dict) -> str:
    """La ayuda con el nombre real de la pareja en los ejemplos (o "tu pareja" si aún no se registra)."""
    pareja = finanzas.otro_usuario(usuario["id"])
    return _AYUDA.format(pareja=escape(comun.primer_nombre(pareja)) if pareja else "tu pareja")


# ---------- comandos generales ----------

# Quiénes pueden usar el bot: ALLOWED_TELEGRAM_IDS + usuarios registrados (se amplía al aceptar una invitación).
AUTORIZADOS = filters.User(allow_empty=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registro: abierto solo para ALLOWED_TELEGRAM_IDS o con un link de invitación válido."""
    usuario_tg = update.effective_user
    usuario = finanzas.obtener_usuario(usuario_tg.id)
    if usuario is None and usuario_tg.id in config.allowed_telegram_ids():
        usuario = db.registrar_usuario(usuario_tg.id, usuario_tg.first_name)
    elif usuario is None and context.args and context.args[0].startswith("inv_"):
        resultado = db.usar_invitacion(context.args[0].removeprefix("inv_"), usuario_tg.id, usuario_tg.first_name)
        if resultado is None:
            await update.message.reply_text("🙈 Ese link ya se usó o venció. Pídele uno nuevo a tu pareja con /invitar")
            return
        usuario, invito = resultado
        AUTORIZADOS.add_user_ids(usuario_tg.id)
        try:
            await context.bot.send_message(
                invito["telegram_id"],
                f"🎉 <b>{escape(usuario['nombre'])}</b> se unió a Pimpollo. ¡Ya están los dos! 🐣", parse_mode="HTML")
        except TelegramError:
            pass
    if usuario is None:
        await update.message.reply_text("🙅 Este bot es privado. Si tu pareja lo usa, pídele un link con /invitar")
        return
    await update.message.reply_text(f"¡Wena {escape(usuario['nombre'])}! Ya quedaste registrado 🐣\n\n"
                                    f"{texto_ayuda(usuario)}", parse_mode="HTML")


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usuario = await comun.usuario_o_aviso(update)
    if usuario is not None:
        await update.message.reply_text(texto_ayuda(usuario), parse_mode="HTML")


async def cmd_nombre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/nombre Catalina — cómo te llama el bot (al registrarse se toma el nombre de Telegram)."""
    usuario = await comun.usuario_o_aviso(update)
    if usuario is None:
        return
    nuevo = " ".join(context.args).strip()[:40]
    if not nuevo:
        await update.message.reply_text(f"Ahora te llamo <b>{escape(usuario['nombre'])}</b>. Para cambiarlo: "
                                        "<code>/nombre Tu Nombre</code>", parse_mode="HTML")
        return
    db.cambiar_nombre(usuario["id"], nuevo)
    await update.message.reply_text(f"✅ Listo, desde ahora eres <b>{escape(nuevo)}</b> 🐣", parse_mode="HTML")


def _boton_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardButton | None:
    url = config.dashboard_url()
    if not url:
        return None
    if comun.es_grupo(update):
        # En grupos Telegram no permite botones web_app: se usa el enlace directo a la Mini App principal.
        return InlineKeyboardButton("📊 Dashboard", url=f"https://t.me/{context.bot.username}?startapp")
    return InlineKeyboardButton("📊 Dashboard", web_app=WebAppInfo(url))


async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    boton = _boton_dashboard(update, context)
    if boton is None:
        await update.message.reply_text("El dashboard todavía no tiene dirección pública 🤷")
        return
    await update.message.reply_text("Ahí está, ábrelo al tiro 👇", reply_markup=InlineKeyboardMarkup([[boton]]))


async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mensaje con accesos directos; en grupos se fija arriba del chat."""
    filas = [
        [comun.boton("🛒 Lista", "panel:lista"), comun.boton("🎯 Metas", "panel:metas")],
        [comun.boton("⚖️ Saldo", "panel:saldo"), comun.boton("📊 Resumen", "panel:resumen")],
        [comun.boton("⏰ Recordatorios", "panel:recordatorios"), comun.boton("📍 Panoramas", "panel:panoramas")],
        [comun.boton("🧹 Tareas", "panel:tareas"), comun.boton("📌 Notas", "panel:notas")],
        [comun.boton("🍽️ Menú semanal", "panel:menu"), comun.boton("🎲 ¿Qué hacemos?", "pan:sort:cualquiera")],
        [comun.boton("💝 Perfil", "panel:perfil"), comun.boton("💡 Ideas para nosotros", "panel:ideas")],
    ]
    if boton := _boton_dashboard(update, context):
        filas.insert(0, [boton])
    mensaje = await update.message.reply_text(
        "🐣 <b>Panel Pimpollo</b>\nToca lo que necesites, o escríbanme normal 👇",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(filas))
    if comun.es_grupo(update):
        try:
            await context.bot.pin_chat_message(update.effective_chat.id, mensaje.message_id,
                                               disable_notification=True)
        except TelegramError:
            await update.message.reply_text(
                "📌 No pude fijarlo: hazme administrador del grupo con permiso para fijar mensajes "
                "(o fíjalo tú: mantén presionado el mensaje → Fijar).")


async def _cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/menu [preferencias] — menú de cenas de la semana con lista de ingredientes."""
    if await comun.usuario_o_aviso(update) is None:
        return
    await hogar._menu_semanal(update, context, " ".join(context.args or []))


async def cb_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if await comun.usuario_de_boton(query) is None:
        return
    await query.answer()
    vistas = {"lista": hogar.cmd_lista, "metas": hogar.cmd_metas, "saldo": plata.saldo, "resumen": plata.resumen,
              "recordatorios": hogar.cmd_recordatorios, "panoramas": hogar.cmd_panoramas, "notas": hogar.cmd_notas,
              "tareas": hogar.cmd_tareas, "menu": _cmd_menu, "planes": hogar.cmd_planes,
              "perfil": pareja.cmd_perfil, "ideas": pareja.cmd_ideas}
    await vistas[query.data.split(":")[1]](update, context)


# ---------- texto libre ----------

def _le_hablan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """En un grupo: ¿el mensaje nombra al bot o responde a un mensaje suyo?"""
    texto = (update.message.text or "").lower()
    respuesta_a = update.message.reply_to_message
    return ("pimpollo" in texto or f"@{context.bot.username.lower()}" in texto
            or (respuesta_a is not None and respuesta_a.from_user.id == context.bot.id))


async def texto_libre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usuario = await comun.usuario_o_aviso(update)
    if usuario is None:
        return
    texto = update.message.text
    grupo = comun.es_grupo(update)
    tareas = [t["nombre"] for t in hogar_core.listar_tareas()]

    esperando = context.user_data.get("esperando")
    if grupo and esperando and esperando.get("tipo") == "pregunta":
        esperando = None  # la pregunta pimpolla se hizo por privado: se responde allá, no con la charla del grupo

    # El hilo de la charla (antes de anotar este mensaje). En el grupo entra toda la charla, no solo lo que va al bot.
    chat_id = update.effective_chat.id
    hilo = historial.reciente(chat_id)
    historial.anotar(chat_id, usuario["nombre"], texto)

    le_hablan = True
    if grupo:
        ahora = finanzas.ahora()
        nombrado = _le_hablan(update, context)   # lo nombran, @mención o le responden
        le_hablan = nombrado
        if not le_hablan and esperando is None and "corrigiendo" not in context.user_data:
            # Sin nombrarlo: ventana de conversación + detector barato (Haiku). Ante la duda, no.
            le_hablan = await conversacion.me_hablan(chat_id, usuario, texto, hilo, ahora)
        if le_hablan:
            conversacion.abrir_ventana(chat_id, usuario["id"], ahora)
        pasa_a_ia = (esperando is not None or "corrigiendo" in context.user_data or le_hablan
                     or conversacion.merece_ia(texto, tareas))
        # Memoria corta (48 h) para que Pimpollo evalúe la charla cuando se pausa (fase A: iniciativa).
        await conversacion.guardar_mensaje(update, usuario, al_bot=pasa_a_ia)
        if nombrado and await conversacion.manejar_silencio(update):
            return
        # En el grupo el bot recibe toda la conversación: la charla normal no pasa por la IA (no gasta tokens).
        if not pasa_a_ia:
            return

    # ¿Responde a una pregunta del bot (monto de un aporte, total de la compra...)?
    contexto = intencion_forzada = movimiento_id = None
    if esperando:
        context.user_data.pop("esperando", None)
        resultado = await hogar.manejar_esperando(update, context, usuario, esperando)
        if resultado is None:
            return
        contexto, intencion_forzada = resultado["contexto"], resultado.get("intencion")
        movimiento_id = resultado.get("movimiento_id")

    # ¿Es la corrección de una propuesta anterior?
    prueba = comun.es_prueba(texto)
    anterior = None
    if previo := plata.tomar_para_corregir(context):
        anterior = (comun.sin_marca_prueba(previo["mensaje"]), previo["crudo"])
        prueba = prueba or previo["es_prueba"]

    # El mensaje del bot al que responde, si cita uno.
    citado = update.message.reply_to_message
    responde_a = (citado.text or citado.caption or "") if citado and citado.from_user.id == context.bot.id else ""

    if not grupo:
        await update.message.chat.send_action(ChatAction.TYPING)
    try:
        datos = await interprete.interpretar(
            comun.sin_marca_prueba(texto), finanzas.ahora(),
            finanzas.nombres_categorias("gasto"), finanzas.nombres_categorias("ingreso"),
            [m["nombre"] for m in metas.listar()], tareas,
            anterior=anterior, contexto=contexto, intencion=intencion_forzada,
            motivo="grupo" if grupo else "mensaje", hilo=hilo, responde_a=responde_a,
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
    elif await compromisos.manejar_intencion(update, context, usuario, datos):
        pass
    elif intencion == "editar_movimiento":
        movimiento_id = movimiento_id or (previo or {}).get("movimiento_id")
        mensaje_original = f"{previo['mensaje']} | corrección: {texto}" if anterior else texto
        await plata.proponer_edicion(update, context, usuario, datos, mensaje_original, movimiento_id)
    elif intencion == "borrar_movimiento":
        await plata.proponer_borrado(update, context, usuario, datos["objetivo"])
    elif intencion == "perfil_guardar":
        origen = "pregunta" if esperando and esperando.get("tipo") == "pregunta" else "dicho"
        await pareja.guardar_perfil(update, context, usuario, datos["perfil"], origen)
    elif intencion == "perfil_ver":
        await pareja.ver_perfil(update, usuario, datos["perfil_filtro"])
    elif intencion == "pedir_ideas":
        await pareja.pedir_ideas(update, context, usuario, datos["busqueda"])
    elif intencion == "consulta":
        await consultas.responder_consulta(update, usuario, datos["consulta"], texto)
    elif grupo and not le_hablan:
        return  # en el grupo solo conversa si le hablan (lo nombran, le responden o el detector lo reconoce)
    else:
        await update.message.reply_text(escape(datos["respuesta"]) or "Mmm, no caché qué necesitas 🤔 Mira /ayuda",
                                        parse_mode="HTML")


# ---------- arranque ----------

COMANDOS = [
    BotCommand("panel", "Accesos directos (fíjalo en el grupo)"),
    BotCommand("saldo", "Quién le debe a quién"),
    BotCommand("resumen", "Resumen del mes"),
    BotCommand("ultimos", "Corregir o borrar tus últimos gastos"),
    BotCommand("semana", "Resumen de la semana"),
    BotCommand("metas", "Metas de ahorro"),
    BotCommand("lista", "Lista de compras"),
    BotCommand("recordatorios", "Próximos recordatorios"),
    BotCommand("planes", "Planes con fecha"),
    BotCommand("cuotas", "Cuotas, fijos y tarjetas"),
    BotCommand("panoramas", "Panoramas pendientes"),
    BotCommand("notas", "Datos de la casa"),
    BotCommand("tareas", "Tareas de la casa y turnos"),
    BotCommand("menu", "Menú de la semana"),
    BotCommand("perfil", "Lo que el bot sabe de ustedes"),
    BotCommand("ideas", "Ideas de citas, regalos y panoramas"),
    BotCommand("pregunta", "Una pregunta para conocerse mejor"),
    BotCommand("feriados", "Feriados y fechas especiales"),
    BotCommand("automatico", "Anotar pagos del celular automáticamente"),
    BotCommand("correo", "Anotar gastos desde los correos del banco"),
    BotCommand("iniciativa", "Cuánto me meto solo en la conversación"),
    BotCommand("costo", "Cuánto va costando la IA este mes"),
    BotCommand("invitar", "Link para que tu pareja se una"),
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
    AUTORIZADOS.add_user_ids(config.allowed_telegram_ids() | db.telegram_ids_registrados())
    autorizados = AUTORIZADOS
    comandos = {
        "ayuda": ayuda, "invitar": pareja.cmd_invitar, "perfil": pareja.cmd_perfil, "ideas": pareja.cmd_ideas, "ultimos": plata.cmd_ultimos, "pregunta": pareja.cmd_pregunta, "feriados": pareja.cmd_feriados,
        "automatico": automatico.cmd_automatico, "correo": automatico.cmd_correo, "costo": consultas.cmd_costo,
        "dashboard": dashboard, "panel": panel,
        "gasto": plata.gasto, "ingreso": plata.ingreso, "pague": plata.pague,
        "saldo": plata.saldo, "resumen": plata.resumen, "borrar_pruebas": plata.borrar_pruebas,
        "metas": hogar.cmd_metas, "lista": hogar.cmd_lista, "recordatorios": hogar.cmd_recordatorios,
        "panoramas": hogar.cmd_panoramas, "notas": hogar.cmd_notas, "semana": consultas.cmd_semana,
        "tareas": hogar.cmd_tareas, "menu": _cmd_menu, "planes": hogar.cmd_planes, "iniciativa": conversacion.cmd_iniciativa,
        "cuotas": compromisos.cmd_compromisos, "fijos": compromisos.cmd_compromisos, "nombre": cmd_nombre,
    }

    # El estado (propuestas por confirmar, respuestas esperadas...) se guarda en Postgres: sobrevive a los deploys.
    # BotConMemoria anota lo que el bot dice en el hilo corto de cada chat (para entender "eso", "añádelo de nuevo").
    app = (Application.builder().bot(historial.BotConMemoria(config.telegram_bot_token()))
           .persistence(estado.PersistenciaPostgres()).build())
    app.add_handler(CommandHandler("start", start))  # abierto: valida invitaciones
    for nombre, handler in comandos.items():
        app.add_handler(CommandHandler(nombre, handler, filters=autorizados))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & autorizados, texto_libre))
    app.add_handler(MessageHandler(filters.PHOTO & autorizados, consultas.foto))
    app.add_handler(CallbackQueryHandler(plata.botones_propuesta, pattern=r"^(conf|corr|canc):[0-9a-f]+$"))
    app.add_handler(CallbackQueryHandler(plata.deshacer, pattern=r"^(deshacer|dpago|dcuotas):[\d,]+$"))
    app.add_handler(CallbackQueryHandler(compromisos.cb_compromiso, pattern=r"^comp:(tarjeta|fijo|cancelar):\d+$"))
    app.add_handler(CallbackQueryHandler(hogar.cb_meta, pattern=r"^meta:"))
    app.add_handler(CallbackQueryHandler(hogar.cb_lista, pattern=r"^lista:"))
    app.add_handler(CallbackQueryHandler(hogar.cb_panorama, pattern=r"^pan:"))
    app.add_handler(CallbackQueryHandler(hogar.cb_borrar, pattern=r"^borrar:"))
    app.add_handler(CallbackQueryHandler(cb_panel, pattern=r"^panel:"))
    app.add_handler(CallbackQueryHandler(hogar.cb_tarea, pattern=r"^tarea:"))
    app.add_handler(CallbackQueryHandler(hogar.cb_menu, pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(pareja.cb_idea, pattern=r"^idea:"))
    app.add_handler(CallbackQueryHandler(plata.cb_movimiento, pattern=r"^mov:"))
    app.add_handler(CallbackQueryHandler(pareja.cb_pedir_ideas, pattern=r"^pideas:"))
    app.add_handler(CallbackQueryHandler(hogar.cb_plan, pattern=r"^plan:cancelado:\d+$"))
    app.add_handler(CallbackQueryHandler(pareja.cb_recuerdo_pendiente, pattern=r"^recp:(si|no):[0-9a-f]+$"))
    app.add_handler(CallbackQueryHandler(conversacion.cb_dato_grupo, pattern=r"^rec:(si|no):\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(conversacion.cb_iniciativa, pattern=r"^ini:(si|no):\d+$"))
    app.add_handler(CallbackQueryHandler(conversacion.cb_sorpresa, pattern=r"^sorp:(g|menos):\d+:\d+$"))
    return app


async def _correr() -> None:
    """Bot (polling), servidor del dashboard y recordatorios en el mismo proceso."""
    app = _crear_app()
    async with app:
        await _configurar_menu(app)
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        runner = await dashboard_web.iniciar(
            config.puerto_web(),
            al_recibir_pago=lambda usuario, texto, origen: automatico.procesar_pago(app, usuario, texto, origen))
        tareas = [asyncio.create_task(hogar.bucle_recordatorios(app.bot)),
                  asyncio.create_task(consultas.bucle_programados(app)),
                  asyncio.create_task(conversacion.bucle_iniciativa(app))]
        log.info("Bot iniciado en modo polling (modelo: %s, dashboard: %s)",
                 config.claude_model(), config.dashboard_url() or "sin URL pública")
        detener = asyncio.Event()
        try:
            # Railway manda SIGTERM al redesplegar: se apaga ordenado para guardar el estado antes de salir.
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, detener.set)
        except (NotImplementedError, AttributeError):
            pass  # Windows (local): no hay add_signal_handler; Ctrl+C igual cierra ordenado
        try:
            await detener.wait()
        finally:
            for tarea in tareas:
                tarea.cancel()
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
