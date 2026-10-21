"""Interpreta mensajes en lenguaje natural con Claude -> datos estructurados.

Claude solo traduce texto a datos; no calcula totales ni saldos.
"""

import base64
import json
import logging
from datetime import date, datetime, time
from decimal import Decimal

import anthropic

from core import config, feriados
from core.finanzas import ZONA

log = logging.getLogger(__name__)

_cliente: anthropic.AsyncAnthropic | None = None


def _client() -> anthropic.AsyncAnthropic:
    global _cliente
    if _cliente is None:
        _cliente = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key())
    return _cliente


DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
CATEGORIAS_PERFIL = ["gustos", "comida", "suenos", "trabajo", "amigos", "deportes", "regalos", "musica", "otro"]
TIPOS_PANORAMA = ["restaurante", "lugar", "pelicula", "serie", "actividad", "viaje", "otro"]
INTENCIONES = [
    "movimientos", "pago_pareja", "meta_nueva", "meta_aporte", "ver_metas",
    "compras_agregar", "ver_lista", "recordatorio", "ver_recordatorios",
    "panorama_agregar", "panorama_sugerir", "nota_guardar", "nota_buscar",
    "tarea_nueva", "ver_tareas", "tarea_hecha", "menu_semanal",
    "perfil_guardar", "perfil_ver", "pedir_ideas", "editar_movimiento", "borrar_movimiento",
    "consulta", "otro",
]

