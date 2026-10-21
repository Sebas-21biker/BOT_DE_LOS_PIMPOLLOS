"""Cuánto cuesta el bot en la API de Claude: se registra cada llamada y se calcula con SQL."""

import logging
from datetime import date

from core.db import conectar

log = logging.getLogger(__name__)

# USD por millón de tokens (precios de la API de Anthropic). Caché: escribir 1,25x la entrada, leer 0,1x.
PRECIOS = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
PRECIO_DESCONOCIDO = (4.00, 20.00)  # si se cambia a un modelo no listado, se asume el más caro


def registrar(modelo: str, motivo: str, uso) -> None:
    """Guarda los tokens de una respuesta de la API. Nunca interrumpe al bot si falla."""
    try:
        with conectar() as conn:
            conn.execute(
                """INSERT INTO uso_ia (modelo, motivo, entrada, cache_escritura, cache_lectura, salida)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (modelo, motivo, uso.input_tokens, uso.cache_creation_input_tokens or 0,
                 uso.cache_read_input_tokens or 0, uso.output_tokens))
    except Exception:
        log.exception("No pude registrar el uso de la IA")


def _sql_precios() -> str:
    """VALUES con los precios para cruzar en SQL."""
    filas = ", ".join(f"('{m}', {e}, {s})" for m, (e, s) in PRECIOS.items())
    return f"(VALUES {filas}) AS p(modelo, entrada, salida)"


def resumen_mes(mes: date) -> dict:
    """Llamadas y costo en USD del mes, total y por motivo."""
    e_def, s_def = PRECIO_DESCONOCIDO
    costo = (f"(u.entrada + 1.25 * u.cache_escritura + 0.1 * u.cache_lectura) * COALESCE(p.entrada, {e_def}) / 1e6"
             f" + u.salida * COALESCE(p.salida, {s_def}) / 1e6")
    with conectar() as conn:
        por_motivo = conn.execute(
            f"""
            SELECT u.motivo, COUNT(*) AS llamadas, SUM({costo}) AS usd
            FROM uso_ia u LEFT JOIN {_sql_precios()} ON p.modelo = u.modelo
            WHERE date_trunc('month', u.creado_en AT TIME ZONE 'America/Santiago') = %s
            GROUP BY u.motivo ORDER BY usd DESC
            """, (mes.replace(day=1),)).fetchall()
    return {"por_motivo": por_motivo,
            "llamadas": sum(f["llamadas"] for f in por_motivo),
            "usd": sum(f["usd"] for f in por_motivo)}
