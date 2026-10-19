"""Invitación de la pareja, perfil (gustos, sueños, horarios...) e ideas personalizadas."""

import logging
import secrets
from datetime import date, timedelta
from html import escape
from urllib.parse import quote

from telegram import ForceReply, InlineKeyboardButton, Update
from telegram.ext import ContextTypes

from bot import comun, interprete
from bot.comun import boton, primer_nombre, teclado
from core import db, feriados, finanzas, hogar, perfil, preguntas

log = logging.getLogger(__name__)

EMOJI_PANORAMA = {"restaurante": "🍽️", "lugar": "📍", "pelicula": "🎬", "serie": "📺",
                  "actividad": "🎯", "viaje": "✈️", "otro": "✨"}


# ---------- invitación ----------

async def cmd_invitar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usuario = await comun.usuario_o_aviso(update)
    if usuario is None:
        return
    if not db.hay_cupo():
        await update.effective_message.reply_text("Ya están los dos registrados 🙌")
        return
    codigo = db.crear_invitacion(usuario["id"])
    link = f"https://t.me/{context.bot.username}?start=inv_{codigo}"
    compartir = f"https://t.me/share/url?url={quote(link)}&text={quote('¡Únete a Pimpollo, nuestro bot de pareja! 🐣')}"
    await update.effective_message.reply_text(
        "💌 Mándale este link a tu pareja. Lo abre, toca <b>Iniciar</b> y queda registrada al tiro.\n\n"
        f"{link}\n\n<i>Sirve una sola vez y vence en 7 días.</i>",
        parse_mode="HTML", disable_web_page_preview=True,
        reply_markup=teclado([InlineKeyboardButton("📤 Compartir invitación", url=compartir)]))


# ---------- perfil ----------

def _quien(sobre: str, usuario: dict, pareja: dict | None) -> int | None:
    if sobre == "yo" or (sobre == "pareja" and pareja is None):
        return usuario["id"]
    return pareja["id"] if sobre == "pareja" else None


async def guardar_perfil(update: Update, usuario: dict, datos: list[dict]) -> None:
    pareja = finanzas.otro_usuario(usuario["id"])
    ids, lineas = [], []
    for d in datos:
        uid = _quien(d["sobre"], usuario, pareja)
        ids.append(perfil.guardar(uid, d["categoria"], d["dato"], usuario["id"]))
        sobre = ("De los dos" if uid is None else "Sobre ti" if uid == usuario["id"]
                 else f"Sobre {escape(primer_nombre(pareja))}")
        lineas.append(f"{perfil.CATEGORIAS[d['categoria']].split()[0]} {sobre}: {escape(d['dato'])}")
    await update.effective_message.reply_text(
        "💝 Anotado, no se me olvida:\n" + "\n".join(lineas), parse_mode="HTML",
        reply_markup=teclado([boton("↩️ Deshacer", "borrar:perfil:" + ",".join(map(str, ids)))]))


def _texto_perfil(filas: list[dict], usuario: dict) -> str:
    bloques, actual, lineas = [], object(), []
    for f in filas:
        if f["usuario_id"] != actual:
            if lineas:
                bloques.append("\n".join(lineas))
            actual = f["usuario_id"]
            titulo = ("👥 <b>De los dos</b>" if actual is None
                      else f"👤 <b>{escape(f['nombre'])}</b>" + (" (tú)" if actual == usuario["id"] else ""))
            lineas, categoria = [titulo], None
        if f["categoria"] != categoria:
            categoria = f["categoria"]
            lineas.append(f"  {perfil.CATEGORIAS[categoria]}")
        lineas.append(f"    • {escape(f['dato'])}")
    if lineas:
        bloques.append("\n".join(lineas))
    return "\n\n".join(bloques)


async def ver_perfil(update: Update, usuario: dict, filtro: dict | None = None) -> None:
    pareja = finanzas.otro_usuario(usuario["id"])
    filtro = filtro or {"sobre": "todos", "categoria": "todas"}
    categoria = None if filtro["categoria"] == "todas" else filtro["categoria"]
    if filtro["sobre"] == "todos":
        filas = perfil.listar(categoria=categoria)
    elif filtro["sobre"] == "ambos":
        filas = perfil.listar(solo_de_los_dos=True, categoria=categoria)
    else:
        filas = perfil.listar(_quien(filtro["sobre"], usuario, pareja), categoria=categoria)
    if not filas:
        await update.effective_message.reply_text(
            "🤔 Todavía no sé eso. Cuéntenme cosas, tipo:\n"
            "<i>a la Cami le encanta el sushi</i> · <i>mi horario es de 9 a 18</i> · "
            "<i>soñamos con ir a Japón</i>", parse_mode="HTML")
        return
    await update.effective_message.reply_text("💝 <b>Lo que sé de ustedes</b>\n\n" + _texto_perfil(filas, usuario),
                                              parse_mode="HTML")


