"""Preguntas en lenguaje natural, foto de boleta y resumen semanal automático.

Claude solo decide QUÉ consultar; los números salen de SQL (core/reportes.py).
"""

import asyncio
import logging
import re
from datetime import date, timedelta
from html import escape

from telegram import Bot, Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import comun, interprete, plata
from bot import hogar as hogar_bot
from core import finanzas, hogar, metas, reportes
from core.finanzas import formatear_clp

log = logging.getLogger(__name__)


# ---------- preguntas ----------

def _periodo(desde: date, hasta: date) -> str:
    hoy = finanzas.hoy()
    if desde == hasta:
        return comun.formatear_fecha(desde)
    if desde == hoy.replace(day=1) and hasta == hoy:
        return "este mes"
    if desde == date(hoy.year, 1, 1) and hasta == hoy:
        return "este año"
    return f"del {comun.formatear_fecha(desde)} al {comun.formatear_fecha(hasta)}"


def _nombre_grupo(agrupar: str, grupo: str) -> str:
    if agrupar == "mes":
        anio, mes = grupo.split("-")
        return f"{comun.MESES[int(mes) - 1][:3]} {anio}"
    return grupo


async def responder_consulta(update: Update, usuario: dict, consulta: dict, texto_original: str) -> None:
    if re.search(r"\bdeb|saldo|a mano", texto_original.lower()):
        await update.effective_message.reply_text(comun.texto_saldo(), parse_mode="HTML")
        return

    pareja = finanzas.otro_usuario(usuario["id"])
    persona = consulta["persona"]
    aviso_privacidad = ""
    if consulta["metrica"] == "ingresos" and persona != "yo":
        aviso_privacidad = "🔒 Los ingresos de cada uno son privados, así que te muestro solo los tuyos.\n\n"
        persona = "yo"
    if persona == "pareja" and pareja is None:
        await update.effective_message.reply_text("Tu pareja todavía no se registra 🤷")
        return

    categoria = None if consulta["categoria"] == "todas" else consulta["categoria"]
    resultado = reportes.consultar(usuario["id"], pareja["id"] if pareja else None, consulta["metrica"],
                                   consulta["desde"], consulta["hasta"], categoria, persona,
                                   consulta["agrupar"], consulta["busqueda"])

    quien = {"yo": " tuyos", "pareja": f" de {escape(comun.primer_nombre(pareja))}" if pareja else "",
             "ambos": ""}[persona]
    filtro = ""
    if categoria:
        emoji = finanzas.categoria_por_nombre(categoria, "gasto" if consulta["metrica"] == "gastos" else "ingreso")
        filtro += f" en {emoji['emoji'] if emoji else ''} {escape(categoria)}"
    if consulta["busqueda"]:
        filtro += f" con «{escape(consulta['busqueda'])}»"
    titulo = "💸 Gastos" if consulta["metrica"] == "gastos" else "💰 Ingresos"
    periodo = _periodo(consulta["desde"], consulta["hasta"])

    if resultado["cantidad"] == 0:
        await update.effective_message.reply_text(
            f"{aviso_privacidad}🤷 No encontré {titulo.split()[1].lower()}{quien}{filtro} {periodo}.", parse_mode="HTML")
        return

    lineas = [f"{aviso_privacidad}{titulo}{quien}{filtro} {periodo}:",
              f"<b>{formatear_clp(resultado['total'])}</b> en {resultado['cantidad']} "
              f"movimiento{'s' if resultado['cantidad'] != 1 else ''}"]
    if resultado["grupos"] and len(resultado["grupos"]) > 1 or consulta["agrupar"] == "mes":
        lineas.append("")
        lineas += [f"• {escape(_nombre_grupo(consulta['agrupar'], g['grupo']))}: {formatear_clp(g['total'])}"
                   for g in resultado["grupos"]]
    elif resultado["grupos"]:
        g = resultado["grupos"][0]
        lineas.append(f"Todo en {escape(_nombre_grupo(consulta['agrupar'], g['grupo']))}")
    await update.effective_message.reply_text("\n".join(lineas), parse_mode="HTML")


# ---------- foto de boleta ----------

