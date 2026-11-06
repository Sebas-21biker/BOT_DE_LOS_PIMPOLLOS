"""Cuándo vale la pena que Pimpollo procese un mensaje del grupo, y cuándo se mete por iniciativa propia.

Fase 0: filtro gratis (sin IA). En el grupo el bot recibe TODA la conversación; antes cada mensaje costaba dos
llamadas a Claude aunque fuera un "jajaja". Ahora solo pasa a la IA lo que:
  - le habla al bot (lo nombran o le responden),
  - trae un monto (posible gasto/pago),
  - suena a algo que el bot sabe hacer (lista, recordatorio, meta, tarea, panorama, nota, perfil, consulta).
El resto se ignora.

Fase A: iniciativa. Los mensajes del grupo se guardan 48 h (`mensajes_grupo`). Cuando la charla se pausa, un
modelo barato (Haiku) lee el tramo UNA vez y dice de qué hablan, si es sensible, si ya lo resolvieron, qué tan
útil sería intervenir y si alguien dejó caer un deseo. El CÓDIGO decide (`decidir_grupo`, `decidir_sorpresa`):
horario de descanso, silencio, cooldown, máximo diario y umbral según el nivel de iniciativa. Si interviene en el
grupo, redacta el modelo principal. Los deseos se avisan por privado SOLO a la otra persona, como idea de sorpresa
(lo que cada uno escribe en privado nunca se usa para escribirle al otro).
"""

import asyncio
import logging
import re
from datetime import datetime, time, timedelta
from html import escape

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import comun, interprete, pareja
from bot.comun import boton, primer_nombre, teclado
from core import feriados, finanzas, hogar, iniciativa, perfil
from core.finanzas import ZONA
from core.parser import normalizar

log = logging.getLogger(__name__)

# Un monto: "5000", "5.000", "$5000", "5 lucas", "20k", "2 millones", "usd 30". Números chicos sueltos
# ("a las 8", "el 15") no cuentan.
_RE_MONTO = re.compile(
    r"\$\s?\d"
    r"|\b\d{1,3}(?:[.,]\d{3})+\b"
    r"|\b\d{4,}\b"
    r"|\b\d+(?:[.,]\d+)?\s?(?:k|lucas?|mil|millon(?:es)?|palos?|usd|mxn|clp|dolares?|pesos)\b"
    r"|\b(?:usd|mxn|clp)\s?\d"
)

# Raíces de palabras (sin tildes, en minúsculas) de cosas que el bot sabe hacer.
_RAICES = (
    # plata
    "gaste", "gastamos", "gasto", "pague", "pagamos", "pago", "transferi", "transfiri", "deposite", "compre",
    "a medias", "compartido", "saldo", "debo", "debe ", "debes", "cuanto", "presupuesto", "boleta",
    # metas
    "meta", "ahorr", "aparte ", "juntar",
    # lista de compras
    "lista", "agrega", "hay que comprar", "falta ", "faltan ", "se acabo", "se acabaron",
    # recordatorios y fechas
    "recuerda", "recordatorio", "acuerdate", "avisame", "avisanos", "cumple", "aniversario", "santo de",
    # panoramas e ideas
    "anota", "anotar", "guarda", "panorama", "pelicula", "peli ", "serie", "restaurant", "que hacemos",
    "que podemos hacer", "ideas", "recomienda", "regalo", "menu", "que cocinamos", "que comemos",
    # notas de la casa
    "clave", "wifi", "contrasena", "talla",
    # tareas
    "tarea", "me toca", "te toca", "le toca", "turno",
    # perfil
    "le gusta", "le encanta", "me encanta", "le carga", "suena con", "sueno con", "sonamos",
)


def tiene_monto(texto: str) -> bool:
    return bool(_RE_MONTO.search(normalizar(texto)))


