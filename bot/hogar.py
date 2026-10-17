"""Metas de ahorro, lista de compras, recordatorios, panoramas y notas de la casa."""

import asyncio
import logging
import secrets
from datetime import date
from html import escape

from telegram import Bot, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import comun, interprete, plata
from bot.comun import boton, teclado
from core import finanzas, hogar, metas, parser, perfil
from core.finanzas import formatear_clp

log = logging.getLogger(__name__)

EMOJI_PANORAMA = {"restaurante": "🍽️", "lugar": "📍", "pelicula": "🎬", "serie": "📺",
                  "actividad": "🎯", "viaje": "✈️", "otro": "✨"}
REPETIR_TEXTO = {"diario": "todos los días", "semanal": "todas las semanas",
                 "mensual": "todos los meses", "anual": "todos los años"}


def _barra(porcentaje: float, largo: int = 10) -> str:
    llenos = round(porcentaje / 100 * largo)
    return "▓" * llenos + "░" * (largo - llenos)


def _meses_entre(desde: date, hasta: date) -> int:
    return (hasta.year - desde.year) * 12 + hasta.month - desde.month


# ---------- metas ----------

def _vista_metas() -> tuple[str, object]:
    lista = metas.listar()
    if not lista:
        texto = ("🎯 <b>Metas</b>\n\nTodavía no tienen metas. Toquen ➕ o escríbanme algo como:\n"
                 "<i>queremos juntar 2 millones para México en julio</i>")
        return texto, teclado([boton("➕ Nueva meta", "meta:nueva")])

    hoy = finanzas.hoy()
    bloques, filas = [], []
    for m in lista:
        falta = max(m["monto_objetivo"] - m["ahorrado"], 0)
        lineas = [f"{m['emoji']} <b>{escape(m['nombre'])}</b>",
                  f"{_barra(m['porcentaje'])} {m['porcentaje']}%",
                  f"{formatear_clp(m['ahorrado'])} de {formatear_clp(m['monto_objetivo'])}"]
        if falta == 0:
            lineas.append("🎉 ¡Meta cumplida!")
            filas.append([boton(f"🗄️ Archivar {m['nombre']}", f"meta:arch:{m['id']}")])
        else:
            lineas[-1] += f" · faltan {formatear_clp(falta)}"
            if m["fecha_objetivo"]:
                meses = _meses_entre(hoy, m["fecha_objetivo"])
                cuando = f"📅 {comun.MESES[m['fecha_objetivo'].month - 1]} {m['fecha_objetivo'].year}"
                lineas.append(f"{cuando} · ~{formatear_clp(falta / meses)} al mes" if meses > 0
                              else f"{cuando} · ¡ya llegó la fecha!")
            filas.append([boton(f"💰 Aportar a {m['nombre']}", f"meta:ap:{m['id']}")])
        bloques.append("\n".join(lineas))
    filas.append([boton("➕ Nueva meta", "meta:nueva")])
    return "🎯 <b>Metas</b>\n\n" + "\n\n".join(bloques), teclado(*filas)


async def cmd_metas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await comun.usuario_o_aviso(update) is None:
        return
    texto, botones = _vista_metas()
    await update.effective_message.reply_text(texto, parse_mode="HTML", reply_markup=botones)


async def cb_meta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if await comun.usuario_de_boton(query) is None:
        return
    partes = query.data.split(":")
    if partes[1] == "nueva":
        context.user_data["esperando"] = {"tipo": "meta_nueva"}
        await query.answer()
        await query.message.reply_text(
            "🎯 ¡Bacán! Cuéntame la meta: qué es, cuánto necesitan y para cuándo.\n"
            "Ej: <i>viaje a México, 2 millones para julio</i>\n(o escribe <i>cancelar</i>)", parse_mode="HTML")
    elif partes[1] == "ap":
        meta = metas.obtener(int(partes[2]))
        if meta is None:
            await query.answer("Esa meta ya no está activa", show_alert=True)
            return
        context.user_data["esperando"] = {"tipo": "aporte", "meta_id": meta["id"]}
        await query.answer()
        await query.message.reply_text(
            f"💰 ¿Cuánto apartaron para {meta['emoji']} <b>{escape(meta['nombre'])}</b>?\n"
            "Ej: <i>50000</i> o <i>50 lucas</i> (o <i>cancelar</i>)", parse_mode="HTML")
    elif partes[1] == "arch":
        metas.archivar(int(partes[2]))
        await query.answer("Archivada 🎉")
        texto, botones = _vista_metas()
        await query.edit_message_text(texto, parse_mode="HTML", reply_markup=botones)