SISTEMA = """Eres el intérprete de "Pimpollo", el bot de una pareja en Chile. Recibes mensajes informales en \
español chileno y los conviertes en datos estructurados. Quien escribe es "el usuario"; la otra persona \
de la pareja es "la pareja".

Cada mensaje tiene UNA intención. Se trabaja en dos pasos: primero se te pide solo la intención y luego los
datos de esa intención.

1. movimientos: gastos o ingresos de plata. Un mensaje puede traer varios (uno por monto).
   - Quien escribe es quien pagó el gasto o recibió el ingreso.
   - Montos en pesos chilenos salvo otra moneda explícita (USD, MXN). "lucas" = miles ("5 lucas" = 5000),
     "k"/"mil" = miles. Devuelve el número completo.
   - compartido = true SOLO si dice explícitamente que se divide ("a medias", "compartido", "dividimos",
     "50/50", "40/60"). Hablar en plural ("gastamos", "salimos") NO basta.
   - porcentaje_pagador: % que asume quien pagó ("a medias" = 50; "40/60" = 40; "yo pongo el 70%" = 70).
     Si no es compartido, 100.
   - categoria: la más adecuada de la lista según el tipo; si ninguna calza, "Otros" u "Otros ingresos".
   - descripcion breve en minúsculas salvo nombres propios.
2. pago_pareja: transferencias ENTRE ellos para saldar deudas ("le transferí 20 lucas a la Cami",
   "me pagó lo de la cena"). direccion = "yo_pago" si el usuario le pagó a la pareja, "me_pagan" si al revés.
   Esto NO es un gasto ni un ingreso.
3. meta_nueva: crear una meta de ahorro ("queremos juntar 2 millones para México en julio").
   emoji representativo; fecha_objetivo YYYY-MM-DD o "" si no dice.
4. meta_aporte: plata que apartan para una meta existente ("aparté 50 lucas para México").
   meta = nombre EXACTO de una de las metas activas listadas; si no calza ninguna, intencion = "otro" y
   en respuesta pregunta a qué meta.
5. ver_metas: quiere ver cómo van las metas.
6. compras_agregar: agregar cosas a la lista del súper ("agrega leche y 2 kilos de tomates"). Un ítem por
   producto, nombre en minúsculas, cantidad "" si no dice.
7. ver_lista: quiere ver la lista de compras.
8. recordatorio: avisar algo en una fecha/hora ("recuérdame pagar la luz el 5 a las 10",
   "el 3 de marzo es el cumpleaños de la Cami"). fecha_hora "YYYY-MM-DDTHH:MM" en hora de Chile, siempre
   en el futuro; si no da hora usa 09:00. repetir: nunca/diario/semanal/mensual/anual (cumpleaños y
   aniversarios = anual; "todos los meses" = mensual). para = "yo" si es solo para el usuario
   ("recuérdame"), "ambos" si es para los dos ("recuérdanos", fechas especiales, cuentas de la casa).
   texto: lo que hay que recordar, breve, sin la fecha.
9. ver_recordatorios: quiere ver los próximos recordatorios.
10. panorama_agregar: guardar algo que quieren hacer/visitar/ver ("anota el restaurante X para ir",
    "hay que ver la peli Y"). tipo: restaurante/lugar/pelicula/serie/actividad/viaje/otro.
    notas: solo detalles útiles que vengan en el mensaje (dirección, quién lo recomendó); si no hay, "".
11. panorama_sugerir: piden una idea de qué hacer ("¿qué hacemos hoy?", "¿qué peli vemos?").
    filtro_tipo = el tipo pedido o "cualquiera".
12. nota_guardar: guardar un dato de la casa ("la clave del wifi es X", "mi talla de zapatos es 38").
    clave corta en minúsculas ("clave wifi", "talla zapatos rishi"), valor tal cual.
13. nota_buscar: preguntan por un dato guardado ("¿cuál era la clave del wifi?"). busqueda = palabras clave.
14. consulta: preguntas sobre sus gastos o ingresos ("¿cuánto gastamos en uber este año?", "¿en qué
    gastamos más este mes?", "¿cuánto gasté yo en comida la semana pasada?"). Llena el objeto consulta:
    - metrica: "gastos" o "ingresos".
    - desde / hasta: YYYY-MM-DD (inclusive). "este mes" = día 1 del mes hasta hoy; "este año" = 1 de enero
      hasta hoy; "la semana pasada" = lunes a domingo anteriores. Si no dice periodo, usa este mes.
    - categoria: una de la lista o "todas".
    - persona: "yo" si pregunta por lo suyo ("gasté", "mis"), "pareja" si por la otra persona, "ambos" si
      habla en plural o no especifica.
    - agrupar: "categoria" (en qué gastamos más), "mes" (por mes), "persona" (quién gastó más),
      "descripcion" (en qué cosas específicas) o "nada" (solo el total).
    - busqueda: palabra(s) para filtrar por descripción ("uber", "sushi") o "" si no aplica. Si la palabra
      ya corresponde a una categoría completa (ej: "transporte"), usa categoria y deja busqueda vacía.
    Para las demás intenciones llena consulta igual con valores neutros (no se usará).
    Preguntas sobre quién le debe a quién también son consulta con metrica "gastos" y agrupar "nada".
15. tarea_nueva: una tarea de la casa que se repite ("la basura se saca cada 3 días, nos turnamos",
    "yo riego las plantas todas las semanas"). nombre corto con mayúscula inicial ("Sacar la basura"),
    cada_dias (semanal = 7, quincenal = 14, mensual = 30), quien: "turnos" si se turnan o no dice,
    "yo" si la hace quien escribe, "pareja" si la hace la otra persona.
16. ver_tareas: quiere ver las tareas o a quién le toca qué.
17. tarea_hecha: avisa que hizo una tarea ("ya saqué la basura"). busqueda = nombre EXACTO de una de las
    tareas activas listadas; si no calza ninguna, intencion = "otro" y pregunta cuál.
18. menu_semanal: pide ideas de comidas o un menú para la semana. busqueda = preferencias mencionadas
    ("vegetariano", "rápido", "barato", "solo 3 días") o "".
19. perfil_guardar: datos sobre las personas para conocerse y sorprenderse: gustos, comidas favoritas,
    sueños, horario de trabajo, amigos, deportes, música, ideas de regalo ("a la Cami le encanta el sushi",
    "mi horario es de 9 a 18", "nuestros amigos son Juan y Pedro", "sueño con ir a Japón", "juego pádel
    los martes"). Un ítem por dato. sobre: "yo" (quien escribe), "pareja" o "ambos" (de los dos, ej: amigos en
    común, sueños compartidos). categoria: gustos/comida/suenos/trabajo/amigos/deportes/regalos/musica/otro.
    dato: frase corta y clara en tercera persona sin el nombre ("le encanta el sushi", "trabaja de 9 a 18");
    si es de los dos, en plural ("sueñan con ir a Japón", "sus amigos son Juan y la Fran").
    Ojo: datos prácticos de la casa (wifi, contactos, códigos) son nota_guardar, no perfil.
20. perfil_ver: preguntan qué saben de alguien ("¿qué le gusta a la Cami?", "¿cuál es mi horario?",
    "¿qué sueños tenemos?"). Llena perfil_filtro: sobre (yo/pareja/ambos/todos) y categoria (o "todas").
    "¿Qué le gusta a X?" o "¿qué sabes de X?" van con categoria "todas" (los gustos están repartidos en
    comida, deportes, música...); usa una categoría solo si preguntan por algo específico (horario, sueños).
21. pedir_ideas: piden que les RECOMIENDES algo nuevo: una cita, un regalo, un panorama para el finde, una
    sorpresa ("recomiéndanos algo para el sábado", "ideas de regalo para la Cami", "¿qué podríamos hacer
    distinto?"). busqueda = qué tipo de idea piden, con los detalles (presupuesto, para quién, cuándo).
    (Si en cambio preguntan qué hacer de lo que YA tienen anotado, es panorama_sugerir.)
22. editar_movimiento: corregir un gasto/ingreso YA guardado ("corrige el uber de ayer, eran 4500",
    "el último gasto era a medias", "el sushi era comida fuera, no supermercado").
    objetivo: busqueda = palabras para encontrarlo ("uber", "sushi"; "" = el último anotado),
    fecha = YYYY-MM-DD si dice cuándo fue o "".
    cambios: solo lo que cambia; lo demás vacío: monto (0 = no cambia), categoria ("" = no cambia),
    descripcion ("" = no cambia), fecha ("" = no cambia), compartido ("si", "no" o "sin_cambio"),
    porcentaje_pagador (si pasa a compartido: 50 para "a medias", 40 para "40/60"; si no, 0).
    Si el Contexto ya dice cuál movimiento es, deja objetivo vacío.
23. borrar_movimiento: eliminar un gasto/ingreso ya guardado ("borra el gasto del sushi", "elimina el
    último"). objetivo igual que arriba.
24. otro: cualquier otra cosa (saludos, conversación).

Imágenes: si viene una foto de una boleta, voucher o comprobante, la intención es movimientos con UN solo
gasto por el TOTAL pagado (no cada ítem): descripcion = comercio (y qué se compró, breve), fecha = la de la
boleta si se lee, categoria según el comercio. El texto que acompaña la foto puede decir "a medias", etc.
Si la imagen no es una boleta ni comprobante, intencion = "otro" y en respuesta dilo con buena onda.

Fechas: interpreta "hoy", "ayer", "mañana", "el sábado", etc. respecto de la fecha y hora actuales que vienen
en el mensaje. Para movimientos y pagos, si no se menciona fecha usa hoy.

respuesta: SOLO si intencion es "otro", una frase corta con la personalidad de abajo; si no, "".
Si se te entrega una corrección de una interpretación anterior, devuelve la interpretación completa ya
corregida.

Personalidad (solo para respuesta): eres "Pimpollo", un amigo chileno buena onda. Hablas relajado, con
modismos chilenos suaves (wena, cachai, al tiro, bacán, pucha), frases cortas y uno o dos emojis como máximo.
Nunca tomas partido entre los miembros de la pareja ni juzgas en qué gasta cada uno. Si conversan, respondes
breve y recuerdas que pueden contarte gastos, pedirte que anote cosas en la lista, recordatorios o panoramas.

Categorías de gasto: {gastos}
Categorías de ingreso: {ingresos}"""