async def cmd_perfil(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usuario = await comun.usuario_o_aviso(update)
    if usuario is None:
        return
    await ver_perfil(update, usuario)
    filas = perfil.listar()
    if filas:
        botones = [[boton(f"🗑️ {f['dato'][:40]}", f"borrar:perfil:{f['id']}")] for f in filas[:20]]
        await update.effective_message.reply_text("¿Algo ya no corre? Tócalo para borrarlo:",
                                                  reply_markup=teclado(*botones))


# ---------- ideas ----------

def _estacion(mes: int) -> str:
    return {12: "verano", 1: "verano", 2: "verano", 3: "otoño", 4: "otoño", 5: "otoño",
            6: "invierno", 7: "invierno", 8: "invierno"}.get(mes, "primavera")


def _contexto_ideas(usuario: dict, recientes: list[str]) -> str:
    pareja = finanzas.otro_usuario(usuario["id"])
    hoy = finanzas.hoy()
    lineas = [f"Hoy: {comun.DIAS[hoy.weekday()]} {hoy:%Y-%m-%d} ({_estacion(hoy.month)} en Chile).",
              f"Pide: {usuario['nombre']}" + (f" (su pareja es {pareja['nombre']})" if pareja else "")]
    proximos = feriados.proximos(hoy, dias=30)
    if proximos:
        lineas += ["", "Feriados y fechas especiales del próximo mes:"]
        lineas += [f"- {f['fecha']:%Y-%m-%d} ({comun.DIAS[f['fecha'].weekday()]}): {f['nombre']}" for f in proximos]
    lineas += ["", "Perfil (contexto opcional; usa solo lo relacionado con lo que piden):"]
    filas = perfil.listar()
    lineas += [f"- {f['nombre'] or 'De los dos'} / {f['categoria']}: {f['dato']}" for f in filas] or ["- (vacío)"]
    pendientes = hogar.panoramas_pendientes()
    if pendientes:
        lineas += ["", "Panoramas que ya tienen anotados (puedes sugerir uno si calza con lo que piden):"]
        lineas += [f"- {p['titulo']} ({p['tipo']})" for p in pendientes[:15]]
    if recientes:
        lineas += ["", "Ideas que ya sugeriste hace poco (no las repitas):"] + [f"- {t}" for t in recientes]
    return "\n".join(lineas)


async def armar_ideas(usuario: dict, pedido: str, bot_data: dict, titulo: str = "💡 <b>Ideas para ustedes</b>"):
    """Genera ideas y devuelve (texto HTML, botones para guardarlas). Lanza ErrorInterprete si falla."""
    recientes: list = bot_data.setdefault("ideas_recientes", [])
    resultado = await interprete.generar_ideas(pedido, _contexto_ideas(usuario, recientes))
    ideas = resultado["ideas"][:5]
    recientes.extend(i["titulo"] for i in ideas)
    del recientes[:-20]  # memoria de las últimas 20 ideas
    iid = secrets.token_hex(4)
    bot_data.setdefault("ideas", {})[iid] = ideas
    lineas = [titulo, ""]
    for i, idea in enumerate(ideas, 1):
        lineas.append(f"{i}. {EMOJI_PANORAMA[idea['tipo']]} <b>{escape(idea['titulo'])}</b>\n   {escape(idea['descripcion'])}")
    lineas += ["", escape(resultado["comentario"])]
    if not perfil.listar():
        lineas += ["", "<i>Tip: mientras más me cuenten de sus gustos, mejores ideas les doy 😉</i>"]
    botones = [[boton(f"📍 Guardar {i}. {idea['titulo'][:30]}", f"idea:{iid}:{i - 1}")] for i, idea in enumerate(ideas, 1)]
    return "\n".join(lineas), teclado(*botones)


async def pedir_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE, usuario: dict, pedido: str) -> None:
    if not comun.es_grupo(update):
        await update.effective_chat.send_action("typing")
    try:
        texto, botones = await armar_ideas(usuario, pedido, context.bot_data)
    except interprete.ErrorInterprete as e:
        await update.effective_message.reply_text(str(e))
        return
    await update.effective_message.reply_text(texto, parse_mode="HTML", reply_markup=botones)