async def _crear_metas(update: Update, usuario: dict, nuevas: list[dict]) -> None:
    for m in nuevas:
        meta_id = metas.crear(m["nombre"], m["emoji"], m["monto_objetivo"], m["fecha_objetivo"], usuario["id"])
        cuando = (f"\n📅 Para {comun.MESES[m['fecha_objetivo'].month - 1]} {m['fecha_objetivo'].year}"
                  if m["fecha_objetivo"] else "")
        await update.effective_message.reply_text(
            f"🎯 ¡Meta creada!\n\n{m['emoji']} <b>{escape(m['nombre'])}</b>\n"
            f"Objetivo: {formatear_clp(m['monto_objetivo'])}{cuando}\n\n"
            "Cuando aparten plata, díganme <i>aparté 50 lucas para esto</i> o usen /metas",
            parse_mode="HTML",
            reply_markup=teclado([boton("💰 Aportar", f"meta:ap:{meta_id}"),
                                  boton("↩️ Deshacer", f"borrar:meta:{meta_id}")]))


async def _aportar(update: Update, context, usuario: dict, meta: dict, monto) -> None:
    aporte_id = metas.aportar(meta["id"], usuario["id"], monto)
    meta = metas.obtener(meta["id"])
    await update.effective_message.reply_text(
        f"💰 ¡Vamos! {formatear_clp(monto)} para {meta['emoji']} <b>{escape(meta['nombre'])}</b>\n"
        f"{_barra(meta['porcentaje'])} {meta['porcentaje']}% · {formatear_clp(meta['ahorrado'])} "
        f"de {formatear_clp(meta['monto_objetivo'])}",
        parse_mode="HTML", reply_markup=teclado([boton("↩️ Deshacer", f"borrar:aporte:{aporte_id}")]))
    await comun.avisar_pareja(
        context.bot, update, usuario,
        f"🔔 <b>{escape(comun.primer_nombre(usuario))}</b> aportó {formatear_clp(monto)} a "
        f"{meta['emoji']} {escape(meta['nombre'])} ({meta['porcentaje']}%) 💪")


# ---------- lista de compras ----------

def _vista_lista() -> tuple[str, object]:
    items = hogar.lista_compras()
    if not items:
        return "🛒 La lista está vacía 🙌\nAgrega cosas escribiendo <i>agrega leche y pan</i>", None
    pendientes = sum(not i["comprado"] for i in items)
    filas = [[boton(("✅ " if i["comprado"] else "⬜ ") + i["nombre"]
                    + (f" ({i['cantidad']})" if i["cantidad"] else ""), f"lista:t:{i['id']}")] for i in items]
    filas.append([boton("🧾 Terminé de comprar", "lista:fin")])
    return (f"🛒 <b>Lista de compras</b> · {pendientes} pendiente{'s' if pendientes != 1 else ''}\n"
            "Toca lo que ya echaste al carro 👇"), teclado(*filas)


