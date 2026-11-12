"""Compromisos mensuales: tarjetas de crédito, compras en cuotas y fijos (sueldo, arriendo...).

Las fechas y los montos se calculan con funciones puras (probadas sin base); las cuotas se anotan como movimientos
normales el día que tocan, así saldos, resúmenes y dashboard las ven sin cambios.
"""

import calendar
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

from core import finanzas
from core.db import conectar

AVISO_PAGO_DIAS = 2   # días de anticipación del aviso de pago de la tarjeta


# ---------- fechas y montos (puros) ----------

def fecha_en_mes(anio: int, mes: int, dia: int) -> date:
    """El `dia` de ese mes; si el mes es más corto (31 en febrero), su último día."""
    return date(anio, mes, min(dia, calendar.monthrange(anio, mes)[1]))


def sumar_meses(base: date, meses: int, dia: int) -> date:
    anios, mes0 = divmod(base.month - 1 + meses, 12)
    return fecha_en_mes(base.year + anios, mes0 + 1, dia)


def primera_cuota(fecha_compra: date, dia_facturacion: int | None) -> tuple[date, int]:
    """(fecha de la 1ª cuota, día de cobro). Con tarjeta: la primera facturación DESPUÉS de la compra.
    Sin tarjeta: el mismo día del mes siguiente (como suele cobrar una tarjeta de crédito en Chile)."""
    if dia_facturacion:
        este_mes = fecha_en_mes(fecha_compra.year, fecha_compra.month, dia_facturacion)
        return (este_mes if este_mes > fecha_compra else sumar_meses(fecha_compra, 1, dia_facturacion)), dia_facturacion
    return sumar_meses(fecha_compra, 1, fecha_compra.day), fecha_compra.day


def fechas_cuotas(primera: date, dia_cobro: int, n: int) -> list[date]:
    return [sumar_meses(primera, i, dia_cobro) for i in range(n)]


def montos_cuotas(total: Decimal, n: int, moneda: str = "CLP") -> list[Decimal]:
    """Cuotas iguales; la última absorbe el redondeo (en CLP, pesos enteros)."""
    unidad = Decimal("1") if moneda == "CLP" else Decimal("0.01")
    base = (total / n).quantize(unidad, rounding=ROUND_DOWN)
    return [base] * (n - 1) + [total - base * (n - 1)]


def proyeccion(planes: list[dict], meses: int = 6) -> list[tuple[date, Decimal]]:
    """Cuánto suman las cuotas que faltan anotar, por mes (primer día del mes), para los próximos `meses` con cuotas."""
    por_mes: dict[date, Decimal] = {}
    for p in planes:
        fechas = fechas_cuotas(p["primera_fecha"], p["dia_cobro"], p["n_cuotas"])
        montos = montos_cuotas(p["monto_total"], p["n_cuotas"], p["moneda"])
        for f, m in list(zip(fechas, montos))[p["cuotas_registradas"]:]:
            clave = f.replace(day=1)
            por_mes[clave] = por_mes.get(clave, Decimal(0)) + m
    return sorted(por_mes.items())[:meses]


def fijo_toca(fijo: dict, hoy: date) -> date | None:
    """Fecha de este mes en que toca el fijo, si ya llegó y el fijo existía ese día (si no, None)."""
    objetivo = fecha_en_mes(hoy.year, hoy.month, fijo["dia_del_mes"])
    return objetivo if objetivo <= hoy and objetivo >= fijo["creado_dia"] else None


def proximo_fijo(fijo: dict, hoy: date) -> date:
    objetivo = fecha_en_mes(hoy.year, hoy.month, fijo["dia_del_mes"])
    return objetivo if objetivo >= hoy else sumar_meses(hoy, 1, fijo["dia_del_mes"])


def avisos_tarjeta(tarjeta: dict, hoy: date) -> list[tuple[str, date]]:
    """Avisos que tocan hoy: ("facturacion", hoy) el día que factura y ("pago", fecha) unos días antes de pagar."""
    avisos = []
    if hoy == fecha_en_mes(hoy.year, hoy.month, tarjeta["dia_facturacion"]):
        avisos.append(("facturacion", hoy))
    if tarjeta.get("dia_vencimiento"):
        objetivo = hoy + timedelta(days=AVISO_PAGO_DIAS)
        if objetivo == fecha_en_mes(objetivo.year, objetivo.month, tarjeta["dia_vencimiento"]):
            avisos.append(("pago", objetivo))
    return avisos


# ---------- tarjetas ----------