def merece_ia(texto: str, tareas_activas: list[str] = ()) -> bool:
    """¿Este mensaje del grupo (que NO le habla al bot) podría ser algo que el bot debe registrar o responder?"""
    t = f" {normalizar(texto)} "
    if len(t.strip()) < 4:
        return False
    if tiene_monto(texto):
        return True
    if any(raiz in t for raiz in _RAICES):
        return True
    # "ya saqué la basura": la tarea se menciona por nombre.
    for tarea in tareas_activas:
        palabras = [p for p in normalizar(tarea).split() if len(p) > 3]
        if palabras and any(p in t for p in palabras):
            return True
    return False


_PALABRAS_FOTO = ("boleta", "voucher", "comprobante", "factura", "recibo", "ticket", "anota", "pimpollo",
                  "test", "prueba", "medias", "compartido")


def foto_merece_ia(caption: str) -> bool:
    """En el grupo las fotos (memes, selfies...) no se mandan a la IA salvo que el texto pida anotarla."""
    t = normalizar(caption or "")
    return bool(t) and (any(p in t for p in _PALABRAS_FOTO) or tiene_monto(t))


# ---------- fase A: iniciativa ----------

NIVELES = {
    "baja": {"umbral": 8, "cooldown": timedelta(hours=3), "max_dia": 2, "sorpresa_cada_dias": 3},
    "media": {"umbral": 7, "cooldown": timedelta(minutes=90), "max_dia": 4, "sorpresa_cada_dias": 2},
    "alta": {"umbral": 6, "cooldown": timedelta(minutes=45), "max_dia": 6, "sorpresa_cada_dias": 1},
}
NOMBRES_NIVEL = {"off": "🔕 apagada", "baja": "🐢 baja", "media": "🙂 media", "alta": "🙋 alta"}
DESCANSO = (time(22, 30), time(8, 30))   # no habla por iniciativa entre 22:30 y 8:30
PAUSA = 2                                # minutos sin mensajes para evaluar el tramo
MOMENTO_PASADO = timedelta(minutes=20)   # si la charla terminó hace más, ya no se mete en el grupo
BOT_RECIEN = timedelta(minutes=10)       # si le hablaron al bot hace poco, no se suma por iniciativa
MAX_CLASIFICACIONES_DIA = 80             # tope de llamadas a Haiku por día

# Temas delicados: si aparecen, ni siquiera se llama a la IA (además de lo que detecte Haiku).
_RAICES_SENSIBLES = (
    "pelea", "peleamos", "peliamos", "enojad", "enojo", "molest", "discutimos", "discusion",
    "separarnos", "nos separamos", "terminemos", "divorc", "infiel", "celos", "lo siento",
    "doctor", "medico", "hospital", "clinica", "urgencia", "enferm", "examen", "diagnostic", "cancer", "remedio",
    "terapia", "psicolog", "psiquiatr", "depre", "ansiedad", "angustia", "llor", "triste", "embaraz",
    "murio", "fallec", "funeral", "velorio", "duelo",
    "sexo", "despid", "deuda", "embargo", "dicom",
)
_RE_SILENCIO = re.compile(r"\b(silencio|callate|no hables|no te metas|deja de hablar|shh+)\b")
_RE_HABLAR = re.compile(r"\b(ya puedes hablar|puedes volver a hablar|vuelve a hablar|habla nomas|ya puedes opinar)\b")


def en_descanso(ahora: datetime) -> bool:
    hora = ahora.astimezone(ZONA).time()
    return hora >= DESCANSO[0] or hora < DESCANSO[1]


def fin_del_descanso(ahora: datetime) -> datetime:
    """Próximas 8:30 (para "silencio por hoy")."""
    local = ahora.astimezone(ZONA)
    dia = local.date() if local.time() < DESCANSO[1] else local.date() + timedelta(days=1)
    return datetime.combine(dia, DESCANSO[1], tzinfo=ZONA)


def es_sensible(texto: str) -> bool:
    t = f" {normalizar(texto)} "
    return any(raiz in t for raiz in _RAICES_SENSIBLES)


def pide_silencio(texto: str) -> bool:
    return bool(_RE_SILENCIO.search(normalizar(texto)))


