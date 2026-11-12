"""Tarjetas, cuotas y fijos: fechas y montos (puros) y lo que valida el intérprete. Sin base ni API."""

import json
from datetime import date
from decimal import Decimal

from bot import compromisos as comp_bot
from bot import interprete
from core import compromisos as c

D = Decimal


# ---------- fechas ----------

def test_fecha_en_mes_ajusta_meses_cortos():
    assert c.fecha_en_mes(2027, 2, 31) == date(2027, 2, 28)
    assert c.fecha_en_mes(2028, 2, 30) == date(2028, 2, 29)
    assert c.sumar_meses(date(2026, 11, 30), 3, 30) == date(2027, 2, 28)
    assert c.sumar_meses(date(2027, 2, 28), 1, 30) == date(2027, 3, 30)   # vuelve al día de cobro


def test_primera_cuota_con_tarjeta_cae_en_la_proxima_facturacion():
    # factura el 25: compra el 12 → entra en la facturación del 25 de ese mes
    assert c.primera_cuota(date(2026, 11, 12), 25) == (date(2026, 11, 25), 25)
    # compra después del cierre (o el mismo día) → la del mes siguiente
    assert c.primera_cuota(date(2026, 11, 25), 25) == (date(2026, 12, 25), 25)
    assert c.primera_cuota(date(2026, 12, 28), 25) == (date(2027, 1, 25), 25)


def test_primera_cuota_sin_tarjeta_es_el_mes_siguiente():
    assert c.primera_cuota(date(2026, 11, 12), None) == (date(2026, 12, 12), 12)
    assert c.primera_cuota(date(2027, 1, 31), None) == (date(2027, 2, 28), 31)


def test_fechas_de_las_cuotas():
    assert c.fechas_cuotas(date(2026, 11, 30), 30, 4) == [date(2026, 11, 30), date(2026, 12, 30),
                                                         date(2027, 1, 30), date(2027, 2, 28)]


# ---------- montos ----------

def test_montos_cuotas_suman_el_total():
    assert c.montos_cuotas(D(600000), 6) == [D(100000)] * 6
    m = c.montos_cuotas(D(100000), 3)
    assert m == [D(33333), D(33333), D(33334)] and sum(m) == D(100000)
    u = c.montos_cuotas(D("100.00"), 3, "USD")
    assert u == [D("33.33"), D("33.33"), D("33.34")]


def test_proyeccion_solo_lo_que_falta():
    planes = [{"primera_fecha": date(2026, 11, 25), "dia_cobro": 25, "n_cuotas": 3, "monto_total": D(300000),
               "moneda": "CLP", "cuotas_registradas": 1},
              {"primera_fecha": date(2026, 12, 10), "dia_cobro": 10, "n_cuotas": 2, "monto_total": D(50000),
               "moneda": "CLP", "cuotas_registradas": 0}]
    assert c.proyeccion(planes) == [(date(2026, 12, 1), D(125000)), (date(2027, 1, 1), D(125000))]


# ---------- fijos y tarjetas ----------

def test_fijo_toca_desde_su_dia_y_solo_si_ya_existia():
    fijo = {"dia_del_mes": 30, "creado_dia": date(2026, 11, 12)}
    assert c.fijo_toca(fijo, date(2026, 11, 29)) is None
    assert c.fijo_toca(fijo, date(2026, 11, 30)) == date(2026, 11, 30)
    assert c.fijo_toca(fijo, date(2027, 2, 28)) == date(2027, 2, 28)   # febrero: último día
    viejo = {"dia_del_mes": 5, "creado_dia": date(2026, 11, 12)}          # creado después del 5: parte en diciembre
    assert c.fijo_toca(viejo, date(2026, 11, 20)) is None
    assert c.fijo_toca(viejo, date(2026, 12, 7)) == date(2026, 12, 5)    # si el bot estuvo caído, igual pregunta
    assert c.proximo_fijo(viejo, date(2026, 11, 20)) == date(2026, 12, 5)


