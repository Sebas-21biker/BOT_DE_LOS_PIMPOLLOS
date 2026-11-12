"""Tarjetas de crédito (facturación y pago), compras en cuotas y fijos (sueldo, arriendo...).

- Tarjeta: "mi tarjeta factura el 25 y se paga el 5" → aviso el día que factura y 2 días antes de pagar (solo a su dueño).
- Cuotas: la compra se confirma una vez; cada cuota se anota sola el día de facturación que le toca.
- Fijos: el día del mes que toca, el bot propone el movimiento con Confirmar / Corregir (otro monto) / Cancelar.
"""

import asyncio
import json
import logging
from html import escape

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import comun, plata
from bot.comun import boton, formatear_fecha, formatear_monto, primer_nombre, teclado
from core import compromisos, finanzas, hogar
from core.finanzas import formatear_clp

log = logging.getLogger(__name__)

MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


# ---------- lo que dice el usuario ----------

async def manejar_intencion(update: Update, context: ContextTypes.DEFAULT_TYPE, usuario: dict, datos: dict) -> bool:
    intencion = datos["intencion"]
    if intencion == "tarjeta_guardar":
        await _guardar_tarjetas(update, usuario, datos["tarjetas"])
    elif intencion == "fijo_nuevo":
        await _guardar_fijos(update, usuario, datos["fijos"], update.effective_message.text or "")
    elif intencion == "ver_compromisos":
        await mostrar(update, context, usuario)
    else:
        return False
    return True


async def _guardar_tarjetas(update: Update, usuario: dict, items: list[dict]) -> None:
    lineas, ids = [], []
    for t in items:
        ids.append(await asyncio.to_thread(compromisos.guardar_tarjeta, usuario["id"], t["nombre"],
                                           t["dia_facturacion"], t["dia_vencimiento"]))
        pago = f" y se paga el {t['dia_vencimiento']}" if t["dia_vencimiento"] else ""
        lineas.append(f"💳 <b>{escape(t['nombre'])}</b>: factura el {t['dia_facturacion']}{pago} de cada mes")
    extra = ("Te aviso por privado el día que factura y 2 días antes de pagar. Las compras en cuotas con esta "
             "tarjeta van a caer en su facturación.")
    if any(t["dia_vencimiento"] is None for t in items):
        extra += "\n¿Qué día se paga? Dime \"se paga el 5\" y te aviso antes 😉"
    filas = [[boton(f"↩️ Deshacer {t['nombre'][:25]}", f"comp:tarjeta:{i}")] for t, i in zip(items, ids)]
    await update.effective_message.reply_text("✅ Anotado\n" + "\n".join(lineas) + f"\n\n{extra}",
                                              parse_mode="HTML", reply_markup=teclado(*filas))


async def _guardar_fijos(update: Update, usuario: dict, items: list[dict], mensaje: str) -> None:
    hoy = finanzas.hoy()
    lineas, filas = [], []
    for f in items:
        fijo_id = await asyncio.to_thread(compromisos.crear_fijo, usuario["id"], f, mensaje)
        primero = compromisos.proximo_fijo({"dia_del_mes": f["dia_del_mes"]}, hoy)
        signo = "💰" if f["tipo"] == "ingreso" else "💸"
        compartido = " · a medias" if f["porcentaje_pagador"] == 50 else (
            f" · compartido ({comun.porcentaje(f['porcentaje_pagador'])} tú)" if f["porcentaje_pagador"] is not None else "")
        lineas.append(f"{signo} <b>{escape(f['descripcion'] or f['categoria'])}</b>: "
                      f"{formatear_monto(f['monto'], f['moneda'])} el {f['dia_del_mes']} de cada mes{compartido} "
                      f"(parto el {formatear_fecha(primero)})")
        filas.append([boton(f"↩️ Deshacer {(f['descripcion'] or f['categoria'])[:25]}", f"comp:fijo:{fijo_id}")])
    await update.effective_message.reply_text(
        "📌 <b>Fijo anotado</b>\n" + "\n".join(lineas)
        + "\n\nEse día te pregunto si lo anoto: un toque y listo. Si fue otro monto, tocas Corregir 😉",
        parse_mode="HTML", reply_markup=teclado(*filas))


# ---------- ver ----------