def pide_hablar(texto: str) -> bool:
    return bool(_RE_HABLAR.search(normalizar(texto)))


def decidir_grupo(ev: dict, ahora: datetime, prefs: dict, anteriores: list[datetime],
                  ultimo_mensaje: datetime, ultimo_al_bot: datetime | None) -> tuple[bool, str]:
    """¿Pimpollo se mete en el grupo? Lo decide el código, no la IA. Devuelve (sí/no, por qué)."""
    nivel = NIVELES.get(prefs["nivel"])
    if nivel is None:
        return False, "iniciativa apagada"
    if prefs.get("silencio_hasta") and prefs["silencio_hasta"] > ahora:
        return False, "le pidieron silencio"
    if en_descanso(ahora):
        return False, "horario de descanso"
    if ev["sensible"]:
        return False, "tema sensible"
    if ev["ya_resuelto"] or ev["oportunidad"] == "ninguna":
        return False, "nada que aportar"
    if ahora - ultimo_mensaje > MOMENTO_PASADO:
        return False, "ya pasó el momento"
    if ultimo_al_bot and ahora - ultimo_al_bot < BOT_RECIEN:
        return False, "el bot acaba de hablar"
    if anteriores and ahora - max(anteriores) < nivel["cooldown"]:
        return False, "cooldown"
    hoy = [a for a in anteriores if a.astimezone(ZONA).date() == ahora.astimezone(ZONA).date()]
    if len(hoy) >= nivel["max_dia"]:
        return False, "máximo del día"
    puntaje = ev["utilidad"] - len(hoy)          # cada intervención de hoy le resta un punto
    umbral = nivel["umbral"] + prefs.get("ajuste", 0)
    if puntaje < umbral:
        return False, f"puntaje {puntaje} < umbral {umbral}"
    return True, f"puntaje {puntaje} >= umbral {umbral}"


def decidir_sorpresa(deseo: str, ahora: datetime, prefs: dict, anteriores: list[dict]) -> bool:
    """¿Se le avisa por privado a la pareja un deseo dicho en el grupo? `anteriores` = avisos de los últimos 30 días."""
    nivel = NIVELES.get(prefs["nivel"])
    if nivel is None or en_descanso(ahora):
        return False
    if any(normalizar(a["texto"]) == normalizar(deseo) for a in anteriores):
        return False
    cada = timedelta(days=nivel["sorpresa_cada_dias"] + 2 * max(0, prefs.get("ajuste", 0)))
    return not anteriores or ahora - max(a["creado_en"] for a in anteriores) >= cada


def formatear_tramo(tramo: list[dict]) -> str:
    return "\n".join(f"[{m['creado_en'].astimezone(ZONA):%H:%M}] {m['nombre'] or '?'}"
                     f"{' [al bot]' if m['al_bot'] else ''}: {m['texto']}" for m in tramo)


# ----- bucle: evalúa los tramos cuando la charla se pausa -----

_clasificaciones: dict = {}


def _hay_cupo_clasificador(ahora: datetime) -> bool:
    dia = ahora.date()
    if _clasificaciones.get("dia") != dia:
        _clasificaciones.update(dia=dia, n=0)
    if _clasificaciones["n"] >= MAX_CLASIFICACIONES_DIA:
        return False
    _clasificaciones["n"] += 1
    return True


async def bucle_iniciativa(app) -> None:
    while True:
        try:
            ahora = finanzas.ahora()
            if not en_descanso(ahora):  # de noche se espera: a las 8:30 se evalúa lo pendiente
                await asyncio.to_thread(iniciativa.borrar_viejos)
                for chat_id in await asyncio.to_thread(iniciativa.chats_en_pausa, PAUSA):
                    tramo = await asyncio.to_thread(iniciativa.tomar_tramo, chat_id)
                    await _evaluar(app, chat_id, tramo, ahora)
        except Exception:
            log.exception("Error en el bucle de iniciativa")
        await asyncio.sleep(30)