async def foto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usuario = await comun.usuario_o_aviso(update)
    if usuario is None:
        return
    caption = update.message.caption or ""
    if not comun.es_grupo(update):
        await update.message.chat.send_action(ChatAction.TYPING)
    archivo = await update.message.photo[-1].get_file()
    imagen = bytes(await archivo.download_as_bytearray())
    try:
        datos = await interprete.interpretar(
            "[Foto adjunta] " + comun.sin_marca_prueba(caption), finanzas.ahora(),
            finanzas.nombres_categorias("gasto"), finanzas.nombres_categorias("ingreso"),
            [m["nombre"] for m in metas.listar()], imagen=imagen)
    except interprete.ErrorInterprete as e:
        await update.message.reply_text(str(e))
        return
    if datos["intencion"] == "movimientos":
        await plata.proponer(update, context, usuario, datos, f"📷 foto de boleta {caption}".strip(),
                             comun.es_prueba(caption))
    elif not comun.es_grupo(update):  # en el grupo las fotos normales no se comentan
        await update.message.reply_text(escape(datos["respuesta"]) or "🤔 No vi una boleta ahí.", parse_mode="HTML")


# ---------- resumen semanal ----------

def texto_resumen_semanal(usuario: dict, hasta: date) -> str:
    d = reportes.resumen_semanal(usuario["id"], hasta)
    lineas = [f"🗓️ <b>Resumen de la semana</b> ({d['desde'].day}/{d['desde'].month} al {d['hasta'].day}/{d['hasta'].month})", ""]
    if d["semana"] > 0:
        lineas.append(f"💸 Gastaron <b>{formatear_clp(d['semana'])}</b>")
        if d["anterior"] > 0:
            pct = round((d["semana"] / d["anterior"] - 1) * 100)
            lineas.append(f"   {'▲' if pct >= 0 else '▼'} {abs(pct)}% vs la semana anterior "
                          + ("💪" if pct < 0 else "👀" if pct > 20 else ""))
        lineas += [f"   {c['emoji']} {escape(c['categoria'])}: {formatear_clp(c['total'])}" for c in d["categorias"]]
    else:
        lineas.append("💸 No anotaron gastos esta semana")
    lineas += ["", comun.texto_saldo()]
    if d["metas"]:
        lineas += ["", "🎯 <b>Metas</b>"] + [f"   {m['emoji']} {escape(m['nombre'])}: {m['porcentaje']}%" for m in d["metas"]]
    if d["recordatorios"]:
        lineas += ["", "⏰ <b>Esta semana</b>"] + [
            f"   • {escape(r['texto'])}: {comun.formatear_fecha_hora(r['fecha_hora'])}" for r in d["recordatorios"]]
    panorama = hogar.sortear_panorama()
    if panorama:
        lineas += ["", f"🎲 Idea para la semana: <b>{escape(panorama['titulo'])}</b>"]
    lineas += ["", "¡Buena semana, pimpollos! 🐣"]
    return "\n".join(lineas)


async def cmd_semana(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usuario = await comun.usuario_o_aviso(update)
    if usuario is None:
        return
    hoy = finanzas.hoy()
    domingo = hoy + timedelta(days=6 - hoy.weekday())  # semana en curso (lunes a domingo)
    await update.effective_message.reply_text(texto_resumen_semanal(usuario, min(domingo, hoy)), parse_mode="HTML")


async def bucle_programados(bot: Bot) -> None:
    """Aviso diario de tareas (9:00) y resumen semanal los domingos desde las 20:00. Cada uno se envía una vez."""
    while True:
        try:
            ahora = finanzas.ahora()
            if ahora.hour >= 9 and await asyncio.to_thread(hogar.reservar_envio, f"tareas-{ahora:%Y-%m-%d}"):
                await hogar_bot.avisar_tareas_del_dia(bot)
            if ahora.weekday() == 6 and ahora.hour >= 20:
                clave = f"resumen-semanal-{ahora:%G-W%V}"
                if await asyncio.to_thread(hogar.reservar_envio, clave):
                    for usuario in await asyncio.to_thread(finanzas.todos_los_usuarios):
                        try:
                            await bot.send_message(usuario["telegram_id"],
                                                   texto_resumen_semanal(usuario, ahora.date()), parse_mode="HTML")
                        except TelegramError:
                            log.warning("No pude enviar el resumen semanal a %s", usuario["nombre"])
        except Exception:
            log.exception("Error en envíos programados")
        await asyncio.sleep(60)