def texto_compromisos(usuario: dict) -> tuple[str, list]:
    hoy = finanzas.hoy()
    tarjetas = compromisos.tarjetas(usuario["id"])
    fijos = compromisos.fijos(usuario["id"])
    planes = compromisos.planes_pendientes(usuario["id"])
    partes, filas = [], []

    if tarjetas:
        lineas = ["💳 <b>Tus tarjetas</b>"]
        for t in tarjetas:
            pago = f", se paga el {t['dia_vencimiento']}" if t["dia_vencimiento"] else ""
            lineas.append(f"• {escape(t['nombre'])}: factura el {t['dia_facturacion']}{pago}")
            filas.append([boton(f"🗑️ Tarjeta {t['nombre'][:25]}", f"comp:tarjeta:{t['id']}")])
        partes.append("\n".join(lineas))

    if planes:
        lineas = ["📅 <b>Compras en cuotas</b>"]
        for p in planes:
            montos = compromisos.montos_cuotas(p["monto_total"], p["n_cuotas"], p["moneda"])
            falta = sum(montos[p["cuotas_registradas"]:])
            fechas = compromisos.fechas_cuotas(p["primera_fecha"], p["dia_cobro"], p["n_cuotas"])
            lineas.append(f"• {escape(p['descripcion'] or 'compra')}: cuota {p['cuotas_registradas']}/{p['n_cuotas']} de "
                          f"{formatear_monto(montos[0], p['moneda'])} · faltan {formatear_monto(falta, p['moneda'])} "
                          f"(hasta {MESES_CORTOS[fechas[-1].month - 1]} {fechas[-1].year})")
            filas.append([boton(f"✋ Dejar de anotar {(p['descripcion'] or 'compra')[:20]}", f"comp:cancelar:{p['id']}")])
        proyeccion = compromisos.proyeccion([p for p in planes if p["moneda"] == "CLP"])
        if proyeccion:
            lineas.append("Próximos meses: " + " · ".join(
                f"{MESES_CORTOS[mes.month - 1]} {formatear_clp(total)}" for mes, total in proyeccion))
        partes.append("\n".join(lineas))

    if fijos:
        lineas = ["📌 <b>Fijos de cada mes</b>"]
        for f in fijos:
            signo = "💰" if f["tipo"] == "ingreso" else "💸"
            lineas.append(f"{signo} {escape(f['descripcion'])}: {formatear_monto(f['monto'], f['moneda'])} el "
                          f"{f['dia_del_mes']} (próximo: {formatear_fecha(compromisos.proximo_fijo(f, hoy))})")
            filas.append([boton(f"🗑️ Fijo {f['descripcion'][:25]}", f"comp:fijo:{f['id']}")])
        partes.append("\n".join(lineas))

    if not partes:
        return ("Todavía no tienes tarjetas, cuotas ni fijos 🙌\n\n"
                "Prueba con:\n• <i>mi tarjeta factura el 25 y se paga el 5</i>\n"
                "• <i>compré una tele en 600 lucas en 6 cuotas</i>\n"
                "• <i>mi sueldo es 1.200.000 y me llega el 30</i>"), []
    return "\n\n".join(partes), filas


async def mostrar(update: Update, context: ContextTypes.DEFAULT_TYPE, usuario: dict) -> None:
    """Fijos, cuotas y tarjetas de quien pregunta. Son datos personales (sueldo): en el grupo se mandan por privado."""
    texto, filas = await asyncio.to_thread(texto_compromisos, usuario)
    if comun.es_grupo(update):
        try:
            await context.bot.send_message(usuario["telegram_id"], texto, parse_mode="HTML",
                                           reply_markup=teclado(*filas))
            await update.effective_message.reply_text("🔒 Te lo mandé por privado 😉")
        except TelegramError:
            await update.effective_message.reply_text("Escríbeme por privado primero (/start) y te lo mando allá 🙏")
        return
    await update.effective_message.reply_text(texto, parse_mode="HTML", reply_markup=teclado(*filas))