async def _evaluar(app, chat_id: int, tramo: list[dict], ahora: datetime) -> None:
    propios = [m for m in tramo if not m["al_bot"]]
    if sum(len(m["texto"]) for m in propios) < 15:
        return  # nada de charla nueva (o solo le hablaban al bot, que ya respondió)
    if es_sensible(" ".join(m["texto"] for m in tramo)):
        log.info("Iniciativa: tramo sensible, no se evalúa")
        return
    usuarios = await asyncio.to_thread(finanzas.todos_los_usuarios)
    prefs = await asyncio.to_thread(iniciativa.preferencias, chat_id)
    privados = [await asyncio.to_thread(iniciativa.preferencias, u["telegram_id"]) for u in usuarios]
    if prefs["nivel"] == "off" and all(p["nivel"] == "off" for p in privados):
        return
    if not _hay_cupo_clasificador(ahora):
        return
    texto = formatear_tramo(tramo)
    try:
        ev = await interprete.clasificar_conversacion(texto, [u["nombre"] for u in usuarios], ahora)
    except interprete.ErrorInterprete:
        return
    log.info("Iniciativa: tema=%s oportunidad=%s utilidad=%s sensible=%s deseos=%d",
             ev["tema"], ev["oportunidad"], ev["utilidad"], ev["sensible"], len(ev["deseos"]))
    if ev["sensible"]:
        return
    await _quizas_intervenir(app, chat_id, tramo, texto, ev, prefs, ahora)
    await _avisar_deseos(app, ev, usuarios, ahora)


def _contexto_intervencion() -> str:
    """Lo que Pimpollo sabe y puede usar en el grupo (sin sorpresas: los dos ven el chat)."""
    hoy = finanzas.hoy()
    lineas = [f"Hoy: {comun.DIAS[hoy.weekday()]} {hoy:%Y-%m-%d}."]
    proximos = feriados.proximos(hoy, dias=30)
    if proximos:
        lineas.append("Fechas cercanas: " + "; ".join(f"{f['nombre']} {f['fecha']:%d-%m}" for f in proximos))
    panoramas = hogar.panoramas_pendientes()[:15]
    if panoramas:
        lineas.append("Panoramas que tienen anotados: " + "; ".join(f"{p['titulo']} ({p['tipo']})" for p in panoramas))
    filas = perfil.visibles_para(perfil.listar(), usuario_id=0, en_grupo=True)
    if filas:
        lineas.append("Lo que te contaron de ellos:")
        lineas += [f"- {f['nombre'] or 'De los dos'} / {f['categoria']}: {f['dato']}" for f in filas[:40]]
    return "\n".join(lineas)


async def _quizas_intervenir(app, chat_id: int, tramo: list[dict], texto: str, ev: dict, prefs: dict,
                             ahora: datetime) -> None:
    anteriores = [i["creado_en"] for i in
                  await asyncio.to_thread(iniciativa.intervenciones_recientes, chat_id, "grupo", 2)]
    al_bot = [m["creado_en"] for m in tramo if m["al_bot"]]
    si, razon = decidir_grupo(ev, ahora, prefs, anteriores, tramo[-1]["creado_en"], max(al_bot, default=None))
    log.info("Iniciativa en el grupo: %s (%s)", "sí" if si else "no", razon)
    if not si:
        return
    contexto = await asyncio.to_thread(_contexto_intervencion)
    try:
        redaccion = await interprete.redactar_intervencion(texto, ev, contexto)
    except interprete.ErrorInterprete:
        return
    mensaje = redaccion["mensaje"].strip()
    if not redaccion["intervenir"] or not mensaje:
        return
    iid = await asyncio.to_thread(iniciativa.registrar_intervencion, chat_id, "grupo", ev["tema"],
                                  ev["utilidad"], mensaje)
    try:
        await app.bot.send_message(chat_id, escape(mensaje), parse_mode="HTML", reply_markup=teclado(
            [boton("👍 Buena", f"ini:si:{iid}"), boton("🤫 Mejor no", f"ini:no:{iid}")]))
    except TelegramError:
        log.warning("No pude intervenir en el grupo %s", chat_id)


