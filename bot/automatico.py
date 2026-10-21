"""Pagos automáticos: el celular avisa cada pago (Atajos de iPhone / MacroDroid en Android) a una URL secreta
y el bot propone el gasto con Confirmar / Corregir. Nunca se guarda sin confirmación."""

import logging
import re
import secrets
from datetime import timedelta
from html import escape
from pathlib import Path

from telegram import InputFile, Update
from telegram.ext import ContextTypes

from bot import comun, interprete, plata
from core import config, db, finanzas, hogar, metas

log = logging.getLogger(__name__)

CATEGORIAS_COMERCIO = ("Usa el comercio como descripción y para elegir la categoría (Metro, Red, Uber, Copec → "
                       "Transporte; Lider, Jumbo, Unimarc → Supermercado; restoranes y cafés → Comida fuera...).")

CONTEXTO_ATAJO = ("Es una notificación AUTOMÁTICA de un pago hecho con el celular o una tarjeta (no la escribió el "
                  "usuario). Es un gasto individual de quien pagó, con fecha de hoy salvo que diga otra. "
                  + CATEGORIAS_COMERCIO)

CONTEXTO_CORREO = ("Es el texto de un CORREO DE ALERTA DE UN BANCO chileno que le llegó al usuario (no lo escribió "
                   "él). Si informa una compra, cargo, pago, giro o transferencia enviada: es un gasto individual "
                   "(movimientos), con la fecha del correo. Si informa una transferencia recibida: es un ingreso. "
                   "Si la transferencia es hacia o desde su pareja ({pareja}), es pago_pareja (yo_pago si el usuario "
                   "le transfirió, me_pagan si recibió de ella/él). Si NO informa un movimiento de plata concreto con "
                   "monto (promociones, claves, avisos de seguridad, estados de cuenta, cupos disponibles), la "
                   "intención es otro. " + CATEGORIAS_COMERCIO)

PREFIJO = {"atajo": "📱 automático: ", "correo": "🏦 correo: "}
ENCABEZADO = {"atajo": "📱 <b>Detecté un pago</b>. ¿Lo anoto así? 👇",
              "correo": "🏦 <b>Me llegó un correo del banco</b>. ¿Lo anoto así? 👇"}


def _es_duplicado_de_correo(app, usuario: dict, mov: dict) -> bool:
    """Un correo duplica a un pago que ya llegó por otro camino (Apple Pay o escrito a mano).

    Se cuentan los movimientos parecidos (mismo monto y moneda, ±1 día), guardados o esperando confirmación:
    si hay más de otros orígenes que de correo, este correo es el "eco" de uno de ellos. Así dos viajes en
    metro de $820 anotados por Apple Pay no se duplican, pero dos correos de $820 sin Apple Pay sí se anotan.
    """
    origenes = finanzas.origenes_similares(usuario["id"], mov["monto"], mov["moneda"], mov["fecha"])
    for p in app.bot_data.get("pendientes", {}).values():
        if p.get("dueno") == usuario["telegram_id"] and p.get("tipo") == "movimientos":
            origenes += [p["mensaje"] for m in p["items"] if m["monto"] == mov["monto"] and m["moneda"] == mov["moneda"]]
    # Correos que ya se descartaron como eco de otro pago (cuentan como "de correo").
    base = f"eco:{usuario['id']}:{mov['moneda']}:{mov['monto']}:"
    ecos = sum(len(hogar.envios_con_prefijo(f"{base}{mov['fecha'] + timedelta(days=d)}:")) for d in (-1, 0, 1))
    de_correo = sum(o.startswith(PREFIJO["correo"]) for o in origenes) + ecos
    de_otros = len(origenes) - (de_correo - ecos)
    if de_otros > de_correo:
        hogar.reservar_envio(f"{base}{mov['fecha']}:{secrets.token_hex(4)}")  # queda emparejado con uno de ellos
        return True
    return False


# Un correo que no trae ningún monto ($12.990, US$ 30, "Monto: 25.000") no puede ser un cargo:
# se descarta sin llamar a Claude (las promociones con "30% dcto" cuestan $0).
_RE_MONTO = re.compile(r"(\$|\b(clp|usd|mxn|us\$)\b)\s?\d|\bmonto\b\D{0,20}\d", re.IGNORECASE)


