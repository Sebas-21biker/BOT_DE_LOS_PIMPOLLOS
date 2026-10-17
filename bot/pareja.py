"""Invitación de la pareja, perfil (gustos, sueños, horarios...) e ideas personalizadas."""

import secrets
from html import escape
from urllib.parse import quote

from telegram import InlineKeyboardButton, Update
from telegram.ext import ContextTypes

from bot import comun, interprete
from bot.comun import boton, primer_nombre, teclado
from core import db, finanzas, hogar, perfil

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


def _contexto_ideas(usuario: dict) -> str:
    pareja = finanzas.otro_usuario(usuario["id"])
    hoy = finanzas.hoy()
    lineas = [f"Hoy: {comun.DIAS[hoy.weekday()]} {hoy:%Y-%m-%d} ({_estacion(hoy.month)} en Chile).",
              f"Pide: {usuario['nombre']}" + (f" (su pareja es {pareja['nombre']})" if pareja else ""),
              "", "Perfil:"]
    filas = perfil.listar()
    lineas += [f"- {f['nombre'] or 'De los dos'} / {f['categoria']}: {f['dato']}" for f in filas] or ["- (vacío)"]
    pendientes = hogar.panoramas_pendientes()
    if pendientes:
        lineas += ["", "Panoramas que ya tienen anotados (puedes sugerir uno si calza):"]
        lineas += [f"- {p['titulo']} ({p['tipo']})" for p in pendientes[:15]]
    return "\n".join(lineas)


async def pedir_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE, usuario: dict, pedido: str) -> None:
    if not comun.es_grupo(update):
        await update.effective_chat.send_action("typing")
    try:
        resultado = await interprete.generar_ideas(pedido, _contexto_ideas(usuario))
    except interprete.ErrorInterprete as e:
        await update.effective_message.reply_text(str(e))
        return
    ideas = resultado["ideas"][:5]
    iid = secrets.token_hex(4)
    context.bot_data.setdefault("ideas", {})[iid] = ideas
    lineas = ["💡 <b>Ideas para ustedes</b>", ""]
    for i, idea in enumerate(ideas, 1):
        lineas.append(f"{i}. {EMOJI_PANORAMA[idea['tipo']]} <b>{escape(idea['titulo'])}</b>\n   {escape(idea['descripcion'])}")
    lineas += ["", escape(resultado["comentario"])]
    if not perfil.listar():
        lineas += ["", "<i>Tip: mientras más me cuenten de sus gustos, mejores ideas les doy 😉</i>"]
    botones = [[boton(f"📍 Guardar {i}. {idea['titulo'][:30]}", f"idea:{iid}:{i - 1}")] for i, idea in enumerate(ideas, 1)]
    await update.effective_message.reply_text("\n".join(lineas), parse_mode="HTML", reply_markup=teclado(*botones))


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