async def cmd_compromisos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/cuotas y /fijos: tarjetas, compras en cuotas y fijos de quien pregunta."""
    usuario = await comun.usuario_o_aviso(update)
    if usuario is not None:
        await mostrar(update, context, usuario)


async def cb_compromiso(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """comp:tarjeta|fijo:id → borrar · comp:cancelar:id → dejar de anotar las cuotas que faltan."""
    query = update.callback_query
    usuario = await comun.usuario_de_boton(query)
    if usuario is None:
        return
    _, que, ident = query.data.split(":")
    accion = {"tarjeta": compromisos.borrar_tarjeta, "fijo": compromisos.borrar_fijo,
              "cancelar": compromisos.cancelar_plan}[que]
    if not await asyncio.to_thread(accion, int(ident), usuario["id"]):
        await query.answer("Ya no existe o no es tuyo 🤷", show_alert=True)
        return
    await query.answer("Listo")
    if que == "cancelar":
        aviso = "✋ Listo, dejo de anotar las cuotas que faltaban de esa compra (las ya anotadas quedan)."
    else:
        aviso = "🗑️ Listo, lo quité."
    texto, filas = await asyncio.to_thread(texto_compromisos, usuario)
    await query.edit_message_text(f"{aviso}\n\n{texto}", parse_mode="HTML", reply_markup=teclado(*filas))


# ---------- avisos diarios (desde consultas.bucle_programados, 9:00) ----------

def _datos_fijo(fijo: dict, fecha) -> dict:
    """El fijo como una interpretación de la IA (para reusar la propuesta con Confirmar / Corregir / Cancelar)."""
    mov = {"tipo": fijo["tipo"], "monto": fijo["monto"], "moneda": fijo["moneda"],
           "categoria": fijo["categoria"] or ("Otros" if fijo["tipo"] == "gasto" else "Otros ingresos"),
           "descripcion": fijo["descripcion"], "fecha": fecha, "porcentaje_pagador": fijo["porcentaje_pagador"],
           "cuotas": 1, "tarjeta": ""}
    crudo = {"intencion": "movimientos", "respuesta": "", "movimientos": [{
        **{k: mov[k] for k in ("tipo", "moneda", "categoria", "descripcion", "tarjeta", "cuotas")},
        "monto": float(mov["monto"]), "fecha": fecha.isoformat(), "compartido": mov["porcentaje_pagador"] is not None,
        "porcentaje_pagador": float(mov["porcentaje_pagador"] or 100)}]}
    return {"intencion": "movimientos", "movimientos": [mov], "pagos": [], "crudo": json.dumps(crudo, ensure_ascii=False)}


async def avisos_diarios(app) -> None:
    hoy = finanzas.hoy()
    bot = app.bot

    # 1) Cuotas que tocan hoy (o que quedaron atrasadas si el bot estuvo caído).
    anotadas = await asyncio.to_thread(compromisos.anotar_cuotas_vencidas, hoy)
    por_usuario: dict[int, list[dict]] = {}
    for c in anotadas:
        por_usuario.setdefault(c["telegram_id"], []).append(c)
    for telegram_id, cuotas in por_usuario.items():
        lineas = ["📅 <b>Anoté las cuotas de hoy</b>"] + [
            f"• {escape(c['descripcion'])}: {formatear_monto(c['monto'], c['moneda'])}"
            + (" 🧪" if c["es_prueba"] else "") for c in cuotas]
        try:
            await bot.send_message(telegram_id, "\n".join(lineas), parse_mode="HTML")
        except TelegramError:
            log.warning("No pude avisar las cuotas a %s", telegram_id)
        compartidas = [c for c in cuotas if c["porcentaje_pagador"] is not None and not c["es_prueba"]]
        pareja = finanzas.otro_usuario(cuotas[0]["usuario_id"]) if compartidas else None
        if pareja:
            lineas = [f"🔔 Cuota compartida de <b>{escape(primer_nombre({'nombre': cuotas[0]['usuario']}))}</b>:"] + [
                f"• {escape(c['descripcion'])}: te toca {formatear_clp(c['monto'] * (100 - c['porcentaje_pagador']) / 100)}"
                for c in compartidas] + ["", comun.texto_saldo()]
            try:
                await bot.send_message(pareja["telegram_id"], "\n".join(lineas), parse_mode="HTML")
            except TelegramError:
                log.warning("No pude avisar la cuota compartida a %s", pareja["nombre"])

    # 2) Fijos del día: se proponen (nunca se anotan solos).
    for f in await asyncio.to_thread(compromisos.fijos):
        fecha = compromisos.fijo_toca(f, hoy)
        if fecha is None or not await asyncio.to_thread(hogar.reservar_envio, f"fijo-{f['id']}-{fecha:%Y-%m}"):
            continue
        usuario = {"id": f["usuario_id"], "nombre": f["usuario"], "telegram_id": f["telegram_id"]}
        que = "te llegó" if f["tipo"] == "ingreso" else "pagaste"
        encabezado = (f"📌 <b>Hoy toca: {escape(f['descripcion'])}</b>. ¿{que.capitalize()}? ¿Lo anoto así? 👇\n"
                      "<i>(Si fue otro monto: Corregir. Si no " + ("llegó" if f["tipo"] == "ingreso" else "lo pagaste")
                      + ": Cancelar.)</i>")
        try:
            await plata.enviar_propuesta(bot, app.bot_data, f["telegram_id"], f["telegram_id"], usuario,
                                         _datos_fijo(f, fecha), f"📌 fijo: {f['descripcion']}", False,
                                         encabezado=encabezado)
        except TelegramError:
            log.warning("No pude proponer el fijo %s", f["id"])

    # 3) Tarjetas: el día que facturan y unos días antes de pagar.
    for t in await asyncio.to_thread(compromisos.tarjetas):
        for tipo, fecha in compromisos.avisos_tarjeta(t, hoy):
            if not await asyncio.to_thread(hogar.reservar_envio, f"tarjeta-{tipo}-{t['id']}-{fecha}"):
                continue
            if tipo == "facturacion":
                cuotas = [c for c in anotadas if c["tarjeta_id"] == t["id"]]
                texto = f"🧾 Hoy factura tu tarjeta <b>{escape(t['nombre'])}</b>."
                if cuotas:
                    texto += " Entran estas cuotas:\n" + "\n".join(
                        f"• {escape(c['descripcion'])}: {formatear_monto(c['monto'], c['moneda'])}" for c in cuotas)
                if t["dia_vencimiento"]:
                    texto += f"\nSe paga el {t['dia_vencimiento']}: te aviso antes 😉"
            else:
                texto = (f"💳 Tu tarjeta <b>{escape(t['nombre'])}</b> se paga el {formatear_fecha(fecha)} "
                         f"(en {compromisos.AVISO_PAGO_DIAS} días). Revisa el monto en la app del banco y págala 🙏")
            try:
                await bot.send_message(t["telegram_id"], texto, parse_mode="HTML")
            except TelegramError:
                log.warning("No pude avisar la tarjeta %s", t["id"])