def _obj(propiedades: dict) -> dict:
    return {"type": "object", "properties": propiedades, "required": list(propiedades),
            "additionalProperties": False}


_TEXTO = {"type": "string"}
_NUMERO = {"type": "number"}
_FECHA = {"type": "string", "format": "date"}


def _campos(categorias: list[str]) -> dict:
    """Los datos que se pueden extraer, por campo. Cada intención usa solo los suyos (ver _CAMPOS_POR_INTENCION)."""
    return {
        "movimientos": {"type": "array", "items": _obj({
            "tipo": {"type": "string", "enum": ["gasto", "ingreso"]},
            "monto": _NUMERO,
            "moneda": {"type": "string", "enum": ["CLP", "USD", "MXN"]},
            "categoria": {"type": "string", "enum": categorias},
            "descripcion": _TEXTO,
            "fecha": _FECHA,
            "compartido": {"type": "boolean"},
            "porcentaje_pagador": _NUMERO,
        })},
        "pagos": {"type": "array", "items": _obj({
            "monto": _NUMERO,
            "direccion": {"type": "string", "enum": ["yo_pago", "me_pagan"]},
            "fecha": _FECHA,
        })},
        "metas_nuevas": {"type": "array", "items": _obj({
            "nombre": _TEXTO, "emoji": _TEXTO, "monto_objetivo": _NUMERO, "fecha_objetivo": _TEXTO,
        })},
        "aportes": {"type": "array", "items": _obj({"meta": _TEXTO, "monto": _NUMERO})},
        "compras": {"type": "array", "items": _obj({"nombre": _TEXTO, "cantidad": _TEXTO})},
        "recordatorios": {"type": "array", "items": _obj({
            "texto": _TEXTO,
            "fecha_hora": _TEXTO,
            "repetir": {"type": "string", "enum": ["nunca", "diario", "semanal", "mensual", "anual"]},
            "para": {"type": "string", "enum": ["yo", "ambos"]},
        })},
        "panoramas": {"type": "array", "items": _obj({
            "titulo": _TEXTO, "tipo": {"type": "string", "enum": TIPOS_PANORAMA}, "notas": _TEXTO,
        })},
        "notas": {"type": "array", "items": _obj({"clave": _TEXTO, "valor": _TEXTO})},
        "filtro_tipo": {"type": "string", "enum": TIPOS_PANORAMA + ["cualquiera"]},
        "busqueda": _TEXTO,
        "perfil": {"type": "array", "items": _obj({
            "sobre": {"type": "string", "enum": ["yo", "pareja", "ambos"]},
            "categoria": {"type": "string", "enum": CATEGORIAS_PERFIL},
            "dato": _TEXTO,
        })},
        "perfil_filtro": _obj({
            "sobre": {"type": "string", "enum": ["yo", "pareja", "ambos", "todos"]},
            "categoria": {"type": "string", "enum": CATEGORIAS_PERFIL + ["todas"]},
        }),
        "tareas": {"type": "array", "items": _obj({
            "nombre": _TEXTO, "cada_dias": {"type": "integer"},
            "quien": {"type": "string", "enum": ["turnos", "yo", "pareja"]},
        })},
        "consulta": _obj({
            "metrica": {"type": "string", "enum": ["gastos", "ingresos"]},
            "desde": _FECHA,
            "hasta": _FECHA,
            "categoria": {"type": "string", "enum": categorias + ["todas"]},
            "persona": {"type": "string", "enum": ["ambos", "yo", "pareja"]},
            "agrupar": {"type": "string", "enum": ["nada", "categoria", "mes", "persona", "descripcion"]},
            "busqueda": _TEXTO,
        }),
        "objetivo": _obj({"busqueda": _TEXTO, "fecha": _TEXTO}),
        "cambios": _obj({
            "monto": _NUMERO, "categoria": _TEXTO, "descripcion": _TEXTO, "fecha": _TEXTO,
            "compartido": {"type": "string", "enum": ["sin_cambio", "si", "no"]}, "porcentaje_pagador": _NUMERO,
        }),
        "respuesta": _TEXTO,
    }