def guardar_tarjeta(usuario_id: int, nombre: str, dia_facturacion: int, dia_vencimiento: int | None) -> int:
    with conectar() as conn:
        return conn.execute(
            """INSERT INTO tarjetas (usuario_id, nombre, dia_facturacion, dia_vencimiento) VALUES (%s, %s, %s, %s)
               ON CONFLICT (usuario_id, lower(nombre)) DO UPDATE
                   SET dia_facturacion = EXCLUDED.dia_facturacion,
                       dia_vencimiento = COALESCE(EXCLUDED.dia_vencimiento, tarjetas.dia_vencimiento)
               RETURNING id""",
            (usuario_id, nombre, dia_facturacion, dia_vencimiento)).fetchone()["id"]


def tarjetas(usuario_id: int | None = None) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            """SELECT t.*, u.telegram_id, u.nombre AS usuario FROM tarjetas t JOIN usuarios u ON u.id = t.usuario_id
               WHERE %(u)s::int IS NULL OR t.usuario_id = %(u)s::int ORDER BY t.usuario_id, t.id""",
            {"u": usuario_id}).fetchall()


def tarjeta_para(usuario_id: int, nombre: str = "") -> dict | None:
    """La tarjeta que nombra (si calza con alguna suya) o la primera que registró."""
    propias = tarjetas(usuario_id)
    nombre = nombre.strip().lower()
    if nombre:
        for t in propias:
            if nombre in t["nombre"].lower() or t["nombre"].lower() in nombre:
                return t
    return propias[0] if propias else None


def borrar_tarjeta(tarjeta_id: int, usuario_id: int) -> bool:
    with conectar() as conn:
        return conn.execute("DELETE FROM tarjetas WHERE id = %s AND usuario_id = %s",
                            (tarjeta_id, usuario_id)).rowcount > 0


# ---------- cuotas ----------

def plan_para(mov: dict, usuario_id: int) -> dict:
    """Cómo quedaría una compra en cuotas: tarjeta, primera fecha, día de cobro y monto de cada cuota."""
    tarjeta = tarjeta_para(usuario_id, mov.get("tarjeta", ""))
    primera, dia = primera_cuota(mov["fecha"], tarjeta["dia_facturacion"] if tarjeta else None)
    return {"tarjeta": tarjeta, "primera_fecha": primera, "dia_cobro": dia,
            "montos": montos_cuotas(mov["monto"], mov["cuotas"], mov["moneda"])}