async def cmd_lista(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await comun.usuario_o_aviso(update) is None:
        return
    texto, botones = _vista_lista()
    await update.effective_message.reply_text(texto, parse_mode="HTML", reply_markup=botones)


async def cb_lista(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if await comun.usuario_de_boton(query) is None:
        return
    partes = query.data.split(":")
    if partes[1] == "ver":
        await query.answer()
        texto, botones = _vista_lista()
        await query.message.reply_text(texto, parse_mode="HTML", reply_markup=botones)
        return
    if partes[1] == "t":
        hogar.alternar_item(int(partes[2]))
        await query.answer()
        texto, botones = _vista_lista()
        await query.edit_message_text(texto, parse_mode="HTML", reply_markup=botones)
        return
    # Terminé de comprar
    comprados = [i["nombre"] for i in hogar.lista_compras() if i["comprado"]]
    if not comprados:
        await query.answer("Marca primero lo que compraste ✅", show_alert=True)
        return
    context.user_data["esperando"] = {"tipo": "total_compra", "items": comprados}
    await query.answer()
    await query.message.reply_text(
        "🧾 ¿Cuánto salió en total? Lo anoto como gasto de Supermercado.\n"
        "Ej: <i>23990</i> o <i>45 lucas a medias</i>. Si no quieres anotarlo, escribe <i>no</i>.",
        parse_mode="HTML")


async def _agregar_compras(update: Update, usuario: dict, compras: list[dict]) -> None:
    ids = hogar.agregar_items([(c["nombre"], c["cantidad"]) for c in compras], usuario["id"])
    nombres = ", ".join(c["nombre"] + (f" ({c['cantidad']})" if c["cantidad"] else "") for c in compras)
    await update.effective_message.reply_text(
        f"🛒 Agregué a la lista: {escape(nombres)}",
        reply_markup=teclado([boton("📋 Ver lista", "lista:ver"),
                              boton("↩️ Deshacer", "borrar:item:" + ",".join(map(str, ids)))]))


# ---------- recordatorios ----------

def _cuando(momento) -> str:
    """'mañana a las 08:00' / 'el domingo 1 de noviembre a las 09:00'."""
    texto = comun.formatear_fecha_hora(momento)
    return texto if texto.startswith(("hoy", "mañana")) else f"el {texto}"


async def _crear_recordatorios(update: Update, context, usuario: dict, recordatorios: list[dict]) -> None:
    ahora = finanzas.ahora()
    for r in recordatorios:
        if r["fecha_hora"] <= ahora:
            await update.effective_message.reply_text(
                f"🤔 Esa fecha ({comun.formatear_fecha_hora(r['fecha_hora'])}) ya pasó. ¿Me la dices de nuevo?")
            continue
        para = None if r["para"] == "ambos" else usuario["id"]
        rec_id = hogar.crear_recordatorio(r["texto"], r["fecha_hora"], r["repetir"], para, usuario["id"])
        repite = f" ({REPETIR_TEXTO[r['repetir']]})" if r["repetir"] != "nunca" else ""
        quien = "👥 Para los dos" if para is None else "👤 Solo para ti"
        await update.effective_message.reply_text(
            f"⏰ Listo, aviso {_cuando(r['fecha_hora'])}{repite}\n"
            f"📝 {escape(r['texto'])}\n{quien}",
            parse_mode="HTML", reply_markup=teclado([boton("↩️ Deshacer", f"borrar:rec:{rec_id}")]))
        if para is None:
            await comun.avisar_pareja(
                context.bot, update, usuario,
                f"🔔 <b>{escape(comun.primer_nombre(usuario))}</b> agregó un recordatorio para los dos:\n"
                f"⏰ {escape(r['texto'])} · {comun.formatear_fecha_hora(r['fecha_hora'])}{repite}")


async def cmd_recordatorios(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usuario = await comun.usuario_o_aviso(update)
    if usuario is None:
        return
    lista = hogar.proximos_recordatorios(usuario["id"])
    if not lista:
        await update.effective_message.reply_text(
            "⏰ No hay recordatorios pendientes.\nPídeme uno, tipo: <i>recuérdame pagar la luz el 5 a las 10</i>",
            parse_mode="HTML")
        return
    lineas = ["⏰ <b>Próximos recordatorios</b>", ""]
    filas = []
    for r in lista:
        repite = f" 🔁 {REPETIR_TEXTO[r['repetir']]}" if r["repetir"] != "nunca" else ""
        quien = "👥" if r["para_usuario_id"] is None else "👤"
        lineas.append(f"{quien} <b>{escape(r['texto'])}</b>\n    {comun.formatear_fecha_hora(r['fecha_hora'])}{repite}")
        filas.append([boton(f"🗑️ {r['texto'][:40]}", f"borrar:rec:{r['id']}")])
    await update.effective_message.reply_text("\n".join(lineas), parse_mode="HTML", reply_markup=teclado(*filas))


async def bucle_recordatorios(bot: Bot) -> None:
    """Revisa cada 30 segundos si hay recordatorios que enviar."""
    while True:
        try:
            for r in await asyncio.to_thread(hogar.tomar_recordatorios_vencidos):
                repite = f"\n<i>🔁 Te lo vuelvo a recordar {REPETIR_TEXTO[r['repetir']]}</i>" \
                    if r["repetir"] != "nunca" else ""
                for telegram_id in r["destinos"]:
                    try:
                        await bot.send_message(telegram_id, f"⏰ <b>¡Recordatorio!</b>\n{escape(r['texto'])}{repite}",
                                               parse_mode="HTML")
                    except TelegramError:
                        log.warning("No pude enviar el recordatorio %s a %s", r["id"], telegram_id)
        except Exception:
            log.exception("Error revisando recordatorios")
        await asyncio.sleep(30)


# ---------- panoramas ----------

def _texto_panorama(p: dict) -> str:
    notas = f"\n<i>{escape(p['notas'])}</i>" if p["notas"] else ""
    return f"{EMOJI_PANORAMA[p['tipo']]} <b>{escape(p['titulo'])}</b>{notas}"


async def _agregar_panoramas(update: Update, usuario: dict, panoramas: list[dict]) -> None:
    ids = hogar.agregar_panoramas([(p["titulo"], p["tipo"], p["notas"]) for p in panoramas], usuario["id"])
    lineas = "\n".join(_texto_panorama(p) for p in panoramas)
    await update.effective_message.reply_text(
        f"📍 Anotado en panoramas pendientes:\n{lineas}", parse_mode="HTML",
        reply_markup=teclado([boton("🎲 Sortear uno", "pan:sort:cualquiera"),
                              boton("↩️ Deshacer", "borrar:pan:" + ",".join(map(str, ids)))]))


async def _sugerir_panorama(mensaje, tipo: str, editar: bool = False) -> None:
    filtro = None if tipo == "cualquiera" else tipo
    p = hogar.sortear_panorama(filtro)
    if p is None:
        texto = ("🤷 No tengo panoramas anotados" + (f" de tipo {tipo}" if filtro else "") + ".\n"
                 "Anoten uno, tipo: <i>anota el restaurante Liguria para ir</i>")
        botones = None
    else:
        texto = f"🎲 ¿Qué tal…\n\n{_texto_panorama(p)}"
        botones = teclado([boton("🎲 Otro", f"pan:sort:{tipo}"), boton("✅ ¡Lo hicimos!", f"pan:hecho:{p['id']}")])
    if editar:
        await mensaje.edit_text(texto, parse_mode="HTML", reply_markup=botones)
    else:
        await mensaje.reply_text(texto, parse_mode="HTML", reply_markup=botones)


async def cmd_panoramas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await comun.usuario_o_aviso(update) is None:
        return
    lista = hogar.panoramas_pendientes()
    if not lista:
        await update.effective_message.reply_text(
            "📍 No hay panoramas pendientes.\nAnoten uno: <i>hay que ir al restaurante Liguria</i>",
            parse_mode="HTML")
        return
    lineas = ["📍 <b>Panoramas pendientes</b>", ""] + [_texto_panorama(p) for p in lista]
    filas = [[boton(f"✅ {p['titulo'][:40]}", f"pan:hecho:{p['id']}")] for p in lista[:12]]
    filas.insert(0, [boton("🎲 Sortear uno", "pan:sort:cualquiera")])
    await update.effective_message.reply_text("\n".join(lineas), parse_mode="HTML", reply_markup=teclado(*filas))


async def cb_panorama(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if await comun.usuario_de_boton(query) is None:
        return
    _, accion, valor = query.data.split(":")
    if accion == "sort":
        await query.answer("🎲")
        await _sugerir_panorama(query.message, valor, editar=query.message.text.startswith("🎲"))
    else:
        hecho = hogar.marcar_panorama_hecho(int(valor))
        await query.answer("¡Bacán! 🎉" if hecho else "Ya estaba marcado")
        if hecho:
            await query.message.reply_text(f"✅ Tachado: <b>{escape(hecho['titulo'])}</b>. ¡Que lo hayan pasado bacán! 🎉",
                                           parse_mode="HTML")


# ---------- notas de la casa ----------

async def _guardar_notas(update: Update, usuario: dict, notas: list[dict]) -> None:
    for n in notas:
        nota_id = hogar.guardar_nota(n["clave"], n["valor"], usuario["id"])
        await update.effective_message.reply_text(
            f"📌 Guardado: <b>{escape(n['clave'])}</b> → {escape(n['valor'])}", parse_mode="HTML",
            reply_markup=teclado([boton("↩️ Deshacer", f"borrar:nota:{nota_id}")]))


async def _buscar_notas(update: Update, busqueda: str) -> None:
    encontradas = hogar.buscar_notas(busqueda)
    if not encontradas:
        await update.effective_message.reply_text("🤔 No tengo nada guardado sobre eso. Revisa /notas")
        return
    lineas = [f"📌 <b>{escape(n['clave'])}</b>: {escape(n['valor'])}" for n in encontradas]
    await update.effective_message.reply_text("\n".join(lineas), parse_mode="HTML")


async def cmd_notas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await comun.usuario_o_aviso(update) is None:
        return
    notas = hogar.todas_las_notas()
    if not notas:
        await update.effective_message.reply_text(
            "📌 No hay datos guardados.\nGuarda uno: <i>la clave del wifi es pimpollo123</i>", parse_mode="HTML")
        return
    lineas = ["📌 <b>Datos de la casa</b>", ""] + [f"<b>{escape(n['clave'])}</b>: {escape(n['valor'])}" for n in notas]
    filas = [[boton(f"🗑️ {n['clave'][:40]}", f"borrar:nota:{n['id']}")] for n in notas[:15]]
    await update.effective_message.reply_text("\n".join(lineas), parse_mode="HTML", reply_markup=teclado(*filas))


# ---------- tareas de la casa ----------

def _cuando_tarea(proxima: date) -> str:
    dias = (proxima - finanzas.hoy()).days
    if dias < 0:
        return f"⚠️ atrasada {-dias} día{'s' if dias != -1 else ''}"
    return comun.formatear_fecha(proxima)


async def _crear_tareas(update: Update, context, usuario: dict, tareas: list[dict]) -> None:
    pareja = finanzas.otro_usuario(usuario["id"])
    for t in tareas:
        quien = t["quien"] if pareja else "yo"
        responsable = pareja if quien == "pareja" else usuario
        tarea_id = hogar.crear_tarea(t["nombre"], t["cada_dias"], responsable["id"], quien == "turnos",
                                     finanzas.hoy(), usuario["id"])
        modo = (f"por turnos (parte {escape(comun.primer_nombre(responsable))})" if quien == "turnos"
                else f"la hace {escape(comun.primer_nombre(responsable))}")
        frecuencia = "todos los días" if t["cada_dias"] == 1 else f"cada {t['cada_dias']} días"
        await update.effective_message.reply_text(
            f"🧹 Tarea creada: <b>{escape(t['nombre'])}</b>\n🔁 {frecuencia} · {modo}\n"
            "Les aviso a las 9:00 el día que toca 😉", parse_mode="HTML",
            reply_markup=teclado([boton("📋 Ver tareas", "tarea:ver"), boton("↩️ Deshacer", f"borrar:tarea:{tarea_id}")]))


async def cmd_tareas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await comun.usuario_o_aviso(update) is None:
        return
    tareas = hogar.listar_tareas()
    if not tareas:
        await update.effective_message.reply_text(
            "🧹 No hay tareas.\nCrea una: <i>la basura se saca cada 3 días, nos turnamos</i>", parse_mode="HTML")
        return
    lineas = ["🧹 <b>Tareas de la casa</b>", ""]
    filas = []
    for t in tareas:
        lineas.append(f"<b>{escape(t['nombre'])}</b> · le toca a {escape(t['responsable'].split()[0])} · "
                      f"{_cuando_tarea(t['proxima'])}")
        filas.append([boton(f"✅ {t['nombre'][:30]}", f"tarea:hecha:{t['id']}"), boton("🗑️", f"borrar:tarea:{t['id']}")])
    await update.effective_message.reply_text("\n".join(lineas), parse_mode="HTML", reply_markup=teclado(*filas))


async def _tarea_hecha(update: Update, context, usuario: dict, tarea_id: int) -> None:
    tarea = hogar.marcar_tarea_hecha(tarea_id, usuario["id"], finanzas.hoy())
    if tarea is None:
        await update.effective_message.reply_text("🤔 Esa tarea ya no existe.")
        return
    siguiente = f"Próxima: {comun.formatear_fecha(tarea['proxima'])}, le toca a {escape(tarea['responsable'].split()[0])}"
    await update.effective_message.reply_text(f"✅ ¡Bien ahí! <b>{escape(tarea['nombre'])}</b> lista 💪\n{siguiente}",
                                              parse_mode="HTML")
    await comun.avisar_pareja(context.bot, update, usuario,
                              f"🔔 <b>{escape(comun.primer_nombre(usuario))}</b> hizo: {escape(tarea['nombre'])} ✅\n{siguiente}")


async def cb_tarea(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    usuario = await comun.usuario_de_boton(query)
    if usuario is None:
        return
    await query.answer()
    partes = query.data.split(":")
    if partes[1] == "ver":
        await cmd_tareas(update, context)
    else:
        await _tarea_hecha(update, context, usuario, int(partes[2]))


async def avisar_tareas_del_dia(bot: Bot) -> None:
    """Le avisa a cada responsable las tareas que le tocan hoy (o están atrasadas)."""
    hoy = finanzas.hoy()
    for t in await asyncio.to_thread(hogar.listar_tareas):
        if t["proxima"] > hoy:
            continue
        try:
            await bot.send_message(
                t["responsable_telegram_id"],
                f"🧹 Hoy te toca: <b>{escape(t['nombre'])}</b> ({_cuando_tarea(t['proxima'])})",
                parse_mode="HTML", reply_markup=teclado([boton("✅ ¡Listo!", f"tarea:hecha:{t['id']}")]))
        except TelegramError:
            log.warning("No pude avisar la tarea %s", t["id"])


# ---------- menú semanal ----------

def _menus(context) -> dict:
    return context.bot_data.setdefault("menus", {})


async def _menu_semanal(update: Update, context, preferencias: str, mensaje=None) -> None:
    if not comun.es_grupo(update):
        await update.effective_chat.send_action("typing")
    try:
        menu = await interprete.generar_menu(preferencias)
    except interprete.ErrorInterprete as e:
        await update.effective_message.reply_text(str(e))
        return
    mid = secrets.token_hex(4)
    _menus(context)[mid] = {"ingredientes": menu["ingredientes"], "preferencias": preferencias}
    lineas = ["🍽️ <b>Menú de la semana</b>" + (f" ({escape(preferencias)})" if preferencias else ""), ""]
    lineas += [f"<b>{escape(p['dia'])}:</b> {escape(p['plato'])}" for p in menu["platos"]]
    lineas += ["", f"🛒 {len(menu['ingredientes'])} ingredientes para comprar", "", escape(menu["comentario"])]
    await update.effective_message.reply_text(
        "\n".join(lineas), parse_mode="HTML",
        reply_markup=teclado([boton("🛒 Agregar ingredientes a la lista", f"menu:lista:{mid}")],
                             [boton("🔄 Otro menú", f"menu:otro:{mid}")]))


async def cb_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    usuario = await comun.usuario_de_boton(query)
    if usuario is None:
        return
    _, accion, mid = query.data.split(":")
    menu = _menus(context).get(mid)
    if menu is None:
        await query.answer("Ese menú ya caducó, pide otro 🙏", show_alert=True)
        return
    await query.answer()
    if accion == "otro":
        await _menu_semanal(update, context, menu["preferencias"])
        return
    _menus(context).pop(mid)
    hogar.agregar_items([(i["nombre"], i["cantidad"]) for i in menu["ingredientes"]], usuario["id"])
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(f"🛒 Agregué {len(menu['ingredientes'])} ingredientes a la lista 👌",
                                   reply_markup=teclado([boton("📋 Ver lista", "lista:ver")]))


# ---------- deshacer / borrar genérico ----------

async def cb_borrar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    usuario = await comun.usuario_de_boton(query)
    if usuario is None:
        return
    _, tipo, ids_txt = query.data.split(":")
    ids = [int(x) for x in ids_txt.split(",")]
    if tipo == "meta":
        ok = metas.borrar(ids[0], usuario["id"])
    elif tipo == "aporte":
        ok = metas.borrar_aporte(ids[0], usuario["id"])
    elif tipo == "item":
        ok = hogar.borrar_items(ids) > 0
    elif tipo == "rec":
        ok = hogar.borrar_recordatorio(ids[0])
    elif tipo == "pan":
        ok = hogar.borrar_panoramas(ids) > 0
    elif tipo == "nota":
        ok = hogar.borrar_nota(ids[0])
    elif tipo == "tarea":
        ok = hogar.borrar_tarea(ids[0])
    elif tipo == "perfil":
        ok = sum(perfil.borrar(i) for i in ids) > 0
    else:
        ok = False
    if ok:
        await query.answer("Borrado")
        await query.edit_message_text(f"🗑️ Borrado, como si nada\n\n{query.message.text}", reply_markup=None)
    else:
        await query.answer("Ya no existe (o no lo creaste tú)", show_alert=True)


# ---------- integración con el texto libre ----------

async def manejar_intencion(update: Update, context: ContextTypes.DEFAULT_TYPE, usuario: dict, datos: dict) -> bool:
    """Ejecuta las intenciones de este módulo. Devuelve False si la intención no es de aquí."""
    intencion = datos["intencion"]
    if intencion == "meta_nueva":
        await _crear_metas(update, usuario, datos["metas_nuevas"])
    elif intencion == "meta_aporte":
        por_nombre = {m["nombre"]: m for m in metas.listar()}
        for a in datos["aportes"]:
            await _aportar(update, context, usuario, por_nombre[a["meta"]], a["monto"])
    elif intencion == "ver_metas":
        await cmd_metas(update, context)
    elif intencion == "compras_agregar":
        await _agregar_compras(update, usuario, datos["compras"])
    elif intencion == "ver_lista":
        await cmd_lista(update, context)
    elif intencion == "recordatorio":
        await _crear_recordatorios(update, context, usuario, datos["recordatorios"])
    elif intencion == "ver_recordatorios":
        await cmd_recordatorios(update, context)
    elif intencion == "panorama_agregar":
        await _agregar_panoramas(update, usuario, datos["panoramas"])
    elif intencion == "panorama_sugerir":
        await _sugerir_panorama(update.message, datos["filtro_tipo"])
    elif intencion == "nota_guardar":
        await _guardar_notas(update, usuario, datos["notas"])
    elif intencion == "nota_buscar":
        await _buscar_notas(update, datos["busqueda"] or update.message.text)
    elif intencion == "tarea_nueva":
        await _crear_tareas(update, context, usuario, datos["tareas"])
    elif intencion == "ver_tareas":
        await cmd_tareas(update, context)
    elif intencion == "tarea_hecha":
        tarea = next(t for t in hogar.listar_tareas() if t["nombre"] == datos["busqueda"])
        await _tarea_hecha(update, context, usuario, tarea["id"])
    elif intencion == "menu_semanal":
        await _menu_semanal(update, context, datos["busqueda"])
    else:
        return False
    return True


async def manejar_esperando(update: Update, context: ContextTypes.DEFAULT_TYPE, usuario: dict,
                            esperando: dict) -> dict | None:
    """Responde a una pregunta que el bot hizo antes (aporte, total de compra...).

    Devuelve un dict {"contexto": ...} si el texto debe pasar por la IA con esa pista, o None si ya se resolvió.
    """
    texto = update.message.text.strip()
    if texto.lower() in ("cancelar", "nada", "olvídalo", "olvidalo"):
        await update.effective_message.reply_text("👌 Ya, lo dejamos ahí.")
        return None

    if esperando["tipo"] == "meta_nueva":
        return {"contexto": "El bot le pidió al usuario los datos de una meta de ahorro nueva.",
                "intencion": "meta_nueva"}

    if esperando["tipo"] == "aporte":
        meta = metas.obtener(esperando["meta_id"])
        try:
            monto = parser.interpretar(texto.split(), [], permitir_reparto=False).monto
        except parser.ErrorFormato:
            monto = None
        if meta is None or monto is None:
            context.user_data["esperando"] = esperando  # sigue esperando
            await update.effective_message.reply_text("🤔 No cacho el monto. Escríbelo tipo <i>50000</i> o <i>50 lucas</i>",
                                            parse_mode="HTML")
            return None
        await _aportar(update, context, usuario, meta, monto)
        return None

    if esperando["tipo"] == "total_compra":
        if texto.lower() in ("no", "nop", "no gracias"):
            sacados = hogar.quitar_comprados()
            await update.effective_message.reply_text(f"👌 Saqué de la lista: {escape(', '.join(sacados))}")
            return None
        tokens = comun.sin_marca_prueba(texto).split()
        try:
            datos = parser.interpretar(tokens, finanzas.nombres_categorias("gasto"))
        except parser.ErrorFormato:
            context.user_data["esperando"] = esperando
            await update.effective_message.reply_text("🤔 No cacho el monto. Escríbelo tipo <i>23990</i> o <i>45 lucas a medias</i>",
                                            parse_mode="HTML")
            return None
        mov = {"tipo": "gasto", "monto": datos.monto, "moneda": "CLP", "categoria": "Supermercado",
               "descripcion": ("compras: " + ", ".join(esperando["items"]))[:120], "fecha": finanzas.hoy(),
               "porcentaje_pagador": datos.porcentaje_pagador, "es_prueba": comun.es_prueba(texto)}
        if await plata.guardar_directo(update, context, usuario, mov, texto, "🧾 ¡Compra anotada!") is not None:
            hogar.quitar_comprados()
        return None

    return None