async def _avisar_deseos(app, ev: dict, usuarios: list[dict], ahora: datetime) -> None:
    for d in ev["deseos"]:
        quien = next((u for u in usuarios if u["nombre"] == d["quien"]), None)
        destino = next((u for u in usuarios if quien and u["id"] != quien["id"]), None)
        if destino is None:
            continue
        chat = destino["telegram_id"]
        prefs = await asyncio.to_thread(iniciativa.preferencias, chat)
        anteriores = await asyncio.to_thread(iniciativa.intervenciones_recientes, chat, "sorpresa", 30)
        deseo = d["deseo"].strip()
        if not decidir_sorpresa(deseo, ahora, prefs, anteriores):
            continue
        iid = await asyncio.to_thread(iniciativa.registrar_intervencion, chat, "sorpresa", d["tipo"], 0, deseo)
        nombre = primer_nombre(quien)
        botones = [[boton("💝 Guardar como idea", f"sorp:g:{iid}:{quien['id']}"),
                    pareja._boton_pedir_ideas(app.bot_data, "💡 Ideas con esto",
                                              f"una sorpresa o regalo para {nombre}: {deseo}")],
                   [boton("🤫 Menos avisos de estos", f"sorp:menos:{iid}:{quien['id']}")]]
        try:
            await app.bot.send_message(
                chat, f"🤫 <b>Dato para una sorpresa</b>\nEn el grupo, {escape(nombre)} dejó caer algo que te puede "
                      f"servir: <i>{escape(deseo)}</i>.\n\n¿Lo guardo como idea de regalo? A {escape(nombre)} no "
                      "se lo voy a mostrar.", parse_mode="HTML", reply_markup=teclado(*botones))
        except TelegramError:
            log.warning("No pude avisar un deseo a %s", destino["nombre"])


# ----- lo que hacen los usuarios: guardar la charla, silencio, botones, /iniciativa -----

async def guardar_mensaje(update: Update, usuario: dict, al_bot: bool) -> None:
    try:
        await asyncio.to_thread(iniciativa.guardar_mensaje, update.effective_chat.id, usuario["id"],
                                update.message.text, al_bot)
    except Exception:
        log.exception("No pude guardar el mensaje del grupo")


async def manejar_silencio(update: Update) -> bool:
    """"pimpollo, silencio" / "ya puedes hablar". Devuelve True si el mensaje era eso."""
    texto = update.message.text or ""
    chat_id = update.effective_chat.id
    if pide_silencio(texto):
        hasta = fin_del_descanso(finanzas.ahora())
        await asyncio.to_thread(iniciativa.cambiar, chat_id, silencio_hasta=hasta)
        await update.message.reply_text("🤐 Listo, me quedo piola hasta mañana. Si me necesitan, me nombran.")
        return True
    if pide_hablar(texto):
        await asyncio.to_thread(iniciativa.cambiar, chat_id, silencio_hasta=None)
        await update.message.reply_text("🙌 Wena, vuelvo a opinar de vez en cuando (poquito, prometido).")
        return True
    return False


async def cb_iniciativa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """👍 / 🤫 bajo una intervención espontánea: mueven el umbral del grupo."""
    query = update.callback_query
    if await comun.usuario_de_boton(query) is None:
        return
    _, voto, iid = query.data.split(":")
    fila = await asyncio.to_thread(iniciativa.reaccionar, int(iid), "util" if voto == "si" else "silencio")
    if fila is None:
        await query.answer("Ya me lo dijeron 👌")
        return
    await asyncio.to_thread(iniciativa.cambiar, fila["chat_id"], delta_ajuste=-1 if voto == "si" else 1)
    await query.answer("¡Bacán! Lo tomo en cuenta 🙌" if voto == "si" else "Entendido, me pongo más piola 🤫")
    try:
        await query.edit_message_reply_markup(None)
    except TelegramError:
        pass