def crear_plan(mov: dict, usuario_id: int, mensaje_original: str) -> int:
    plan = plan_para(mov, usuario_id)
    categoria = finanzas.categoria_por_nombre(mov["categoria"], "gasto")
    with conectar() as conn:
        plan_id = conn.execute(
            """INSERT INTO planes_cuotas (usuario_id, tarjeta_id, descripcion, categoria_id, monto_total, moneda,
                   n_cuotas, fecha_compra, primera_fecha, dia_cobro, porcentaje_pagador, mensaje_original, es_prueba)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (usuario_id, plan["tarjeta"]["id"] if plan["tarjeta"] else None, mov["descripcion"] or None,
             categoria["id"] if categoria else None, mov["monto"], mov["moneda"], mov["cuotas"], mov["fecha"],
             plan["primera_fecha"], plan["dia_cobro"], mov["porcentaje_pagador"], mensaje_original,
             mov.get("es_prueba", False))).fetchone()["id"]
    anotar_cuotas_vencidas(finanzas.hoy(), plan_id)  # si es una compra antigua, las cuotas pasadas quedan al tiro
    return plan_id


def anotar_cuotas_vencidas(hoy: date, plan_id: int | None = None) -> list[dict]:
    """Anota como movimientos las cuotas que ya tocan (una sola vez: el plan queda bloqueado mientras tanto)."""
    anotadas = []
    with conectar() as conn, conn.transaction():
        planes = conn.execute(
            """SELECT p.*, c.nombre AS categoria, u.telegram_id, u.nombre AS usuario,
                      (SELECT o.id FROM usuarios o WHERE o.id <> p.usuario_id ORDER BY o.id LIMIT 1) AS pareja_id
               FROM planes_cuotas p JOIN usuarios u ON u.id = p.usuario_id
               LEFT JOIN categorias c ON c.id = p.categoria_id
               WHERE p.activo AND p.cuotas_registradas < p.n_cuotas AND p.primera_fecha <= %(hoy)s
                 AND (%(id)s::int IS NULL OR p.id = %(id)s::int)
               FOR UPDATE OF p SKIP LOCKED""", {"hoy": hoy, "id": plan_id}).fetchall()
        for p in planes:
            fechas = fechas_cuotas(p["primera_fecha"], p["dia_cobro"], p["n_cuotas"])
            montos = montos_cuotas(p["monto_total"], p["n_cuotas"], p["moneda"])
            repartos = None
            if p["porcentaje_pagador"] is not None and p["pareja_id"]:
                repartos = {p["usuario_id"]: p["porcentaje_pagador"], p["pareja_id"]: 100 - p["porcentaje_pagador"]}
            hechas = p["cuotas_registradas"]
            while hechas < p["n_cuotas"] and fechas[hechas] <= hoy:
                numero = hechas + 1
                descripcion = f"{p['descripcion'] or p['categoria'] or 'compra'} (cuota {numero}/{p['n_cuotas']})"
                finanzas.registrar_movimiento(
                    tipo="gasto", monto=montos[hechas], moneda=p["moneda"], categoria_id=p["categoria_id"],
                    pagado_por=p["usuario_id"], descripcion=descripcion,
                    mensaje_original=f"{p['mensaje_original'] or ''} | cuota {numero}/{p['n_cuotas']}",
                    repartos=repartos, fecha=fechas[hechas], es_prueba=p["es_prueba"], plan_cuotas_id=p["id"],
                    conn=conn)
                anotadas.append({"plan_id": p["id"], "usuario_id": p["usuario_id"], "telegram_id": p["telegram_id"],
                                 "usuario": p["usuario"], "descripcion": descripcion, "monto": montos[hechas],
                                 "moneda": p["moneda"], "porcentaje_pagador": p["porcentaje_pagador"],
                                 "es_prueba": p["es_prueba"], "tarjeta_id": p["tarjeta_id"]})
                hechas += 1
            conn.execute("UPDATE planes_cuotas SET cuotas_registradas = %s WHERE id = %s", (hechas, p["id"]))
    return anotadas


def planes_pendientes(usuario_id: int) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            """SELECT p.*, t.nombre AS tarjeta FROM planes_cuotas p LEFT JOIN tarjetas t ON t.id = p.tarjeta_id
               WHERE p.usuario_id = %s AND p.activo AND p.cuotas_registradas < p.n_cuotas AND NOT p.es_prueba
               ORDER BY p.primera_fecha, p.id""", (usuario_id,)).fetchall()


def cancelar_plan(plan_id: int, usuario_id: int) -> bool:
    """Deja de anotar las cuotas que faltan (ej: la prepagó). Las ya anotadas se conservan."""
    with conectar() as conn:
        return conn.execute("UPDATE planes_cuotas SET activo = FALSE WHERE id = %s AND usuario_id = %s AND activo",
                            (plan_id, usuario_id)).rowcount > 0


def borrar_plan(plan_id: int, usuario_id: int) -> bool:
    """Deshacer: borra el plan y las cuotas que ya se hubieran anotado."""
    with conectar() as conn:
        return conn.execute("DELETE FROM planes_cuotas WHERE id = %s AND usuario_id = %s",
                            (plan_id, usuario_id)).rowcount > 0


# ---------- fijos (tabla recurrentes) ----------

def crear_fijo(usuario_id: int, fijo: dict, mensaje_original: str) -> int:
    categoria = finanzas.categoria_por_nombre(fijo["categoria"], fijo["tipo"])
    with conectar() as conn:
        return conn.execute(
            """INSERT INTO recurrentes (descripcion, monto, moneda, categoria_id, frecuencia, dia_del_mes, tipo,
                                        usuario_id, porcentaje_pagador, mensaje_original)
               VALUES (%s, %s, %s, %s, 'mensual', %s, %s, %s, %s, %s) RETURNING id""",
            (fijo["descripcion"] or fijo["categoria"], fijo["monto"], fijo["moneda"],
             categoria["id"] if categoria else None, fijo["dia_del_mes"], fijo["tipo"], usuario_id,
             fijo["porcentaje_pagador"], mensaje_original)).fetchone()["id"]


def fijos(usuario_id: int | None = None) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            """SELECT r.*, c.nombre AS categoria, u.telegram_id, u.nombre AS usuario,
                      (r.creado_en AT TIME ZONE 'America/Santiago')::date AS creado_dia
               FROM recurrentes r JOIN usuarios u ON u.id = r.usuario_id
               LEFT JOIN categorias c ON c.id = r.categoria_id
               WHERE r.activo AND r.frecuencia = 'mensual' AND r.dia_del_mes IS NOT NULL
                 AND (%(u)s::int IS NULL OR r.usuario_id = %(u)s::int)
               ORDER BY r.usuario_id, r.dia_del_mes, r.id""", {"u": usuario_id}).fetchall()


def borrar_fijo(fijo_id: int, usuario_id: int) -> bool:
    with conectar() as conn:
        return conn.execute("DELETE FROM recurrentes WHERE id = %s AND usuario_id = %s",
                            (fijo_id, usuario_id)).rowcount > 0
