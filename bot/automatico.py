"""Pagos automáticos: el celular avisa cada pago (Atajos de iPhone / MacroDroid en Android) a una URL secreta
y el bot propone el gasto con Confirmar / Corregir. Nunca se guarda sin confirmación."""

import logging
from html import escape

from telegram import Update
from telegram.ext import ContextTypes

from bot import comun, interprete, plata
from core import config, db, finanzas, hogar, metas

log = logging.getLogger(__name__)

CONTEXTO = ("Es una notificación AUTOMÁTICA de un pago hecho con el celular o una tarjeta (no la escribió el "
            "usuario). Es un gasto individual de quien pagó, con fecha de hoy salvo que diga otra. Usa el comercio "
            "como descripción y para elegir la categoría (Metro, Red, Uber, Copec → Transporte; Lider, Jumbo, "
            "Unimarc → Supermercado; restoranes y cafés → Comida fuera...).")


async def procesar_pago(app, usuario: dict, texto: str) -> None:
    """Interpreta el aviso de pago y le manda la propuesta al usuario por privado."""
    try:
        datos = await interprete.interpretar(
            comun.sin_marca_prueba(texto), finanzas.ahora(),
            finanzas.nombres_categorias("gasto"), finanzas.nombres_categorias("ingreso"),
            [m["nombre"] for m in metas.listar()], [t["nombre"] for t in hogar.listar_tareas()],
            contexto=CONTEXTO, intencion="movimientos")
    except interprete.ErrorInterprete:
        datos = {"intencion": "otro"}
    try:
        if datos["intencion"] != "movimientos":
            await app.bot.send_message(
                usuario["telegram_id"],
                f"📱 Me llegó un aviso de pago pero no lo caché:\n<i>{escape(texto)}</i>\n¿Me lo anotas tú? 🙏",
                parse_mode="HTML")
            return
        await plata.enviar_propuesta(
            app.bot, app.bot_data, usuario["telegram_id"], usuario["telegram_id"], usuario, datos,
            f"📱 automático: {texto}", comun.es_prueba(texto),
            encabezado="📱 <b>Detecté un pago</b>. ¿Lo anoto así? 👇")
    except Exception:
        log.exception("No pude proponer el pago automático de %s", usuario["nombre"])


async def cmd_automatico(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/automatico — entrega la URL secreta y cómo configurar el celular. /automatico nuevo — cambia la URL."""
    usuario = await comun.usuario_o_aviso(update)
    if usuario is None:
        return
    if comun.es_grupo(update):
        await update.effective_message.reply_text("🔒 Esto es secreto: pídemelo por privado 😉")
        return
    base = config.dashboard_url()
    if not base:
        await update.effective_message.reply_text("Todavía no tengo dirección pública (falta el dominio en Railway) 🤷")
        return
    nuevo = bool(context.args) and context.args[0].lower() == "nuevo"
    token = db.token_automatico(usuario["id"], nuevo=nuevo)
    url = f"{base.rstrip('/')}/auto/{token}"
    await update.effective_message.reply_text(
        ("🔄 Listo, cambié tu dirección. La anterior ya no sirve.\n\n" if nuevo else "")
        + "📱 <b>Gastos automáticos</b>\n"
        "Tu celular me avisa cada vez que pagas y yo te pregunto si lo anoto (nada se guarda sin que confirmes).\n\n"
        f"🔑 Tu dirección secreta (no la compartas):\n<code>{escape(url)}</code>\n\n"
        "🍎 <b>iPhone</b> (Apple Pay):\n"
        "1. App <b>Atajos</b> → <b>Automatización</b> → <b>+</b> → <b>Transacción</b>.\n"
        "2. Elige tus tarjetas (y si quieres, solo algunos comercios) → <b>Ejecutar inmediatamente</b>.\n"
        "3. Acción <b>Obtener contenido de URL</b>: pega la dirección, Método <b>POST</b>, "
        "Cuerpo <b>JSON</b> con la clave <code>texto</code> y como valor: "
        "<i>Pagué [Importe] en [Comercio]</i> (usa las variables de la transacción).\n\n"
        "🤖 <b>Android</b> (Google Wallet o la app de tu banco):\n"
        "1. Instala <b>MacroDroid</b> (gratis).\n"
        "2. Disparador <b>Notificación recibida</b> → elige Google Wallet o tu banco.\n"
        "3. Acción <b>Solicitud HTTP</b>: método POST a tu dirección, cuerpo JSON "
        "<code>{\"texto\": \"[título] [texto de la notificación]\"}</code> con las variables de la notificación.\n\n"
        "🧪 Para probar sin que cuente, incluye la palabra <b>test</b> en el texto.\n"
        "🔄 Si alguien más vio tu dirección: <code>/automatico nuevo</code>",
        parse_mode="HTML", disable_web_page_preview=True)