# Paso 2: qué campos extrae cada intención. Las que no están aquí (ver_lista, ver_metas...) no necesitan datos.
_CAMPOS_POR_INTENCION = {
    "movimientos": ["movimientos"], "pago_pareja": ["pagos"], "meta_nueva": ["metas_nuevas"],
    "meta_aporte": ["aportes"], "compras_agregar": ["compras"], "recordatorio": ["recordatorios"],
    "panorama_agregar": ["panoramas"], "panorama_sugerir": ["filtro_tipo"], "nota_guardar": ["notas"],
    "nota_buscar": ["busqueda"], "tarea_nueva": ["tareas"], "tarea_hecha": ["busqueda"],
    "menu_semanal": ["busqueda"], "perfil_guardar": ["perfil"], "perfil_ver": ["perfil_filtro"],
    "pedir_ideas": ["busqueda"], "consulta": ["consulta"], "otro": ["respuesta"],
    "editar_movimiento": ["objetivo", "cambios"], "borrar_movimiento": ["objetivo"],
}

# Valores por defecto de los campos que una intención no extrae (así el resto del bot no tiene que preguntar).
_VACIO = {
    "movimientos": [], "pagos": [], "metas_nuevas": [], "aportes": [], "compras": [], "recordatorios": [],
    "panoramas": [], "notas": [], "perfil": [], "tareas": [], "filtro_tipo": "cualquiera", "busqueda": "",
    "respuesta": "", "perfil_filtro": {"sobre": "todos", "categoria": "todas"},
    "consulta": {"metrica": "gastos", "desde": "", "hasta": "", "categoria": "todas", "persona": "ambos",
                 "agrupar": "nada", "busqueda": ""},
    "objetivo": {"busqueda": "", "fecha": ""},
    "cambios": {"monto": 0, "categoria": "", "descripcion": "", "fecha": "", "compartido": "sin_cambio",
                "porcentaje_pagador": 0},
}