async def procesar_pago(app, usuario: dict, texto: str, origen: str = "atajo") -> None:
    """Interpreta un aviso automático (celular o correo del banco) y le propone el movimiento al usuario."""
    origen = origen if origen in PREFIJO else "atajo"
    if origen == "correo" and not _RE_MONTO.search(texto):
        log.info("Correo de %s sin montos, se ignora sin usar la IA: %s", usuario["nombre"], texto[:80])
        return
    pareja = finanzas.otro_usuario(usuario["id"])
    try:
        datos = await interprete.interpretar(
            comun.sin_marca_prueba(texto), finanzas.ahora(),
            finanzas.nombres_categorias("gasto"), finanzas.nombres_categorias("ingreso"),
            [m["nombre"] for m in metas.listar()], [t["nombre"] for t in hogar.listar_tareas()],
            contexto=(CONTEXTO_CORREO.format(pareja=pareja["nombre"] if pareja else "sin pareja registrada")
                      if origen == "correo" else CONTEXTO_ATAJO),
            intencion=None if origen == "correo" else "movimientos",
            intenciones_posibles=["movimientos", "pago_pareja", "otro"],
            motivo=origen, extraer_otro=origen != "correo")
    except interprete.ErrorInterprete:
        datos = {"intencion": "otro"}
    try:
        if datos["intencion"] not in ("movimientos", "pago_pareja"):
            if origen == "correo":
                log.info("Correo de %s sin movimiento, se ignora: %s", usuario["nombre"], texto[:80])
                return  # promociones, claves, etc.: silencio
            await app.bot.send_message(
                usuario["telegram_id"],
                f"📱 Me llegó un aviso de pago pero no lo caché:\n<i>{escape(texto)}</i>\n¿Me lo anotas tú? 🙏",
                parse_mode="HTML")
            return
        if origen == "correo" and datos["intencion"] == "movimientos":
            datos["movimientos"] = [m for m in datos["movimientos"] if not _es_duplicado_de_correo(app, usuario, m)]
            if not datos["movimientos"]:
                log.info("Correo de %s duplica un pago ya anotado, se ignora", usuario["nombre"])
                return
        await plata.enviar_propuesta(
            app.bot, app.bot_data, usuario["telegram_id"], usuario["telegram_id"], usuario, datos,
            PREFIJO[origen] + texto[:300], comun.es_prueba(texto), encabezado=ENCABEZADO[origen])
    except Exception:
        log.exception("No pude proponer el aviso automático de %s", usuario["nombre"])


PLANTILLA_GMAIL = Path(__file__).resolve().parent / "plantillas" / "pimpollo_gmail.gs"


async def cmd_correo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/correo — entrega el script de Gmail (con la dirección secreta del usuario) y los pasos para instalarlo."""
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
    url = f"{base.rstrip('/')}/auto/{db.token_automatico(usuario['id'])}"
    script = PLANTILLA_GMAIL.read_text(encoding="utf-8").replace("__URL__", url)
    await update.effective_message.reply_text(
        "🏦 <b>Gastos desde los correos del banco</b>\n"
        "Un script en <b>tu</b> Gmail me pasa los correos de alerta del banco y yo te pregunto si los anoto. "
        "No me das tu clave ni tu correo; puedes apagarlo cuando quieras.\n\n"
        "<b>1. En la app de tu banco</b>\n"
        "Activa las alertas por correo de compras, cargos y transferencias.\n\n"
        "<b>2. En Gmail</b> (mejor desde el computador)\n"
        "• Busca un correo de alerta del banco → menú ⋮ → <b>Filtrar mensajes similares</b>.\n"
        "• Deja solo el remitente (De:) → <b>Crear filtro</b> → marca <b>Aplicar la etiqueta</b> → "
        "<b>Nueva etiqueta</b>: <code>Pimpollo</code> → <b>Crear filtro</b>.\n"
        "• Si tienes más de un banco, repite con cada remitente.\n\n"
        "<b>3. Instalar el script</b>\n"
        "• Entra a <b>script.google.com</b> → <b>Nuevo proyecto</b>.\n"
        "• Borra lo que aparece y pega el contenido del archivo que te mando abajo 👇 → 💾 Guardar.\n"
        "• Arriba elige la función <b>instalar</b> → ▶ <b>Ejecutar</b> → <b>Revisar permisos</b> → tu cuenta → "
        "<b>Configuración avanzada</b> → <b>Ir a … (no seguro)</b> → <b>Permitir</b>. "
        "(Sale ese aviso porque el script es tuyo y no está publicado; es normal.)\n"
        "• Elige la función <b>probar</b> → ▶ <b>Ejecutar</b>: te debería llegar un mensaje mío al tiro 🧪.\n\n"
        "Listo: desde ahí reviso cada 5 minutos. Los correos que no sean cargos (promos, claves) los ignoro, "
        "y si un pago ya llegó por Apple Pay no te pregunto dos veces 😉",
        parse_mode="HTML", disable_web_page_preview=True)
    await update.effective_message.reply_document(
        InputFile(script.encode("utf-8"), filename="pimpollo_gmail.gs"),
        caption="📎 Ábrelo, copia todo y pégalo en script.google.com. Tiene tu dirección secreta: no lo compartas 🔒")


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