async def cb_sorpresa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Botones del aviso privado de un deseo: guardarlo como idea de regalo secreta, o pedir menos avisos."""
    query = update.callback_query
    usuario = await comun.usuario_de_boton(query)
    if usuario is None:
        return
    _, accion, iid, quien_id = query.data.split(":")
    if int(quien_id) == usuario["id"]:
        await query.answer("Esto no era para ti 🙈", show_alert=True)
        return
    if accion == "g":
        fila = await asyncio.to_thread(iniciativa.reaccionar, int(iid), "util")
        if fila is None:
            await query.answer("Ya lo había guardado 👌")
            return
        recientes = await asyncio.to_thread(iniciativa.intervenciones_recientes, usuario["telegram_id"],
                                            "sorpresa", 60)
        deseo = next((i["texto"] for i in recientes if i["id"] == int(iid)), None)
        if deseo:
            # Anotado por quien recibe el aviso SOBRE la otra persona -> es una sorpresa: la festejada no lo ve.
            await asyncio.to_thread(perfil.guardar, int(quien_id), "regalos", deseo, usuario["id"])
        await query.answer("💝 Guardado en secreto")
        await query.edit_message_text(
            query.message.text_html + "\n\n💝 <i>Guardado como idea de regalo (secreta).</i>", parse_mode="HTML")
        return
    await asyncio.to_thread(iniciativa.reaccionar, int(iid), "silencio")
    await asyncio.to_thread(iniciativa.cambiar, usuario["telegram_id"], delta_ajuste=1)
    await query.answer("Ok, te aviso menos seguido 🤫")
    try:
        await query.edit_message_reply_markup(None)
    except TelegramError:
        pass


_ALIAS_NIVEL = {"off": "off", "apagada": "off", "apagado": "off", "nada": "off", "no": "off",
                "baja": "baja", "bajo": "baja", "media": "media", "medio": "media", "alta": "alta", "alto": "alta"}


async def cmd_iniciativa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/iniciativa [baja|media|alta|off]. En el grupo: cuánto se mete en la charla. En privado: avisos de sorpresas."""
    if await comun.usuario_o_aviso(update) is None:
        return
    chat_id = update.effective_chat.id
    if context.args:
        nivel = _ALIAS_NIVEL.get(normalizar(context.args[0]))
        if nivel is None:
            await update.message.reply_text("Usa /iniciativa baja, media, alta u off 🙏")
            return
        await asyncio.to_thread(iniciativa.cambiar, chat_id, nivel=nivel)
    prefs = await asyncio.to_thread(iniciativa.preferencias, chat_id)
    if comun.es_grupo(update):
        texto = (f"🙋 Iniciativa en el grupo: <b>{NOMBRES_NIVEL[prefs['nivel']]}</b>\n\n"
                 "Cuando la charla se pausa, veo si puedo aportar algo (planes, qué comer, qué ver, algo que "
                 "anotar). Nunca en temas delicados ni entre 22:30 y 8:30. Los botones 👍/🤫 me ayudan a calibrar.\n"
                 "<i>«pimpollo, silencio» me calla hasta mañana.</i>")
        if prefs.get("silencio_hasta") and prefs["silencio_hasta"] > finanzas.ahora():
            texto += f"\n\n🤐 Ahora estoy en silencio hasta las {prefs['silencio_hasta'].astimezone(ZONA):%H:%M}."
    else:
        texto = (f"🤫 Avisos de sorpresas por privado: <b>{NOMBRES_NIVEL[prefs['nivel']]}</b>\n\n"
                 "Si en el grupo tu pareja dice que le encantaría algo, te lo cuento acá como idea de regalo o "
                 "cita. Lo que me escribes en privado nunca se lo cuento a tu pareja.")
    texto += "\n\nCambiar: /iniciativa baja · media · alta · off"
    await update.message.reply_text(texto, parse_mode="HTML")