class ErrorInterprete(Exception):
    pass


async def _llamar(sistema: str, mensajes: list[dict], schema: dict) -> dict:
    modelo = config.claude_model()
    extra = {}
    if not modelo.startswith("claude-haiku"):
        # Si el modelo rechaza por política, la API reintenta con un modelo de respaldo.
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    try:
        respuesta = await _client().beta.messages.create(
            model=modelo,
            max_tokens=4000,
            # Las instrucciones son iguales en los dos pasos: se cachean.
            system=[{"type": "text", "text": sistema, "cache_control": {"type": "ephemeral"}}],
            messages=mensajes,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            **extra,
        )
    except anthropic.APIError as e:
        log.exception("Error llamando a Claude")
        raise ErrorInterprete("Pucha, no pude conectarme con la IA. Intenta de nuevo en un rato 🙏") from e
    if respuesta.stop_reason != "end_turn":
        log.warning("Respuesta de Claude con stop_reason=%s", respuesta.stop_reason)
        raise ErrorInterprete("No logré cachar el mensaje. Prueba escribirlo de otra forma 🤔")
    return json.loads(next(b.text for b in respuesta.content if b.type == "text"))


async def interpretar(
    mensaje: str,
    ahora: datetime,
    categorias_gasto: list[str],
    categorias_ingreso: list[str],
    metas_activas: list[str],
    tareas_activas: list[str] = (),
    anterior: tuple[str, str] | None = None,
    contexto: str | None = None,
    imagen: bytes | None = None,
    intencion: str | None = None,
    intenciones_posibles: list[str] | None = None,
) -> dict:
    """Interpreta en dos pasos: (1) la intención, (2) los datos de esa intención con un schema chico.

    Devuelve todos los campos (los que no aplican, vacíos) ya validados, más "crudo" (el JSON del paso 2,
    para correcciones).
    `anterior` = (mensaje_original, crudo_previo) cuando `mensaje` es una corrección (se salta el paso 1).
    `contexto` = pista extra cuando el bot le hizo una pregunta al usuario. `intencion` = forzarla.
    """
    sistema = SISTEMA.format(gastos=", ".join(categorias_gasto), ingresos=", ".join(categorias_ingreso))
    encabezado = (f"Ahora: {DIAS[ahora.weekday()]} {ahora:%Y-%m-%d %H:%M} (hora de Chile).\n"
                  f"Metas activas: {', '.join(metas_activas) or 'ninguna'}.\n"
                  f"Tareas activas: {', '.join(tareas_activas) or 'ninguna'}.\n")
    proximos = feriados.proximos(ahora.date(), dias=45)
    if proximos:
        encabezado += "Próximos feriados y fechas especiales: " + "; ".join(
            f"{f['nombre']} {f['fecha']:%Y-%m-%d}" for f in proximos) + ".\n"
    if contexto:
        encabezado += f"Contexto: {contexto}\n"

    if anterior:
        original, previa = anterior
        intencion = json.loads(previa).get("intencion", "movimientos")
        base = [
            {"role": "user", "content": f"{encabezado}Mensaje: {original}"},
            {"role": "assistant", "content": previa},
            {"role": "user", "content": f"Corrección del usuario: {mensaje}"},
        ]
    else:
        contenido = f"{encabezado}Mensaje: {mensaje}"
        if imagen is not None:
            contenido = [
                {"type": "image", "source": {"type": "base64", "media_type": _tipo_imagen(imagen),
                                             "data": base64.standard_b64encode(imagen).decode()}},
                {"type": "text", "text": contenido},
            ]
        base = [{"role": "user", "content": contenido}]

    # Paso 1: solo la intención.
    if intencion is None:
        # `intenciones_posibles` acota el paso 1 cuando el origen ya lo restringe (ej: un correo del banco).
        paso1 = await _llamar(sistema, base, _obj({"intencion": {"type": "string",
                                                                 "enum": intenciones_posibles or INTENCIONES}}))
        intencion = paso1["intencion"]

    # Paso 2: los datos de esa intención.
    datos = {**{k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                for k, v in _VACIO.items()}, "intencion": intencion, "crudo": json.dumps({"intencion": intencion})}
    campos = _CAMPOS_POR_INTENCION.get(intencion)
    if campos:
        todos = _campos(categorias_gasto + categorias_ingreso)
        schema = _obj({"intencion": {"type": "string", "enum": sorted({intencion, "otro"})},
                       **{c: todos[c] for c in campos}, "respuesta": _TEXTO})
        instruccion = (f"La intención es {intencion}. Extrae sus datos según las reglas. Si al final no "
                       "corresponde (falta información clave), usa intencion \"otro\" y explica en respuesta.")
        if anterior:
            mensajes = base[:-1] + [{"role": "user", "content": f"{base[-1]['content']}\n\n{instruccion}"}]
        elif imagen is not None:
            mensajes = [{"role": "user", "content": base[0]["content"] + [{"type": "text", "text": instruccion}]}]
        else:
            mensajes = [{"role": "user", "content": f"{base[0]['content']}\n\n{instruccion}"}]
        extraido = await _llamar(sistema, mensajes, schema)
        datos.update(extraido)
        datos["crudo"] = json.dumps(extraido, ensure_ascii=False)

    return _validar(datos, ahora, categorias_gasto, categorias_ingreso, metas_activas, tareas_activas)


