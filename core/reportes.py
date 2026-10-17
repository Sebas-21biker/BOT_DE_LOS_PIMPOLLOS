"""Datos agregados para el dashboard. Todos los cálculos en SQL.

Visibilidad: se muestran los gastos de ambos, pero solo los ingresos propios de quien consulta.
"""

from datetime import date

from core import finanzas
from core.db import conectar

# Fragmentos SQL comunes (parámetros con nombre).
_FILTRO_PRUEBAS = "(%(pruebas)s OR NOT m.es_prueba)"
_FILTRO_PERSONA = "(%(persona)s::int IS NULL OR m.pagado_por = %(persona)s::int)"


def _sumar_meses(mes: date, n: int) -> date:
    total = mes.year * 12 + mes.month - 1 + n
    return date(total // 12, total % 12 + 1, 1)


def datos_dashboard(usuario_id: int, mes: date, persona_id: int | None, incluir_pruebas: bool) -> dict:
    mes = mes.replace(day=1)
    p = {
        "yo": usuario_id,
        "mes": mes,
        "mes_anterior": _sumar_meses(mes, -1),
        "desde": _sumar_meses(mes, -11),
        "persona": persona_id,
        "pruebas": incluir_pruebas,
    }
    with conectar() as conn:
        usuarios = conn.execute("SELECT id, nombre FROM usuarios ORDER BY id").fetchall()

        meses = [r["mes"] for r in conn.execute(
            f"""
            SELECT DISTINCT date_trunc('month', m.fecha)::date AS mes
            FROM movimientos m WHERE {_FILTRO_PRUEBAS}
            UNION SELECT date_trunc('month', CURRENT_DATE)::date
            ORDER BY mes DESC
            """, p)]

        totales = conn.execute(
            f"""
            SELECT
              COALESCE(SUM(m.monto_base) FILTER (WHERE m.tipo = 'gasto' AND {_FILTRO_PERSONA}
                                                  AND date_trunc('month', m.fecha) = %(mes)s), 0) AS gastos,
              COALESCE(SUM(m.monto_base) FILTER (WHERE m.tipo = 'gasto' AND {_FILTRO_PERSONA}
                                                  AND date_trunc('month', m.fecha) = %(mes_anterior)s), 0)
                AS gastos_mes_anterior,
              COALESCE(SUM(m.monto_base) FILTER (WHERE m.tipo = 'ingreso' AND m.pagado_por = %(yo)s
                                                  AND date_trunc('month', m.fecha) = %(mes)s), 0) AS mis_ingresos
            FROM movimientos m
            WHERE {_FILTRO_PRUEBAS}
            """, p).fetchone()

        categorias = conn.execute(
            f"""
            SELECT COALESCE(c.emoji, '') AS emoji, COALESCE(c.nombre, 'Sin categoría') AS categoria,
                   SUM(m.monto_base) AS total
            FROM movimientos m
            LEFT JOIN categorias c ON c.id = m.categoria_id
            WHERE m.tipo = 'gasto' AND date_trunc('month', m.fecha) = %(mes)s
              AND {_FILTRO_PERSONA} AND {_FILTRO_PRUEBAS}
            GROUP BY 1, 2
            ORDER BY total DESC
            """, p).fetchall()

        serie = conn.execute(
            f"""
            SELECT s.mes::date AS mes,
                   COALESCE(SUM(m.monto_base) FILTER (WHERE m.tipo = 'gasto' AND {_FILTRO_PERSONA}), 0) AS gastos,
                   COALESCE(SUM(m.monto_base) FILTER (WHERE m.tipo = 'ingreso' AND m.pagado_por = %(yo)s), 0)
                     AS mis_ingresos
            FROM generate_series(%(desde)s::date, %(mes)s::date, interval '1 month') AS s(mes)
            LEFT JOIN movimientos m
                   ON date_trunc('month', m.fecha) = s.mes AND {_FILTRO_PRUEBAS}
            GROUP BY s.mes
            ORDER BY s.mes
            """, p).fetchall()

        por_persona = conn.execute(
            f"""
            SELECT u.id, u.nombre, COALESCE(SUM(m.monto_base), 0) AS total
            FROM usuarios u
            LEFT JOIN movimientos m
                   ON m.pagado_por = u.id AND m.tipo = 'gasto'
                  AND date_trunc('month', m.fecha) = %(mes)s AND {_FILTRO_PRUEBAS}
            GROUP BY u.id, u.nombre
            ORDER BY u.id
            """, p).fetchall()

        movimientos = conn.execute(
            f"""
            SELECT m.id, m.fecha, m.tipo, m.monto_base AS monto, m.descripcion, m.es_compartido, m.es_prueba,
                   COALESCE(c.emoji, '') AS emoji, COALESCE(c.nombre, 'Sin categoría') AS categoria,
                   u.nombre AS persona
            FROM movimientos m
            JOIN usuarios u ON u.id = m.pagado_por
            LEFT JOIN categorias c ON c.id = m.categoria_id
            WHERE date_trunc('month', m.fecha) = %(mes)s
              AND (m.tipo = 'gasto' OR m.pagado_por = %(yo)s)
              AND {_FILTRO_PERSONA} AND {_FILTRO_PRUEBAS}
            ORDER BY m.fecha DESC, m.id DESC
            LIMIT 100
            """, p).fetchall()

    saldo = None
    filas = finanzas.balances(incluir_pruebas)
    if len(filas) >= 2:
        acreedor = max(filas, key=lambda f: f["balance"])
        deudor = min(filas, key=lambda f: f["balance"])
        saldo = {"monto": round(acreedor["balance"]),
                 "acreedor": acreedor["nombre"], "deudor": deudor["nombre"]}

    return {
        "usuarios": usuarios,
        "meses": meses,
        "mes": mes,
        "totales": totales,
        "categorias": categorias,
        "serie": serie,
        "por_persona": por_persona,
        "movimientos": movimientos,
        "saldo": saldo,
    }
