"""Gastos, ingresos, pagos entre la pareja y saldos."""

import secrets
from html import escape

from telegram import Update
from telegram.ext import ContextTypes

from bot import comun
from bot.comun import boton, primer_nombre, teclado
from core import finanzas, parser
from core.finanzas import formatear_clp

# Propuestas interpretadas por la IA que esperan Confirmar / Corregir / Cancelar.
# Se guardan en bot_data (no en user_data) para que en un grupo solo quien escribió pueda confirmar.


def _pendientes(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.bot_data.setdefault("pendientes", {})


def _botones_propuesta(pid: str):
    return teclado([boton("✅ Confirmar", f"conf:{pid}"), boton("✏️ Corregir", f"corr:{pid}"),
                    boton("❌ Cancelar", f"canc:{pid}")])


def _describir_pago(pago: dict, usuario: dict, pareja: dict) -> str:
    de, a = (usuario, pareja) if pago["direccion"] == "yo_pago" else (pareja, usuario)
    lineas = [f"🤝 <b>Pago de {formatear_clp(pago['monto'])}</b>",
              f"{escape(primer_nombre(de))} → {escape(primer_nombre(a))} · {comun.formatear_fecha(pago['fecha'])}",
              "Es para saldar cuentas: no es gasto ni ingreso."]
    if pago.get("es_prueba"):
        lineas.append("🧪 <b>PRUEBA</b>: no cuenta en saldos")
    return "\n".join(lineas)


# ---------- propuestas desde lenguaje natural ----------

async def proponer(update: Update, context: ContextTypes.DEFAULT_TYPE, usuario: dict, datos: dict,
                   mensaje_original: str, es_prueba: bool) -> None:
    """Muestra lo que entendió la IA (movimientos o pagos) con botones para confirmar."""
    await enviar_propuesta(context.bot, context.bot_data, update.effective_chat.id, update.effective_user.id,
                           usuario, datos, mensaje_original, es_prueba)


async def enviar_propuesta(bot, bot_data: dict, chat_id: int, dueno: int, usuario: dict, datos: dict,
                           mensaje_original: str, es_prueba: bool, encabezado: str = "¿Lo anoto así? 👇") -> None:
    """Igual que proponer, pero sin un mensaje al que responder (sirve para pagos detectados automáticamente)."""
    pareja = finanzas.otro_usuario(usuario["id"])
    if datos["intencion"] == "movimientos":
        tipo, items = "movimientos", datos["movimientos"]
        if problema := comun.problema_movimientos(items, pareja):
            await bot.send_message(chat_id, problema)
            return
        cuerpo = "\n\n".join(comun.describir_movimiento({**m, "es_prueba": es_prueba}, usuario, pareja)
                             for m in items)
    else:
        tipo, items = "pagos", datos["pagos"]
        if pareja is None:
            await bot.send_message(chat_id, "Tu pareja todavía no se registra (tiene que tirar un /start) 🤷")
            return
        cuerpo = "\n\n".join(_describir_pago({**p, "es_prueba": es_prueba}, usuario, pareja) for p in items)

    for item in items:
        item["es_prueba"] = es_prueba
    pid = secrets.token_hex(4)
    bot_data.setdefault("pendientes", {})[pid] = {
        "tipo": tipo, "items": items, "mensaje": mensaje_original, "crudo": datos["crudo"],
        "es_prueba": es_prueba, "dueno": dueno}
    await bot.send_message(chat_id, f"{encabezado}\n\n{cuerpo}", parse_mode="HTML",
                           reply_markup=_botones_propuesta(pid))


def tomar_para_corregir(context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    """Si el usuario tocó Corregir, devuelve (y quita) la propuesta que está corrigiendo."""
    pid = context.user_data.pop("corrigiendo", None)
    return _pendientes(context).pop(pid, None) if pid else None


async def botones_propuesta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    accion, pid = query.data.split(":")
    pendiente = _pendientes(context).get(pid)
    if pendiente is None:
        await query.answer("Esto ya caducó, mándamelo de nuevo 🙏", show_alert=True)
        await query.edit_message_reply_markup(reply_markup=None)
        return
    if query.from_user.id != pendiente["dueno"]:
        await query.answer("Solo quien lo escribió puede confirmarlo 😉", show_alert=True)
        return

    if accion == "canc":
        _pendientes(context).pop(pid)
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
    if pendiente["tipo"] in ("edicion", "borrado"):
        _pendientes(context).pop(pid)
        await query.answer("Listo")
        await _confirmar_edicion(update, context, usuario, pendiente)
        return
    if pendiente["tipo"] == "movimientos":
        if problema := comun.problema_movimientos(pendiente["items"], pareja):
            await query.answer(problema, show_alert=True)
            return
        _pendientes(context).pop(pid)
        ids = [comun.guardar_movimiento(m, usuario, pareja, pendiente["mensaje"]) for m in pendiente["items"]]
        cuerpo = "\n\n".join(comun.describir_movimiento(m, usuario, pareja) for m in pendiente["items"])
        await query.answer("Guardado")
        await query.edit_message_text(f"✅ ¡Listo, anotado!\n\n{cuerpo}", parse_mode="HTML",
                                      reply_markup=teclado([boton("↩️ Deshacer", "deshacer:" + ",".join(map(str, ids)))]))
        compartidos = [m for m in pendiente["items"] if m["porcentaje_pagador"] is not None and not m["es_prueba"]]
        if compartidos:
            await _avisar_compartidos(context, update, usuario, compartidos)
    else:
        if pareja is None:
            await query.answer("Tu pareja todavía no se registra 🤷", show_alert=True)
            return
        _pendientes(context).pop(pid)
        ids = []
        for p in pendiente["items"]:
            de, a = (usuario, pareja) if p["direccion"] == "yo_pago" else (pareja, usuario)
            ids.append(finanzas.registrar_liquidacion(de["id"], a["id"], p["monto"], p["fecha"],
                                                      pendiente["mensaje"], p["es_prueba"]))
        cuerpo = "\n\n".join(_describir_pago(p, usuario, pareja) for p in pendiente["items"])
        await query.answer("Guardado")
        await query.edit_message_text(f"✅ ¡Listo, anotado!\n\n{cuerpo}\n\n{comun.texto_saldo()}", parse_mode="HTML",
                                      reply_markup=teclado([boton("↩️ Deshacer", "dpago:" + ",".join(map(str, ids)))]))
        if not pendiente["es_prueba"]:
            await _avisar_pagos(context, update, usuario, pareja, pendiente["items"])


# ---------- editar / borrar movimientos ya guardados ----------

def _encontrar(usuario: dict, objetivo: dict, movimiento_id: int | None) -> dict | None:
    if movimiento_id:
        mov = finanzas.obtener_movimiento(movimiento_id)
        return mov if mov and mov["pagado_por"] == usuario["id"] else None
    encontrados = finanzas.ultimos_movimientos(usuario["id"], objetivo["busqueda"], objetivo["fecha"], limite=1)
    return encontrados[0] if encontrados else None


async def proponer_edicion(update: Update, context: ContextTypes.DEFAULT_TYPE, usuario: dict, datos: dict,
                           mensaje_original: str, movimiento_id: int | None = None) -> None:
    actual = _encontrar(usuario, datos["objetivo"], movimiento_id)
    if actual is None:
        await update.effective_message.reply_text(
            "🤔 No encontré ese movimiento entre los que anotaste tú. Mira /ultimos y edítalo desde ahí.")
        return
    k = datos["cambios"]
    validas = finanzas.nombres_categorias(actual["tipo"])
    nuevo = {**actual}
    if k["monto"]:
        nuevo["monto"] = k["monto"]
    if k["categoria"] in validas:
        nuevo["categoria"] = k["categoria"]
    if k["descripcion"]:
        nuevo["descripcion"] = k["descripcion"]
    if k["fecha"]:
        nuevo["fecha"] = k["fecha"]
    if k["compartido"] == "si" and actual["tipo"] == "gasto":
        nuevo["porcentaje_pagador"] = k["porcentaje_pagador"]
    elif k["compartido"] == "no":
        nuevo["porcentaje_pagador"] = None

    pareja = finanzas.otro_usuario(usuario["id"])
    if nuevo["porcentaje_pagador"] is not None and pareja is None:
        await update.effective_message.reply_text("Tu pareja todavía no se registra, así que no puedo dividirlo 🤷")
        return
    if nuevo == actual:
        await update.effective_message.reply_text(
            "🤔 No caché qué cambiar. Dime, por ejemplo: <i>eran 4500</i>, <i>era a medias</i> o <i>fue ayer</i>",
            parse_mode="HTML")
        return

    pid = secrets.token_hex(4)
    _pendientes(context)[pid] = {"tipo": "edicion", "movimiento_id": actual["id"], "antes": actual, "items": [nuevo],
                                 "mensaje": mensaje_original, "crudo": datos["crudo"], "es_prueba": actual["es_prueba"],
                                 "dueno": update.effective_user.id}
    await update.effective_message.reply_text(
        "✏️ ¿Lo corrijo así?\n\n<b>Antes</b>\n" + comun.describir_movimiento(actual, usuario, pareja)
        + "\n\n<b>Después</b>\n" + comun.describir_movimiento(nuevo, usuario, pareja),
        parse_mode="HTML", reply_markup=_botones_propuesta(pid))


async def proponer_borrado(update: Update, context: ContextTypes.DEFAULT_TYPE, usuario: dict, objetivo: dict,
                           movimiento_id: int | None = None) -> None:
    actual = _encontrar(usuario, objetivo, movimiento_id)
    if actual is None:
        await update.effective_message.reply_text("🤔 No encontré ese movimiento entre los que anotaste tú. Mira /ultimos")
        return
    pid = secrets.token_hex(4)
    _pendientes(context)[pid] = {"tipo": "borrado", "movimiento_id": actual["id"], "antes": actual, "items": [],
                                 "mensaje": "", "crudo": "{}", "es_prueba": actual["es_prueba"],
                                 "dueno": update.effective_user.id}
    pareja = finanzas.otro_usuario(usuario["id"])
    await update.effective_message.reply_text(
        "🗑️ ¿Borro este movimiento?\n\n" + comun.describir_movimiento(actual, usuario, pareja), parse_mode="HTML",
        reply_markup=teclado([boton("🗑️ Sí, borrar", f"conf:{pid}"), boton("❌ No", f"canc:{pid}")]))


async def _confirmar_edicion(update: Update, context, usuario: dict, pendiente: dict) -> None:
    query = update.callback_query
    pareja = finanzas.otro_usuario(usuario["id"])
    antes = pendiente["antes"]
    texto_antes = comun.describir_movimiento(antes, usuario, pareja)
    if pendiente["tipo"] == "borrado":
        ok = finanzas.borrar_movimiento(pendiente["movimiento_id"], usuario["id"])
        await query.edit_message_text(f"🗑️ Borrado\n\n{texto_antes}" if ok else "🤔 Ya no existía.", parse_mode="HTML")
        cambio_txt = f"borró un gasto compartido: {escape(antes['descripcion'] or antes['categoria'])}"
    else:
        nuevo = pendiente["items"][0]
        repartos = None
        if nuevo["porcentaje_pagador"] is not None:
            repartos = {usuario["id"]: nuevo["porcentaje_pagador"], pareja["id"]: 100 - nuevo["porcentaje_pagador"]}
        ok = finanzas.actualizar_movimiento(
            pendiente["movimiento_id"], usuario["id"], monto=nuevo["monto"],
            categoria_id=finanzas.categoria_por_nombre(nuevo["categoria"], nuevo["tipo"])["id"],
            descripcion=nuevo["descripcion"], fecha=nuevo["fecha"], repartos=repartos)
        await query.edit_message_text(
            ("✅ ¡Corregido!\n\n" + comun.describir_movimiento(nuevo, usuario, pareja)) if ok else "🤔 Ya no existía.",
            parse_mode="HTML")
        detalles = []
        if nuevo["monto"] != antes["monto"]:
            detalles.append(f"monto {comun.formatear_monto(antes['monto'], antes['moneda'])} → "
                            f"{comun.formatear_monto(nuevo['monto'], nuevo['moneda'])}")
        if nuevo["porcentaje_pagador"] != antes["porcentaje_pagador"]:
            detalles.append("ahora es compartido" if antes["porcentaje_pagador"] is None
                            else "ya no es compartido" if nuevo["porcentaje_pagador"] is None
                            else f"reparto {comun.porcentaje(nuevo['porcentaje_pagador'])} / "
                                 f"{comun.porcentaje(100 - nuevo['porcentaje_pagador'])}")
        if nuevo["categoria"] != antes["categoria"]:
            detalles.append(f"categoría {escape(nuevo['categoria'])}")
        if nuevo["fecha"] != antes["fecha"]:
            detalles.append(f"fecha {comun.formatear_fecha(nuevo['fecha'])}")
        cambio_txt = (f"corrigió un gasto compartido: {escape(nuevo['descripcion'] or nuevo['categoria'])}"
                      + (f" ({', '.join(detalles)})" if detalles else ""))
    compartido = antes["porcentaje_pagador"] is not None or (
        pendiente["tipo"] == "edicion" and pendiente["items"][0]["porcentaje_pagador"] is not None)
    if ok and compartido and not pendiente["es_prueba"]:
        await comun.avisar_pareja(context.bot, update, usuario,
                                  f"🔔 <b>{escape(primer_nombre(usuario))}</b> {cambio_txt}\n\n{comun.texto_saldo()}")


async def cmd_ultimos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usuario = await comun.usuario_o_aviso(update)
    if usuario is None:
        return
    movs = finanzas.ultimos_movimientos(usuario["id"])
    if not movs:
        await update.effective_message.reply_text("Todavía no has anotado nada 🙌")
        return
    filas = []
    for m in movs:
        etiqueta = (f"{m['fecha'].day}/{m['fecha'].month} · {(m['descripcion'] or m['categoria'])[:22]} · "
                    f"{comun.formatear_monto(m['monto'], m['moneda'])}")
        filas.append([boton(f"✏️ {etiqueta}", f"mov:edit:{m['id']}"), boton("🗑️", f"mov:del:{m['id']}")])
    await update.effective_message.reply_text("🧾 <b>Tus últimos movimientos</b>\nToca ✏️ para corregir o 🗑️ para borrar:",
                                              parse_mode="HTML", reply_markup=teclado(*filas))


async def cb_movimiento(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    usuario = await comun.usuario_de_boton(query)
    if usuario is None:
        return
    _, accion, mid = query.data.split(":")
    await query.answer()
    if accion == "del":
        await proponer_borrado(update, context, usuario, {"busqueda": "", "fecha": None}, int(mid))
        return
    mov = finanzas.obtener_movimiento(int(mid))
    if mov is None or mov["pagado_por"] != usuario["id"]:
        await query.message.reply_text("🤔 Ese movimiento ya no existe.")
        return
    context.user_data["esperando"] = {"tipo": "editar", "movimiento_id": mov["id"],
                                      "descripcion": f"{mov['descripcion'] or mov['categoria']} "
                                                     f"{comun.formatear_monto(mov['monto'], mov['moneda'])} del {mov['fecha']}"}
    await query.message.reply_text(
        f"✏️ ¿Qué le cambio a <b>{escape(mov['descripcion'] or mov['categoria'])}</b>?\n"
        "Ej: <i>eran 4500</i>, <i>era a medias</i>, <i>fue ayer</i>, <i>es transporte</i> (o <i>cancelar</i>)",
        parse_mode="HTML")


async def _avisar_compartidos(context, update: Update, usuario: dict, movimientos: list[dict]) -> None:
    lineas = [f"🔔 <b>{escape(primer_nombre(usuario))}</b> anotó un gasto compartido:"]
    for m in movimientos:
        tu_parte = comun.monto_clp(m) * (100 - m["porcentaje_pagador"]) / 100
        lineas.append(f"• {escape(m['descripcion'] or m['categoria'])}: {comun.formatear_monto(m['monto'], m['moneda'])} "
                      f"→ te toca {formatear_clp(tu_parte)}")
    lineas += ["", comun.texto_saldo()]
    await comun.avisar_pareja(context.bot, update, usuario, "\n".join(lineas))


async def _avisar_pagos(context, update: Update, usuario: dict, pareja: dict, pagos: list[dict]) -> None:
    lineas = []
    for p in pagos:
        if p["direccion"] == "yo_pago":
            lineas.append(f"🔔 <b>{escape(primer_nombre(usuario))}</b> anotó que te pagó {formatear_clp(p['monto'])} 🤝")
        else:
            lineas.append(f"🔔 <b>{escape(primer_nombre(usuario))}</b> anotó que tú le pagaste "
                          f"{formatear_clp(p['monto'])} 🤝")
    lineas += ["", comun.texto_saldo()]
    await comun.avisar_pareja(context.bot, update, usuario, "\n".join(lineas))


async def deshacer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    usuario = await comun.usuario_de_boton(query)
    if usuario is None:
        return
    tipo, ids_txt = query.data.split(":")
    ids = [int(x) for x in ids_txt.split(",")]
    borrar = finanzas.borrar_movimiento if tipo == "deshacer" else finanzas.borrar_liquidacion
    if sum(borrar(i, usuario["id"]) for i in ids):
        await query.answer("Borrado")
        await query.edit_message_text(f"🗑️ Borrado, como si nada\n\n{query.message.text}", reply_markup=None)
    else:
        await query.answer("No lo pude borrar (ya no existe o no lo anotaste tú)", show_alert=True)


# ---------- comandos ----------

async def _registro_rapido(update: Update, context: ContextTypes.DEFAULT_TYPE, tipo: str) -> None:
    """/gasto y /ingreso: registro directo sin IA (el usuario ya escribió los datos explícitos)."""
    usuario = await comun.usuario_o_aviso(update)
    if usuario is None:
        return
    prueba = comun.es_prueba(update.message.text)
    args = [a for a in context.args if not comun.RE_PRUEBA.fullmatch(a)]
    try:
        datos = parser.interpretar(args, finanzas.nombres_categorias(tipo), permitir_reparto=(tipo == "gasto"))
    except parser.ErrorFormato as e:
        await update.effective_message.reply_text(f"{e}\nEjemplo: /{tipo} 15000 super lider")
        return

    mov = {
        "tipo": tipo,
        "monto": datos.monto,
        "moneda": datos.moneda,
        "categoria": datos.categoria or ("Otros" if tipo == "gasto" else "Otros ingresos"),
        "descripcion": datos.descripcion,
        "fecha": finanzas.hoy(),
        "porcentaje_pagador": datos.porcentaje_pagador,
        "es_prueba": prueba,
    }
    await guardar_directo(update, context, usuario, mov, update.message.text)


async def guardar_directo(update: Update, context: ContextTypes.DEFAULT_TYPE, usuario: dict, mov: dict,
                          mensaje_original: str, encabezado: str = "✅ ¡Listo, anotado!") -> int | None:
    """Guarda un movimiento sin pedir confirmación (cuando no lo interpretó la IA)."""
    pareja = finanzas.otro_usuario(usuario["id"])
    if problema := comun.problema_movimientos([mov], pareja):
        await update.effective_message.reply_text(problema)
        return None
    movimiento_id = comun.guardar_movimiento(mov, usuario, pareja, mensaje_original)
    await update.effective_message.reply_text(
        f"{encabezado}\n\n" + comun.describir_movimiento(mov, usuario, pareja), parse_mode="HTML",
        reply_markup=teclado([boton("↩️ Deshacer", f"deshacer:{movimiento_id}")]))
    if mov["porcentaje_pagador"] is not None and not mov["es_prueba"]:
        await _avisar_compartidos(context, update, usuario, [mov])
    return movimiento_id


async def gasto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _registro_rapido(update, context, "gasto")


async def ingreso(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _registro_rapido(update, context, "ingreso")


async def pague(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/pague 25000 — le pagaste a tu pareja para saldar."""
    usuario = await comun.usuario_o_aviso(update)
    if usuario is None:
        return
    pareja = finanzas.otro_usuario(usuario["id"])
    if pareja is None:
        await update.effective_message.reply_text("Tu pareja todavía no se registra (tiene que tirar un /start) 🤷")
        return
    monto = next((m for a in context.args if (m := parser.parsear_monto(a)) is not None), None)
    if monto is None:
        await update.effective_message.reply_text(
            f"Dime cuánto le pagaste a {escape(primer_nombre(pareja))}, tipo: <code>/pague 25000</code>\n"
            "O escríbelo normal: <i>le transferí 25 lucas</i>", parse_mode="HTML")
        return
    pago = {"monto": monto, "direccion": "yo_pago", "fecha": finanzas.hoy(),
            "es_prueba": comun.es_prueba(update.message.text)}
    liq_id = finanzas.registrar_liquidacion(usuario["id"], pareja["id"], monto, pago["fecha"],
                                            update.message.text, pago["es_prueba"])
    await update.effective_message.reply_text(
        f"✅ ¡Listo, anotado!\n\n{_describir_pago(pago, usuario, pareja)}\n\n{comun.texto_saldo()}",
        parse_mode="HTML", reply_markup=teclado([boton("↩️ Deshacer", f"dpago:{liq_id}")]))
    if not pago["es_prueba"]:
        await _avisar_pagos(context, update, usuario, pareja, [pago])


async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await comun.usuario_o_aviso(update) is None:
        return
    await update.effective_message.reply_text(comun.texto_saldo(), parse_mode="HTML")


async def resumen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usuario = await comun.usuario_o_aviso(update)
    if usuario is None:
        return
    datos = finanzas.resumen_mes(usuario["id"])
    mes = datos["mes"]
    lineas = [f"📊 <b>Resumen de {comun.MESES[mes.month - 1]} {mes.year}</b>", ""]
    if datos["gastos"]:
        total = sum(g["total"] for g in datos["gastos"])
        lineas.append(f"<b>Gastos: {formatear_clp(total)}</b>")
        lineas += [f"{g['emoji']} {escape(g['categoria'])}: {formatear_clp(g['total'])}" for g in datos["gastos"]]
        lineas.append("")
        lineas += [f"👤 {escape(p['nombre'])} pagó {formatear_clp(p['total'])}" for p in datos["por_persona"]]
    else:
        lineas.append("Sin gastos este mes todavía.")
    if not comun.es_grupo(update):  # en grupo no se muestran ingresos individuales
        lineas += ["", f"💼 Tus ingresos del mes: {formatear_clp(datos['mis_ingresos'])}"]
    await update.effective_message.reply_text("\n".join(lineas), parse_mode="HTML")


async def borrar_pruebas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await comun.usuario_o_aviso(update) is None:
        return
    n = finanzas.borrar_pruebas()
    if n:
        await update.effective_message.reply_text(f"🧹 Listo, borré {n} registro{'s' if n != 1 else ''} de prueba.")
    else:
        await update.effective_message.reply_text("No había nada de prueba 👌")
