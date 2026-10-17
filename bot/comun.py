"""Utilidades compartidas por los módulos del bot."""

import logging
import re
from datetime import date, datetime
from decimal import Decimal
from html import escape

from telegram import Bot, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import TelegramError

from core import cambio, finanzas
from core.finanzas import formatear_clp

log = logging.getLogger(__name__)

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

# "test" o "#prueba" en el mensaje -> movimiento de prueba (no cuenta en saldos ni resúmenes).
RE_PRUEBA = re.compile(r"(?<!\w)(#?test|#prueba)(?!\w)", re.IGNORECASE)


def es_prueba(texto: str) -> bool:
    return bool(RE_PRUEBA.search(texto or ""))


def sin_marca_prueba(texto: str) -> str:
    return re.sub(r"\s{2,}", " ", RE_PRUEBA.sub("", texto)).strip(" ,.:-")


def es_grupo(update: Update) -> bool:
    return update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


def primer_nombre(usuario: dict) -> str:
    return usuario["nombre"].split()[0]


async def usuario_o_aviso(update: Update) -> dict | None:
    usuario = finanzas.obtener_usuario(update.effective_user.id)
    if usuario is None:
        await update.effective_message.reply_text("¡Wena! Primero tira un /start para registrarte 🙌")
    return usuario


async def usuario_de_boton(query: CallbackQuery) -> dict | None:
    """Usuario registrado que tocó el botón, o None (y avisa) si no está autorizado."""
    usuario = finanzas.obtener_usuario(query.from_user.id)
    if usuario is None:
        await query.answer("Este bot es solo para la pareja 🙅", show_alert=True)
    return usuario


async def avisar_pareja(bot: Bot, update: Update, usuario: dict, texto: str) -> None:
    """Manda un aviso privado a la pareja. En grupos no hace falta: los dos ven el chat."""
    if es_grupo(update):
        return
    pareja = finanzas.otro_usuario(usuario["id"])
    if pareja is None:
        return
    try:
        await bot.send_message(pareja["telegram_id"], texto, parse_mode="HTML")
    except TelegramError:
        log.warning("No pude avisarle a %s (¿no ha iniciado el bot?)", pareja["nombre"])


def formatear_fecha(fecha: date) -> str:
    hoy = finanzas.hoy()
    dias = (fecha - hoy).days
    if dias == 0:
        return "hoy"
    if dias == -1:
        return "ayer"
    if dias == 1:
        return "mañana"
    texto = f"{fecha.day} de {MESES[fecha.month - 1]}"
    return texto if fecha.year == hoy.year else f"{texto} de {fecha.year}"


def formatear_fecha_hora(momento: datetime) -> str:
    local = momento.astimezone(finanzas.ZONA)
    fecha = formatear_fecha(local.date())
    if fecha not in ("hoy", "mañana", "ayer"):
        fecha = f"{DIAS[local.weekday()]} {fecha}"
    return f"{fecha} a las {local:%H:%M}"


def texto_saldo() -> str:
    filas = finanzas.balances()
    if len(filas) < 2:
        return "Falta que tu pareja se registre con /start 🙏"
    acreedor = max(filas, key=lambda f: f["balance"])
    deudor = min(filas, key=lambda f: f["balance"])
    if round(acreedor["balance"]) == 0:
        return "⚖️ ¡Están a mano! Nadie le debe a nadie 🙌"
    return f"⚖️ {escape(deudor['nombre'])} le debe {formatear_clp(acreedor['balance'])} a {escape(acreedor['nombre'])}"


def formatear_monto(monto: Decimal, moneda: str) -> str:
    if moneda == "CLP":
        return formatear_clp(monto)
    if moneda == "USD":
        return "US$" + f"{monto:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".").removesuffix(",00")
    return formatear_clp(monto) + f" {moneda}"


def monto_clp(mov: dict) -> Decimal:
    """Monto del movimiento convertido a CLP con el tipo de cambio de hoy (solo para mostrar)."""
    return mov["monto"] * cambio.tasa(mov["moneda"], finanzas.hoy())


def porcentaje(valor: Decimal) -> str:
    """50 -> '50%', 50.00 -> '50%', 33.5 -> '33,5%'."""
    valor = Decimal(valor)
    texto = f"{valor:.0f}" if valor == valor.to_integral_value() else f"{valor.normalize()}".replace(".", ",")
    return f"{texto}%"


def describir_movimiento(mov: dict, usuario: dict, pareja: dict | None) -> str:
    """Texto de un movimiento para mostrar al usuario (HTML)."""
    categoria = finanzas.categoria_por_nombre(mov["categoria"], mov["tipo"])
    signo = "💸" if mov["tipo"] == "gasto" else "💰"
    titulo = f"{signo} <b>{'Gasto' if mov['tipo'] == 'gasto' else 'Ingreso'} de {formatear_monto(mov['monto'], mov['moneda'])}</b>"
    if mov["moneda"] != "CLP":
        titulo += f" (≈ {formatear_clp(monto_clp(mov))})"
    lineas = [
        titulo,
        f"{categoria['emoji']} {escape(categoria['nombre'])} · {formatear_fecha(mov['fecha'])}",
    ]
    if mov["descripcion"]:
        lineas.append(f"📝 {escape(mov['descripcion'])}")
    pct = mov["porcentaje_pagador"]
    if pct is not None:
        nombre_pareja = escape(pareja["nombre"]) if pareja else "tu pareja"
        pct_pareja = Decimal(100) - pct
        lineas.append(f"👥 Compartido: {escape(primer_nombre(usuario))} {porcentaje(pct)} · "
                      f"{nombre_pareja} {porcentaje(pct_pareja)} "
                      f"({nombre_pareja} debe {formatear_clp(monto_clp(mov) * pct_pareja / 100)})")
    elif mov["tipo"] == "gasto":
        lineas.append(f"👤 Individual, pagado por {escape(usuario['nombre'])}")
    else:
        lineas.append(f"👤 Ingreso de {escape(usuario['nombre'])}")
    if mov.get("es_prueba"):
        lineas.append("🧪 <b>PRUEBA</b>: no cuenta en saldos ni resúmenes")
    return "\n".join(lineas)


def guardar_movimiento(mov: dict, usuario: dict, pareja: dict | None, mensaje_original: str) -> int:
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


def problema_movimientos(movimientos: list[dict], pareja: dict | None) -> str | None:
    """Motivo por el que no se pueden guardar estos movimientos, o None si están OK."""
    try:
        for m in movimientos:
            cambio.tasa(m["moneda"], finanzas.hoy())
    except cambio.SinTipoDeCambio:
        return "Pucha, no pude conseguir el tipo de cambio ahora. Intenta en un rato 🙏"
    if any(m["porcentaje_pagador"] is not None for m in movimientos) and pareja is None:
        return "Tu pareja todavía no se registra (tiene que tirar un /start), así que aún no puedo dividir gastos 🤷"
    return None


def boton(texto: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(texto, callback_data=data)


def teclado(*filas: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([f for f in filas if f])