def _validar(datos: dict, ahora: datetime, categorias_gasto: list[str], categorias_ingreso: list[str],
             metas_activas: list[str], tareas_activas: list[str]) -> dict:
    hoy = ahora.date()
    datos["movimientos"] = [_validar_movimiento(m, categorias_gasto, categorias_ingreso, hoy)
                            for m in datos["movimientos"]]
    datos["pagos"] = [_validar_pago(p, hoy) for p in datos["pagos"]]
    datos["metas_nuevas"] = [_validar_meta(m) for m in datos["metas_nuevas"]]
    datos["aportes"] = [{"meta": a["meta"], "monto": _monto(a["monto"])} for a in datos["aportes"]
                        if a["meta"] in metas_activas]
    datos["recordatorios"] = [_validar_recordatorio(r, ahora) for r in datos["recordatorios"]]
    datos["tareas"] = [t for t in datos["tareas"] if t["cada_dias"] > 0]
    c = datos["consulta"]
    c["desde"] = _fecha(c["desde"], hoy.replace(day=1))
    c["hasta"] = _fecha(c["hasta"], hoy)
    if c["desde"] > c["hasta"]:
        c["desde"], c["hasta"] = c["hasta"], c["desde"]
    o = datos["objetivo"]
    o["fecha"] = _fecha(o["fecha"], None) if o["fecha"] else None
    k = datos["cambios"]
    k["fecha"] = _fecha(k["fecha"], None) if k["fecha"] else None
    k["monto"] = Decimal(str(k["monto"])).quantize(Decimal("0.01")) if k["monto"] and k["monto"] > 0 else None
    k["porcentaje_pagador"] = Decimal(str(k["porcentaje_pagador"])) if 0 < k["porcentaje_pagador"] <= 100 else Decimal(50)

    # Si la intención quedó sin datos, se trata como conversación.
    requeridos = {"movimientos": "movimientos", "pago_pareja": "pagos", "meta_nueva": "metas_nuevas",
                  "meta_aporte": "aportes", "compras_agregar": "compras", "recordatorio": "recordatorios",
                  "panorama_agregar": "panoramas", "nota_guardar": "notas", "tarea_nueva": "tareas",
                  "perfil_guardar": "perfil"}
    lista = requeridos.get(datos["intencion"])
    if lista and not datos[lista]:
        datos["intencion"] = "otro"
    if datos["intencion"] == "tarea_hecha" and datos["busqueda"] not in tareas_activas:
        datos["intencion"] = "ver_tareas"
    return datos


