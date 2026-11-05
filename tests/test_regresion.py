"""Regresión de lo que ya funcionaba: registro rápido, validación de lo que entrega la IA, pruebas, dashboard."""

import hashlib
import hmac
import json
import time
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlencode

import pytest

from bot import comun, interprete
from core import costos, parser
from core.finanzas import ZONA
from dashboard.web import validar_init_data

CATS_GASTO = ["Supermercado", "Transporte", "Comida fuera", "Otros"]
CATS_INGRESO = ["Sueldo", "Otros ingresos"]
AHORA = datetime(2026, 11, 5, 12, 0, tzinfo=ZONA)


# ---------- /gasto sin IA ----------

@pytest.mark.parametrize("args,monto,pct", [
    ("15000 super lider", Decimal(15000), None),
    ("32.500 cena sushi compartido", Decimal(32500), Decimal(50)),
    ("20k arriendo 40/60", Decimal(20000), Decimal(40)),
    ("45 lucas cena a medias", Decimal(45000), Decimal(50)),
])
def test_parser_gasto(args, monto, pct):
    r = parser.interpretar(args.split(), CATS_GASTO)
    assert (r.monto, r.porcentaje_pagador) == (monto, pct)


def test_parser_categoria_y_errores():
    assert parser.interpretar("3500 uber".split(), CATS_GASTO).categoria == "Transporte"
    with pytest.raises(parser.ErrorFormato):
        parser.interpretar("cena".split(), CATS_GASTO)
    with pytest.raises(parser.ErrorFormato):
        parser.interpretar("1000 cena 30/60".split(), CATS_GASTO)


# ---------- validación de lo que devuelve Claude ----------

def _datos(**campos):
    base = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
            for k, v in interprete._VACIO.items()}
    return {**base, "crudo": "{}", **campos}


def _validar(datos):
    return interprete._validar(datos, AHORA, CATS_GASTO, CATS_INGRESO, ["México"], ["Sacar la basura"])


def test_movimiento_individual_y_categoria_invalida():
    d = _validar(_datos(intencion="movimientos", movimientos=[{
        "tipo": "gasto", "monto": 3500, "moneda": "CLP", "categoria": "Inventada", "descripcion": " uber ",
        "fecha": "", "compartido": False, "porcentaje_pagador": 100}]))
    m = d["movimientos"][0]
    assert (m["categoria"], m["descripcion"], m["porcentaje_pagador"], str(m["fecha"])) == \
        ("Otros", "uber", None, "2026-11-05")


def test_movimiento_compartido():
    d = _validar(_datos(intencion="movimientos", movimientos=[{
        "tipo": "gasto", "monto": 40000, "moneda": "CLP", "categoria": "Comida fuera", "descripcion": "cena",
        "fecha": "2026-11-04", "compartido": True, "porcentaje_pagador": 140}]))
    assert d["movimientos"][0]["porcentaje_pagador"] == Decimal(50)  # fuera de rango -> 50/50


def test_monto_cero_se_rechaza():
    with pytest.raises(interprete.ErrorInterprete):
        _validar(_datos(intencion="movimientos", movimientos=[{
            "tipo": "gasto", "monto": 0, "moneda": "CLP", "categoria": "Otros", "descripcion": "x",
            "fecha": "", "compartido": False, "porcentaje_pagador": 100}]))


def test_intencion_sin_datos_pasa_a_conversacion():
    assert _validar(_datos(intencion="movimientos"))["intencion"] == "otro"
    assert _validar(_datos(intencion="meta_aporte", aportes=[{"meta": "Japón", "monto": 1000}]))["intencion"] == "otro"
    assert _validar(_datos(intencion="tarea_hecha", busqueda="Lavar"))["intencion"] == "ver_tareas"


def test_consulta_fechas_invertidas():
    d = _validar(_datos(intencion="consulta", consulta={**interprete._VACIO["consulta"],
                                                        "desde": "2026-11-05", "hasta": "2026-10-01"}))
    assert str(d["consulta"]["desde"]) == "2026-10-01"


# ---------- marca de prueba ----------

def test_marca_de_prueba():
    assert comun.es_prueba("uber 3500 test") and comun.es_prueba("#prueba cena")
    assert not comun.es_prueba("fui al testeo de covid")
    assert comun.sin_marca_prueba("uber 3500 test") == "uber 3500"


# ---------- dashboard: firma de Telegram ----------

def _init_data(token, usuario, auth_date):
    datos = {"auth_date": str(auth_date), "user": json.dumps(usuario)}
    texto = "\n".join(f"{k}={v}" for k, v in sorted(datos.items()))
    clave = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return urlencode({**datos, "hash": hmac.new(clave, texto.encode(), hashlib.sha256).hexdigest()})


def test_init_data_valido_vencido_y_falso():
    token, usuario = "123:ABC", {"id": 42, "first_name": "Ricardo"}
    assert validar_init_data(_init_data(token, usuario, int(time.time())), token) == usuario
    assert validar_init_data(_init_data(token, usuario, int(time.time()) - 3 * 86400), token) is None
    assert validar_init_data(_init_data("otro:TOKEN", usuario, int(time.time())), token) is None
    assert validar_init_data("basura", token) is None


# ---------- costos ----------

def test_precios_tienen_lectura_de_cache():
    assert all(len(p) == 3 for p in costos.PRECIOS.values())
    assert "claude-haiku-5-5" in costos._sql_precios()