def test_avisos_tarjeta():
    t = {"dia_facturacion": 25, "dia_vencimiento": 5}
    assert c.avisos_tarjeta(t, date(2026, 11, 25)) == [("facturacion", date(2026, 11, 25))]
    assert c.avisos_tarjeta(t, date(2026, 12, 3)) == [("pago", date(2026, 12, 5))]
    assert c.avisos_tarjeta(t, date(2026, 12, 4)) == []
    assert c.avisos_tarjeta({"dia_facturacion": 31, "dia_vencimiento": None}, date(2027, 2, 28)) == [
        ("facturacion", date(2027, 2, 28))]


# ---------- intérprete ----------

def _mov(**k):
    return {"tipo": "gasto", "monto": 600000, "moneda": "CLP", "categoria": "Hogar", "descripcion": " tele ",
            "fecha": "2026-11-12", "compartido": False, "porcentaje_pagador": 100, "cuotas": 1, "tarjeta": "", **k}


def test_validar_cuotas():
    hoy = date(2026, 11, 12)
    assert interprete._validar_movimiento(_mov(cuotas=6, tarjeta=" CMR "), ["Hogar"], [], hoy)[
        "cuotas"] == 6
    assert interprete._validar_movimiento(_mov(cuotas=0), ["Hogar"], [], hoy)["cuotas"] == 1
    assert interprete._validar_movimiento(_mov(cuotas=99), ["Hogar"], [], hoy)["cuotas"] == 1
    ingreso = interprete._validar_movimiento(_mov(tipo="ingreso", cuotas=6), ["Hogar"], ["Sueldo"], hoy)
    assert ingreso["cuotas"] == 1


def test_validar_tarjetas_y_fijos():
    def base():
        return {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                for k, v in interprete._VACIO.items()}
    datos = {**base(), "intencion": "tarjeta_guardar", "crudo": "{}",
             "tarjetas": [{"nombre": "", "dia_facturacion": 25, "dia_vencimiento": 0},
                          {"nombre": "CMR", "dia_facturacion": 40, "dia_vencimiento": 5}]}
    v = interprete._validar(datos, interprete.datetime(2026, 11, 12, 10), ["Otros"], ["Sueldo"], [], [])
    assert v["tarjetas"] == [{"nombre": "Tarjeta de crédito", "dia_facturacion": 25, "dia_vencimiento": None}]

    fijo = {"tipo": "ingreso", "monto": 1200000, "moneda": "CLP", "categoria": "Sueldo", "descripcion": "sueldo",
            "dia_del_mes": 30, "compartido": False, "porcentaje_pagador": 100}
    datos = {**base(), "intencion": "fijo_nuevo", "crudo": "{}", "fijos": [fijo, {**fijo, "dia_del_mes": 0}]}
    v = interprete._validar(datos, interprete.datetime(2026, 11, 12, 10), ["Otros"], ["Sueldo"], [], [])
    assert v["fijos"] == [{"tipo": "ingreso", "monto": D("1200000.00"), "moneda": "CLP", "categoria": "Sueldo",
                           "descripcion": "sueldo", "porcentaje_pagador": None, "dia_del_mes": 30}]
    sin_dia = {**base(), "intencion": "fijo_nuevo", "crudo": "{}", "fijos": [{**fijo, "dia_del_mes": 0}]}
    assert interprete._validar(sin_dia, interprete.datetime(2026, 11, 12, 10), ["Otros"], ["Sueldo"], [],
                               [])["intencion"] == "otro"


def test_el_fijo_se_propone_como_una_interpretacion_corregible():
    fijo = {"tipo": "gasto", "monto": D("450000.00"), "moneda": "CLP", "categoria": "Hogar",
            "descripcion": "arriendo", "porcentaje_pagador": D(50)}
    datos = comp_bot._datos_fijo(fijo, date(2026, 12, 5))
    mov = datos["movimientos"][0]
    assert mov["fecha"] == date(2026, 12, 5) and mov["porcentaje_pagador"] == D(50) and mov["cuotas"] == 1
    crudo = json.loads(datos["crudo"])["movimientos"][0]   # lo que recibe la IA si tocan Corregir
    assert crudo["monto"] == 450000.0 and crudo["compartido"] is True and crudo["fecha"] == "2026-12-05"
