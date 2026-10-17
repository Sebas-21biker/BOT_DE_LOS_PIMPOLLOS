"""Interpreta mensajes en lenguaje natural con Claude -> datos estructurados.

Claude solo traduce texto a datos; no calcula totales ni saldos.
"""

import json
import logging
from datetime import date, datetime, time
from decimal import Decimal

import anthropic

from core import config
from core.finanzas import ZONA

log = logging.getLogger(__name__)

_cliente: anthropic.AsyncAnthropic | None = None


def _client() -> anthropic.AsyncAnthropic:
    global _cliente
    if _cliente is None:
        _cliente = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key())
    return _cliente


DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
TIPOS_PANORAMA = ["restaurante", "lugar", "pelicula", "serie", "actividad", "viaje", "otro"]
INTENCIONES = [
    "movimientos", "pago_pareja", "meta_nueva", "meta_aporte", "ver_metas",
    "compras_agregar", "ver_lista", "recordatorio", "ver_recordatorios",
    "panorama_agregar", "panorama_sugerir", "nota_guardar", "nota_buscar",
    "consulta", "otro",
]

SISTEMA = """Eres el intérprete de "Pimpollo", el bot de una pareja en Chile. Recibes mensajes informales en \
español chileno y los conviertes en datos estructurados. Quien escribe es "el usuario"; la otra persona \
de la pareja es "la pareja".

Elige UNA intención y llena solo su lista; deja las demás listas vacías.

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
14. consulta: preguntas sobre sus finanzas (cuánto gastamos, cuánto debo...).
15. otro: cualquier otra cosa (saludos, conversación).

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


def _schema(categorias: list[str]) -> dict:
    texto = {"type": "string"}
    numero = {"type": "number"}
    return _obj({
        "intencion": {"type": "string", "enum": INTENCIONES},
        "movimientos": {"type": "array", "items": _obj({
            "tipo": {"type": "string", "enum": ["gasto", "ingreso"]},
            "monto": numero,
            "moneda": {"type": "string", "enum": ["CLP", "USD", "MXN"]},
            "categoria": {"type": "string", "enum": categorias},
            "descripcion": texto,
            "fecha": {"type": "string", "format": "date"},
            "compartido": {"type": "boolean"},
            "porcentaje_pagador": numero,
        })},
        "pagos": {"type": "array", "items": _obj({
            "monto": numero,
            "direccion": {"type": "string", "enum": ["yo_pago", "me_pagan"]},
            "fecha": {"type": "string", "format": "date"},
        })},
        "metas_nuevas": {"type": "array", "items": _obj({
            "nombre": texto, "emoji": texto, "monto_objetivo": numero, "fecha_objetivo": texto,
        })},
        "aportes": {"type": "array", "items": _obj({"meta": texto, "monto": numero})},
        "compras": {"type": "array", "items": _obj({"nombre": texto, "cantidad": texto})},
        "recordatorios": {"type": "array", "items": _obj({
            "texto": texto,
            "fecha_hora": texto,
            "repetir": {"type": "string", "enum": ["nunca", "diario", "semanal", "mensual", "anual"]},
            "para": {"type": "string", "enum": ["yo", "ambos"]},
        })},
        "panoramas": {"type": "array", "items": _obj({
            "titulo": texto, "tipo": {"type": "string", "enum": TIPOS_PANORAMA}, "notas": texto,
        })},
        "notas": {"type": "array", "items": _obj({"clave": texto, "valor": texto})},
        "filtro_tipo": {"type": "string", "enum": TIPOS_PANORAMA + ["cualquiera"]},
        "busqueda": texto,
        "respuesta": texto,
    })


class ErrorInterprete(Exception):
    pass


async def interpretar(
    mensaje: str,
    ahora: datetime,
    categorias_gasto: list[str],
    categorias_ingreso: list[str],
    metas_activas: list[str],
    anterior: tuple[str, str] | None = None,
    contexto: str | None = None,
) -> dict:
    """Devuelve el JSON de Claude ya validado, más "crudo" (el JSON original, para correcciones).

    `anterior` = (mensaje_original, crudo_previo) cuando `mensaje` es una corrección.
    `contexto` = pista extra cuando el bot le hizo una pregunta al usuario (ej: está creando una meta).
    """
    sistema = SISTEMA.format(gastos=", ".join(categorias_gasto), ingresos=", ".join(categorias_ingreso))
    encabezado = (f"Ahora: {DIAS[ahora.weekday()]} {ahora:%Y-%m-%d %H:%M} (hora de Chile).\n"
                  f"Metas activas: {', '.join(metas_activas) or 'ninguna'}.\n")
    if contexto:
        encabezado += f"Contexto: {contexto}\n"
    if anterior:
        original, previa = anterior
        mensajes = [
            {"role": "user", "content": f"{encabezado}Mensaje: {original}"},
            {"role": "assistant", "content": previa},
            {"role": "user", "content": f"Corrección del usuario: {mensaje}"},
        ]
    else:
        mensajes = [{"role": "user", "content": f"{encabezado}Mensaje: {mensaje}"}]

    modelo = config.claude_model()
    extra = {}
    if not modelo.startswith("claude-haiku"):
        # Si el modelo rechaza por política, la API reintenta con un modelo de respaldo.
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}

    try:
        respuesta = await _client().beta.messages.create(
            model=modelo,
            max_tokens=4000,
            system=sistema,
            messages=mensajes,
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": _schema(categorias_gasto + categorias_ingreso)},
            },
            **extra,
        )
    except anthropic.APIError as e:
        log.exception("Error llamando a Claude")
        raise ErrorInterprete("Pucha, no pude conectarme con la IA. Intenta de nuevo en un rato 🙏") from e

    if respuesta.stop_reason != "end_turn":
        log.warning("Respuesta de Claude con stop_reason=%s", respuesta.stop_reason)
        raise ErrorInterprete("No logré cachar el mensaje. Prueba escribirlo de otra forma 🤔")

    texto = next(b.text for b in respuesta.content if b.type == "text")
    datos = json.loads(texto)
    datos["crudo"] = texto
    hoy = ahora.date()
    datos["movimientos"] = [_validar_movimiento(m, categorias_gasto, categorias_ingreso, hoy)
                            for m in datos["movimientos"]]
    datos["pagos"] = [_validar_pago(p, hoy) for p in datos["pagos"]]
    datos["metas_nuevas"] = [_validar_meta(m) for m in datos["metas_nuevas"]]
    datos["aportes"] = [{"meta": a["meta"], "monto": _monto(a["monto"])} for a in datos["aportes"]
                        if a["meta"] in metas_activas]
    datos["recordatorios"] = [_validar_recordatorio(r, ahora) for r in datos["recordatorios"]]

    # Si la intención quedó sin datos, se trata como conversación.
    requeridos = {"movimientos": "movimientos", "pago_pareja": "pagos", "meta_nueva": "metas_nuevas",
                  "meta_aporte": "aportes", "compras_agregar": "compras", "recordatorio": "recordatorios",
                  "panorama_agregar": "panoramas", "nota_guardar": "notas"}
    lista = requeridos.get(datos["intencion"])
    if lista and not datos[lista]:
        datos["intencion"] = "otro"
    return datos


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