def _tipo_imagen(datos: bytes) -> str:
    if datos.startswith(b"\x89PNG"):
        return "image/png"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"  # las fotos de Telegram llegan como JPEG


def _monto(valor) -> Decimal:
    monto = Decimal(str(valor)).quantize(Decimal("0.01"))
    if monto <= 0:
        raise ErrorInterprete("El monto tiene que ser mayor que cero.")
    return monto


def _fecha(texto: str, por_defecto: date) -> date:
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return por_defecto


def _validar_movimiento(m: dict, cat_gasto: list[str], cat_ingreso: list[str], hoy: date) -> dict:
    """Normaliza un movimiento y corrige inconsistencias que el schema no puede impedir."""
    validas = cat_gasto if m["tipo"] == "gasto" else cat_ingreso
    categoria = m["categoria"] if m["categoria"] in validas else ("Otros" if m["tipo"] == "gasto" else "Otros ingresos")

    porcentaje = None
    if m["tipo"] == "gasto" and m["compartido"]:
        porcentaje = Decimal(str(m["porcentaje_pagador"]))
        if not 0 <= porcentaje <= 100:
            porcentaje = Decimal(50)

    return {
        "tipo": m["tipo"],
        "monto": _monto(m["monto"]),
        "moneda": m["moneda"],
        "categoria": categoria,
        "descripcion": m["descripcion"].strip(),
        "fecha": _fecha(m["fecha"], hoy),
        "porcentaje_pagador": porcentaje,
    }


def _validar_pago(p: dict, hoy: date) -> dict:
    return {"monto": _monto(p["monto"]), "direccion": p["direccion"], "fecha": _fecha(p["fecha"], hoy)}


def _validar_meta(m: dict) -> dict:
    fecha = None
    if m["fecha_objetivo"]:
        try:
            fecha = date.fromisoformat(m["fecha_objetivo"])
        except ValueError:
            pass
    return {"nombre": m["nombre"].strip(), "emoji": m["emoji"].strip()[:4] or "🎯",
            "monto_objetivo": _monto(m["monto_objetivo"]), "fecha_objetivo": fecha}


def _validar_recordatorio(r: dict, ahora: datetime) -> dict:
    try:
        fecha_hora = datetime.fromisoformat(r["fecha_hora"]).replace(tzinfo=ZONA)
    except ValueError:
        fecha_hora = datetime.combine(ahora.date(), time(9, 0), tzinfo=ZONA)
    return {"texto": r["texto"].strip(), "fecha_hora": fecha_hora, "repetir": r["repetir"], "para": r["para"]}


