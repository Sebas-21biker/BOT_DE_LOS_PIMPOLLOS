"""Tipo de cambio a CLP. Se consulta una vez al día y se guarda en `tipos_cambio`."""

import json
import logging
import urllib.request
from datetime import date
from decimal import Decimal

from core.db import conectar

log = logging.getLogger(__name__)

URL = "https://open.er-api.com/v6/latest/USD"  # gratuita, sin API key; trae CLP y MXN
MONEDAS = ("USD", "MXN")


class SinTipoDeCambio(Exception):
    pass


def _descargar() -> dict[str, Decimal]:
    """CLP por 1 unidad de cada moneda soportada."""
    with urllib.request.urlopen(URL, timeout=15) as r:
        tasas = json.load(r)["rates"]
    clp_por_usd = Decimal(str(tasas["CLP"]))
    return {"USD": clp_por_usd, "MXN": clp_por_usd / Decimal(str(tasas["MXN"]))}


def tasa(moneda: str, fecha: date | None = None) -> Decimal:
    """Cuántos CLP vale 1 unidad de `moneda`. Usa el valor guardado del día; si no hay, lo descarga."""
    if moneda == "CLP":
        return Decimal(1)
    fecha = fecha or date.today()
    with conectar() as conn:
        fila = conn.execute(
            "SELECT valor FROM tipos_cambio WHERE fecha = %s AND moneda_origen = %s AND moneda_destino = 'CLP'",
            (fecha, moneda)).fetchone()
        if fila:
            return fila["valor"]
        try:
            nuevas = _descargar()
        except Exception:
            log.exception("No pude descargar el tipo de cambio")
            ultima = conn.execute(
                """SELECT valor FROM tipos_cambio WHERE moneda_origen = %s AND moneda_destino = 'CLP'
                   ORDER BY fecha DESC LIMIT 1""", (moneda,)).fetchone()
            if ultima:
                return ultima["valor"]
            raise SinTipoDeCambio(moneda)
        for codigo, valor in nuevas.items():
            conn.execute(
                """INSERT INTO tipos_cambio (fecha, moneda_origen, moneda_destino, valor)
                   VALUES (%s, %s, 'CLP', %s) ON CONFLICT DO NOTHING""",
                (fecha, codigo, round(valor, 6)))
        return round(nuevas[moneda], 6)
