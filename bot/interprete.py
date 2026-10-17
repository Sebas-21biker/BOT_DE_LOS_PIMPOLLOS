"""Interpreta mensajes en lenguaje natural con Claude -> movimientos estructurados.

Claude solo traduce texto a datos; no calcula totales ni saldos.
"""

import json
import logging
from datetime import date
from decimal import Decimal

import anthropic

from core import config

log = logging.getLogger(__name__)

_cliente: anthropic.AsyncAnthropic | None = None


def _client() -> anthropic.AsyncAnthropic:
    global _cliente
    if _cliente is None:
        _cliente = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key())
    return _cliente


SISTEMA = """Eres el intérprete de un bot de finanzas de una pareja en Chile. Recibes mensajes \
informales en español chileno y los conviertes en movimientos (gastos o ingresos) estructurados.

Reglas:
- Quien escribe el mensaje es quien pagó el gasto o recibió el ingreso.
- Un mensaje puede traer varios movimientos; devuelve uno por cada monto distinto.
- Montos en pesos chilenos salvo que se diga otra moneda (dólares/USD, pesos mexicanos/MXN).
  "lucas" = miles ("5 lucas" = 5000), "k" o "mil" = miles. Devuelve el número completo.
- compartido = true SOLO si el mensaje dice explícitamente que se divide: "a medias", "compartido",
  "dividimos", "pagamos entre los dos", "50/50", "40/60", etc. Usar plural ("gastamos", "salimos")
  NO basta: en ese caso es individual de quien escribe.
- porcentaje_pagador: % que asume quien pagó. "a medias"/"compartido" = 50. "40/60" = 40 (el primer
  número es de quien paga). Si "yo pongo el 70%" = 70. Si no es compartido, usa 100.
- categoria: elige la más adecuada de la lista según el tipo. Si ninguna calza, "Otros" (gasto) u
  "Otros ingresos" (ingreso). Nunca uses una categoría de gasto para un ingreso ni al revés.
- descripcion: breve y clara, en minúsculas salvo nombres propios (ej: "uber", "cena con amigos",
  "venta de dulces a juan").
- fecha: YYYY-MM-DD. Interpreta "hoy", "ayer", "el sábado", etc. respecto de la fecha de hoy que
  viene en el mensaje. Si no se menciona, usa hoy.
- intencion: "registrar" si hay al menos un movimiento; "consulta" si pregunta por sus finanzas
  (cuánto gastamos, cuánto debo, etc.); "otro" para cualquier otra cosa. Si no es "registrar",
  movimientos = [].
- respuesta: si intencion es "otro", una frase corta con la personalidad descrita abajo; si no, "".
- Si se te entrega una corrección de una interpretación anterior, devuelve la interpretación completa
  ya corregida (todos los movimientos, no solo el cambio).

Personalidad (solo para el campo respuesta): eres "Pimpollo", un amigo chileno buena onda. Hablas
relajado, con modismos chilenos suaves (wena, cachai, al tiro, bacán, pucha), frases cortas y uno o dos
emojis como máximo. Nunca tomas partido entre los miembros de la pareja ni juzgas en qué gasta cada uno.
Si te saludan o conversan, respondes breve y recuerdas que pueden contarte sus gastos o ingresos.

Categorías de gasto: {gastos}
Categorías de ingreso: {ingresos}"""


def _schema(categorias: list[str]) -> dict:
    movimiento = {
        "type": "object",
        "properties": {
            "tipo": {"type": "string", "enum": ["gasto", "ingreso"]},
            "monto": {"type": "number"},
            "moneda": {"type": "string", "enum": ["CLP", "USD", "MXN"]},
            "categoria": {"type": "string", "enum": categorias},
            "descripcion": {"type": "string"},
            "fecha": {"type": "string", "format": "date"},
            "compartido": {"type": "boolean"},
            "porcentaje_pagador": {"type": "number"},
        },
        "required": ["tipo", "monto", "moneda", "categoria", "descripcion", "fecha",
                     "compartido", "porcentaje_pagador"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "intencion": {"type": "string", "enum": ["registrar", "consulta", "otro"]},
            "movimientos": {"type": "array", "items": movimiento},
            "respuesta": {"type": "string"},
        },
        "required": ["intencion", "movimientos", "respuesta"],
        "additionalProperties": False,
    }


class ErrorInterprete(Exception):
    pass


async def interpretar(
    mensaje: str,
    hoy: date,
    categorias_gasto: list[str],
    categorias_ingreso: list[str],
    anterior: tuple[str, str] | None = None,
) -> dict:
    """Devuelve {"intencion", "movimientos": [...], "respuesta", "crudo"} ya validado.

    `anterior` = (mensaje_original, crudo_previo) cuando `mensaje` es una corrección;
    `crudo` es el JSON tal como lo devolvió Claude, para pasarlo en una corrección posterior.
    """
    sistema = SISTEMA.format(gastos=", ".join(categorias_gasto), ingresos=", ".join(categorias_ingreso))
    if anterior:
        original, previa = anterior
        mensajes = [
            {"role": "user", "content": f"Hoy es {hoy.isoformat()}.\nMensaje: {original}"},
            {"role": "assistant", "content": previa},
            {"role": "user", "content": f"Corrección del usuario: {mensaje}"},
        ]
    else:
        mensajes = [{"role": "user", "content": f"Hoy es {hoy.isoformat()}.\nMensaje: {mensaje}"}]

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
        raise ErrorInterprete("No pude conectarme con la IA, intenta de nuevo en un rato.") from e

    if respuesta.stop_reason != "end_turn":
        log.warning("Respuesta de Claude con stop_reason=%s", respuesta.stop_reason)
        raise ErrorInterprete("No logré interpretar el mensaje. Prueba escribirlo de otra forma.")

    texto = next(b.text for b in respuesta.content if b.type == "text")
    datos = json.loads(texto)
    datos["crudo"] = texto
    datos["movimientos"] = [_validar(m, categorias_gasto, categorias_ingreso, hoy) for m in datos["movimientos"]]
    if datos["intencion"] == "registrar" and not datos["movimientos"]:
        datos["intencion"] = "otro"
    return datos


def _validar(m: dict, cat_gasto: list[str], cat_ingreso: list[str], hoy: date) -> dict:
    """Normaliza un movimiento y corrige inconsistencias que el schema no puede impedir."""
    monto = Decimal(str(m["monto"])).quantize(Decimal("0.01"))
    if monto <= 0:
        raise ErrorInterprete("El monto tiene que ser mayor que cero.")

    validas = cat_gasto if m["tipo"] == "gasto" else cat_ingreso
    categoria = m["categoria"] if m["categoria"] in validas else ("Otros" if m["tipo"] == "gasto" else "Otros ingresos")

    try:
        fecha = date.fromisoformat(m["fecha"])
    except ValueError:
        fecha = hoy

    porcentaje = None
    if m["tipo"] == "gasto" and m["compartido"]:
        porcentaje = Decimal(str(m["porcentaje_pagador"]))
        if not 0 <= porcentaje <= 100:
            porcentaje = Decimal(50)

    return {
        "tipo": m["tipo"],
        "monto": monto,
        "moneda": m["moneda"],
        "categoria": categoria,
        "descripcion": m["descripcion"].strip(),
        "fecha": fecha,
        "porcentaje_pagador": porcentaje,
    }