MENU_SISTEMA = """Eres "Pimpollo", amigo chileno buena onda de una pareja en Chile. Propón un menú de cenas caseras para la
semana: platos variados, ricos y realistas para cocinar en casa un día de semana, con ingredientes fáciles de
encontrar en un supermercado chileno. Respeta las preferencias que te den. Arma además UNA lista de compras
consolidada (sumando cantidades entre platos, para 2 personas), sin básicos de despensa (sal, aceite,
pimienta, azúcar). comentario: una frase corta con modismos chilenos suaves y máximo un emoji."""


async def generar_menu(preferencias: str) -> dict:
    """Menú semanal generado por Claude: {"platos": [{dia, plato}], "ingredientes": [{nombre, cantidad}], "comentario"}."""
    schema = _obj({
        "platos": {"type": "array", "items": _obj({"dia": {"type": "string"}, "plato": {"type": "string"}})},
        "ingredientes": {"type": "array", "items": _obj({"nombre": {"type": "string"}, "cantidad": {"type": "string"}})},
        "comentario": {"type": "string"},
    })
    try:
        respuesta = await _client().messages.create(
            model=config.claude_model(),
            max_tokens=6000,
            system=MENU_SISTEMA,
            messages=[{"role": "user", "content": f"Preferencias: {preferencias or 'ninguna en especial'}"}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.APIError as e:
        log.exception("Error generando menú")
        raise ErrorInterprete("Pucha, no pude armar el menú ahora. Intenta en un rato 🙏") from e
    if respuesta.stop_reason != "end_turn":
        raise ErrorInterprete("No me salió el menú, pídemelo de nuevo 🙏")
    return json.loads(next(b.text for b in respuesta.content if b.type == "text"))


IDEAS_SISTEMA = """Eres "Pimpollo", amigo chileno buena onda de una pareja que vive en Chile. Te piden ideas (citas,
regalos, panoramas, películas, sorpresas). Propón entre 3 y 5 ideas concretas, realistas en Chile y distintas
entre sí, considerando la fecha, la estación y los feriados cercanos (alguna barata o gratis si no dicen
presupuesto). titulo corto; descripcion de 1 a 2 frases; tipo según la lista.

Cómo usar el perfil (muy importante):
- El perfil es contexto opcional, NO una lista de cosas que meter. Usa un dato SOLO si tiene relación directa con
  lo que piden: para una película usa gustos de cine, series o géneros; para un regalo, los gustos de esa persona;
  para salir a comer, sus comidas favoritas. NO mezcles cosas no pedidas (ej: si piden una película, no la
  combines con su comida favorita).
- Nunca uses el mismo dato del perfil en más de una idea. Está bien que varias ideas no usen el perfil.
- Si usas un dato, menciónalo de forma natural en la descripcion; si no, no lo fuerces.
- No repitas ideas que ya sugeriste hace poco (vienen en el contexto). Sorprende con variedad.
- Si las ideas son para UNA persona (regalo, cumpleaños, sorpresa), básalas en los gustos de ESA persona.
- No inventes datos de ellos que no estén en el contexto. Nunca tomes partido ni juzgues.

comentario: frase corta con modismos chilenos suaves y máximo un emoji."""


async def generar_ideas(pedido: str, contexto: str) -> dict:
    """{"ideas": [{titulo, tipo, descripcion}], "comentario"} pensadas con el perfil de la pareja."""
    schema = _obj({
        "ideas": {"type": "array", "items": _obj({
            "titulo": {"type": "string"},
            "tipo": {"type": "string", "enum": TIPOS_PANORAMA},
            "descripcion": {"type": "string"},
        })},
        "comentario": {"type": "string"},
    })
    try:
        respuesta = await _client().messages.create(
            model=config.claude_model(),
            max_tokens=4000,
            system=IDEAS_SISTEMA,
            messages=[{"role": "user",
                       "content": f"{contexto}\n\nLo que piden: {pedido or 'ideas para hacer juntos'}"}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.APIError as e:
        log.exception("Error generando ideas")
        raise ErrorInterprete("Pucha, no se me ocurrió nada ahora. Intenta en un rato 🙏") from e
    if respuesta.stop_reason != "end_turn":
        raise ErrorInterprete("No me salieron las ideas, pídemelas de nuevo 🙏")
    return json.loads(next(b.text for b in respuesta.content if b.type == "text"))
