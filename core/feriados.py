"""Feriados de Chile y fechas especiales (San Valentín, Día de la Madre, Navidad...)."""

import json
import logging
import urllib.request
from datetime import date, timedelta

from core.db import conectar

log = logging.getLogger(__name__)

URL = "https://date.nager.at/api/v3/PublicHolidays/{anio}/CL"  # gratuita, sin API key

_cache: dict[tuple[date, int], list[dict]] = {}


def _enesimo_domingo(anio: int, mes: int, n: int) -> date:
    primero = date(anio, mes, 1)
    return primero + timedelta(days=(6 - primero.weekday()) % 7 + 7 * (n - 1))


def fechas_especiales(anio: int) -> list[dict]:
    """Fechas que no son feriado pero importan en pareja. `regalo` = el bot manda ideas con anticipación."""
    return [
        {"fecha": date(anio, 2, 14), "nombre": "San Valentín", "regalo": True},
        {"fecha": _enesimo_domingo(anio, 5, 2), "nombre": "Día de la Madre", "regalo": False},
        {"fecha": _enesimo_domingo(anio, 6, 3), "nombre": "Día del Padre", "regalo": False},
        {"fecha": _enesimo_domingo(anio, 8, 2), "nombre": "Día del Niño", "regalo": False},
        {"fecha": date(anio, 10, 31), "nombre": "Halloween", "regalo": False},
        {"fecha": date(anio, 12, 24), "nombre": "Nochebuena", "regalo": False},
        {"fecha": date(anio, 12, 25), "nombre": "Navidad", "regalo": True},
        {"fecha": date(anio, 12, 31), "nombre": "Año Nuevo (víspera)", "regalo": False},
    ]


def _cargar_anio(anio: int) -> None:
    """Descarga los feriados nacionales del año si aún no están guardados."""
    with conectar() as conn:
        if conn.execute("SELECT 1 FROM feriados WHERE EXTRACT(YEAR FROM fecha) = %s LIMIT 1", (anio,)).fetchone():
            return
        try:
            with urllib.request.urlopen(URL.format(anio=anio), timeout=15) as r:
                datos = json.load(r)
        except Exception:
            log.exception("No pude descargar los feriados de %s", anio)
            return
        for f in datos:
            if f.get("global", True):  # solo nacionales (no los regionales como el de Arica)
                conn.execute("INSERT INTO feriados (fecha, nombre) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                             (f["date"], f["localName"]))


def proximos(desde: date, dias: int = 60) -> list[dict]:
    """Feriados y fechas especiales entre `desde` y `desde + dias`, ordenados.

    Cada uno: {fecha, nombre, feriado: bool, regalo: bool}.
    """
    clave = (desde, dias)
    if clave in _cache:
        return _cache[clave]
    hasta = desde + timedelta(days=dias)
    for anio in {desde.year, hasta.year}:
        _cargar_anio(anio)
    with conectar() as conn:
        legales = conn.execute("SELECT fecha, nombre FROM feriados WHERE fecha BETWEEN %s AND %s",
                               (desde, hasta)).fetchall()
    resultado = [{"fecha": f["fecha"], "nombre": f["nombre"], "feriado": True,
                  "regalo": f["fecha"].month == 12 and f["fecha"].day == 25} for f in legales]
    por_fecha = {f["fecha"]: f for f in resultado}
    for anio in {desde.year, hasta.year}:
        for e in fechas_especiales(anio):
            if not desde <= e["fecha"] <= hasta:
                continue
            if e["fecha"] in por_fecha:  # misma fecha que un feriado: se juntan (ej: Halloween)
                f = por_fecha[e["fecha"]]
                if e["nombre"] not in f["nombre"]:
                    f["nombre"] += f" · {e['nombre']}"
                f["regalo"] = f["regalo"] or e["regalo"]
            else:
                resultado.append({**e, "feriado": False})
    resultado.sort(key=lambda f: f["fecha"])
    _cache.clear()  # solo se guarda el último cálculo (cambia una vez al día)
    _cache[clave] = resultado
    return resultado


def es_finde_largo(fecha: date) -> bool:
    """¿Este feriado arma un fin de semana largo (cae lunes o viernes, o junto a otro feriado)?"""
    return fecha.weekday() in (0, 4)