# ---------- automáticos: pregunta pimpolla y fechas especiales ----------

async def enviar_pregunta(app, usuario: dict) -> bool:
    """Le manda a `usuario` una pregunta que no le haya hecho antes. Devuelve False si ya se acabaron."""
    hechas = hogar.envios_con_prefijo(f"pregunta-{usuario['id']}-")
    pendientes = [i for i in range(len(preguntas.PREGUNTAS)) if f"pregunta-{usuario['id']}-{i}" not in hechas]
    if not pendientes:
        return False
    indice = pendientes[0]
    hogar.reservar_envio(f"pregunta-{usuario['id']}-{indice}")
    pregunta = preguntas.PREGUNTAS[indice]
    app.user_data[usuario["telegram_id"]]["esperando"] = {"tipo": "pregunta", "pregunta": pregunta}
    await app.bot.send_message(
        usuario["telegram_id"],
        f"💬 <b>Pregunta pimpolla</b>\n{escape(pregunta)}\n\n<i>Respóndeme normal y lo guardo en tu perfil "
        "para darles mejores ideas (o escribe <b>saltar</b>).</i>",
        parse_mode="HTML", reply_markup=ForceReply(input_field_placeholder="Tu respuesta…"))
    return True


async def cmd_pregunta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usuario = await comun.usuario_o_aviso(update)
    if usuario is None:
        return
    if not await enviar_pregunta(context.application, usuario):
        await update.effective_message.reply_text("🙌 ¡Ya te hice todas las preguntas que tenía!")


def _boton_pedir_ideas(bot_data: dict, texto: str, pedido: str):
    clave = secrets.token_hex(4)
    bot_data.setdefault("pedidos_ideas", {})[clave] = pedido
    return boton(texto, f"pideas:{clave}")


async def cb_pedir_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    usuario = await comun.usuario_de_boton(query)
    if usuario is None:
        return
    pedido = context.bot_data.get("pedidos_ideas", {}).get(query.data.split(":")[1])
    await query.answer()
    await pedir_ideas(update, context, usuario, pedido or "ideas para hacer juntos")


async def cmd_feriados(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await comun.usuario_o_aviso(update) is None:
        return
    lista = feriados.proximos(finanzas.hoy(), dias=120)
    lineas = ["🗓️ <b>Feriados y fechas especiales</b> (próximos 4 meses)", ""]
    for f in lista:
        emoji = "🇨🇱" if f["feriado"] else "💝"
        extra = " · <i>¡finde largo!</i>" if f["feriado"] and feriados.es_finde_largo(f["fecha"]) else ""
        lineas.append(f"{emoji} {comun.DIAS[f['fecha'].weekday()]} {comun.formatear_fecha(f['fecha'])}: "
                      f"{escape(f['nombre'])}{extra}")
    lineas += ["", "<i>¿Cumpleaños, aniversario o el santo de alguien? Díganme, tipo: "
                   "«el santo de la Cami es el 26 de julio», y les aviso con ideas una semana antes 😉</i>"]
    await update.effective_message.reply_text("\n".join(lineas), parse_mode="HTML")


async def avisar_feriados(app) -> None:
    """Avisa la víspera de un feriado y, una semana antes, de las fechas especiales (con ideas de regalo)."""
    hoy = finanzas.hoy()
    usuarios = finanzas.todos_los_usuarios()
    for f in feriados.proximos(hoy, dias=7):
        dias = (f["fecha"] - hoy).days
        if f["feriado"] and dias == 1 and hogar.reservar_envio(f"feriado-{f['fecha']}"):
            largo = feriados.es_finde_largo(f["fecha"])
            texto = (f"🇨🇱 Mañana es feriado: <b>{escape(f['nombre'])}</b>"
                     + ("\n¡Se viene finde largo! 🙌" if largo else ""))
            pedido = (f"panoramas para el {'fin de semana largo' if largo else 'feriado'} de {f['nombre']} "
                      f"({f['fecha']:%Y-%m-%d})")
            for u in usuarios:
                try:
                    await app.bot.send_message(u["telegram_id"], texto, parse_mode="HTML", reply_markup=teclado(
                        [_boton_pedir_ideas(app.bot_data, "💡 Ideas para el feriado", pedido)]))
                except Exception:
                    log.warning("No pude avisar el feriado a %s", u["nombre"])
        if not f["feriado"] and dias == 7 and hogar.reservar_envio(f"especial-{f['fecha']}"):
            for u in usuarios:
                try:
                    if f["regalo"]:  # San Valentín / Navidad: ideas en privado para sorprender a la pareja
                        pareja = finanzas.otro_usuario(u["id"])
                        para = f" para {pareja['nombre']}" if pareja else ""
                        texto, botones = await armar_ideas(
                            u, f"ideas de regalo o plan{para} por {f['nombre']} ({f['fecha']:%Y-%m-%d})",
                            app.bot_data, titulo=f"💝 <b>En una semana es {escape(f['nombre'])}</b>\n"
                                                 "Ideas para ir preparando (esto lo ves solo tú 🤫):")
                        await app.bot.send_message(u["telegram_id"], texto, parse_mode="HTML", reply_markup=botones)
                    else:
                        await app.bot.send_message(
                            u["telegram_id"],
                            f"💝 El {comun.DIAS[f['fecha'].weekday()]} {comun.formatear_fecha(f['fecha'])} es "
                            f"<b>{escape(f['nombre'])}</b>, ¡que no se les pase! 😉", parse_mode="HTML")
                except Exception:
                    log.exception("No pude avisar %s a %s", f["nombre"], u["nombre"])
    # Navidad se avisa con más tiempo (además del aviso de la semana antes, para los regalos).
    navidad = date(hoy.year, 12, 25)
    if (navidad - hoy).days == 21 and hogar.reservar_envio(f"navidad-{hoy.year}"):
        for u in usuarios:
            try:
                await app.bot.send_message(
                    u["telegram_id"], "🎄 ¡Quedan 3 semanas para Navidad! ¿Partimos con los regalos?",
                    reply_markup=teclado([_boton_pedir_ideas(app.bot_data, "🎁 Ideas de regalo para mi pareja",
                                                             "ideas de regalo de Navidad para mi pareja")]))
            except Exception:
                log.warning("No pude avisar Navidad a %s", u["nombre"])


async def avisar_fechas_especiales(app) -> None:
    """Una semana antes de un cumpleaños/aniversario, manda ideas de regalo en privado (sorpresa)."""
    en_una_semana = finanzas.hoy() + timedelta(days=7)
    for r in hogar.fechas_especiales_el(en_una_semana):
        if not hogar.reservar_envio(f"anticipo-{r['id']}-{en_una_semana}"):
            continue
        texto_rec = r["texto"].lower()
        for usuario in finanzas.todos_los_usuarios():
            if primer_nombre(usuario).lower() in texto_rec:
                continue  # es su cumpleaños: no se le arruina la sorpresa
            try:
                texto, botones = await armar_ideas(
                    usuario, f"ideas de regalo o celebración para: {r['texto']} (es en una semana)", app.bot_data,
                    titulo=f"🎁 <b>En una semana: {escape(r['texto'])}</b>\nIdeas para ir preparando "
                           "(esto lo ves solo tú 🤫):")
                await app.bot.send_message(usuario["telegram_id"], texto, parse_mode="HTML", reply_markup=botones)
            except Exception:
                log.exception("No pude mandar ideas anticipadas a %s", usuario["nombre"])


async def cb_idea(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    usuario = await comun.usuario_de_boton(query)
    if usuario is None:
        return
    _, iid, indice = query.data.split(":")
    ideas = context.bot_data.get("ideas", {}).get(iid)
    if ideas is None:
        await query.answer("Estas ideas ya caducaron, pide otras 🙏", show_alert=True)
        return
    idea = ideas[int(indice)]
    hogar.agregar_panoramas([(idea["titulo"], idea["tipo"], idea["descripcion"])], usuario["id"])
    await query.answer(f"Guardado en panoramas: {idea['titulo']}"[:190])


async def cmd_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usuario = await comun.usuario_o_aviso(update)
    if usuario is None:
        return
    await pedir_ideas(update, context, usuario, " ".join(context.args or []))
