"""Lógica de negocio: registrar movimientos, saldo y resumen. Todos los cálculos en SQL."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from core import cambio
from core.db import conectar

ZONA = ZoneInfo("America/Santiago")


def ahora() -> datetime:
    return datetime.now(ZONA)


def hoy() -> date:
    return ahora().date()


def formatear_clp(valor: Decimal | int | float) -> str:
    return "$" + f"{round(valor):,}".replace(",", ".")


def obtener_usuario(telegram_id: int) -> dict | None:
    with conectar() as conn:
        return conn.execute("SELECT * FROM usuarios WHERE telegram_id = %s", (telegram_id,)).fetchone()


def todos_los_usuarios() -> list[dict]:
    with conectar() as conn:
        return conn.execute("SELECT * FROM usuarios ORDER BY id").fetchall()


def otro_usuario(usuario_id: int) -> dict | None:
    with conectar() as conn:
        return conn.execute("SELECT * FROM usuarios WHERE id <> %s ORDER BY id LIMIT 1", (usuario_id,)).fetchone()


def nombres_categorias(tipo: str) -> list[str]:
    with conectar() as conn:
        return [r["nombre"] for r in conn.execute("SELECT nombre FROM categorias WHERE tipo = %s", (tipo,))]


def categoria_por_nombre(nombre: str, tipo: str) -> dict:
    with conectar() as conn:
        return conn.execute(
            "SELECT * FROM categorias WHERE nombre = %s AND tipo = %s", (nombre, tipo)
        ).fetchone()


def registrar_movimiento(
    *,
    tipo: str,
    monto: Decimal,
    moneda: str,
    categoria_id: int | None,
    pagado_por: int,
    descripcion: str,
    mensaje_original: str,
    repartos: dict[int, Decimal] | None = None,
    fecha: date | None = None,
    es_prueba: bool = False,
) -> int:
    """Guarda un movimiento; convierte a CLP con el tipo de cambio del día.

    `repartos` = {usuario_id: porcentaje} si es compartido.
    """
    tasa = cambio.tasa(moneda, hoy())
    monto_base = (monto * tasa).quantize(Decimal("0.01"))
    with conectar() as conn, conn.transaction():
        movimiento_id = conn.execute(
            """
            INSERT INTO movimientos (tipo, monto, moneda, monto_base, tipo_cambio_usado, categoria_id,
                                     pagado_por, fecha, descripcion, es_compartido, mensaje_original, es_prueba)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (tipo, monto, moneda, monto_base, tasa, categoria_id, pagado_por, fecha or hoy(), descripcion or None,
             bool(repartos), mensaje_original, es_prueba),
        ).fetchone()["id"]
        for usuario_id, porcentaje in (repartos or {}).items():
            conn.execute(
                "INSERT INTO repartos (movimiento_id, usuario_id, porcentaje) VALUES (%s, %s, %s)",
                (movimiento_id, usuario_id, porcentaje),
            )
    return movimiento_id


def borrar_movimiento(movimiento_id: int, usuario_id: int) -> bool:
    """Borra un movimiento solo si lo pagó `usuario_id`. Devuelve True si se borró."""
    with conectar() as conn:
        cur = conn.execute(
            "DELETE FROM movimientos WHERE id = %s AND pagado_por = %s", (movimiento_id, usuario_id)
        )
        return cur.rowcount > 0


_SELECT_MOVIMIENTO = """
    SELECT m.id, m.tipo, m.monto, m.moneda, m.monto_base, m.tipo_cambio_usado, m.fecha, m.descripcion,
           m.es_compartido, m.es_prueba, m.pagado_por, c.nombre AS categoria,
           (SELECT r.porcentaje FROM repartos r WHERE r.movimiento_id = m.id AND r.usuario_id = m.pagado_por)
               AS porcentaje_pagador
    FROM movimientos m LEFT JOIN categorias c ON c.id = m.categoria_id
"""


def obtener_movimiento(movimiento_id: int) -> dict | None:
    with conectar() as conn:
        return conn.execute(_SELECT_MOVIMIENTO + " WHERE m.id = %s", (movimiento_id,)).fetchone()


def ultimos_movimientos(usuario_id: int, busqueda: str = "", fecha: date | None = None, limite: int = 8) -> list[dict]:
    """Movimientos anotados por `usuario_id`, del más reciente al más antiguo, opcionalmente filtrados."""
    palabras = [f"%{p}%" for p in busqueda.lower().split() if len(p) >= 3] if busqueda else []
    with conectar() as conn:
        return conn.execute(
            _SELECT_MOVIMIENTO + """
            WHERE m.pagado_por = %(yo)s
              AND (%(fecha)s::date IS NULL OR m.fecha = %(fecha)s::date)
              AND (%(palabras)s::text[] IS NULL OR m.descripcion ILIKE ANY(%(palabras)s::text[])
                   OR c.nombre ILIKE ANY(%(palabras)s::text[]))
            ORDER BY m.creado_en DESC LIMIT %(limite)s
            """,
            {"yo": usuario_id, "fecha": fecha, "palabras": palabras or None, "limite": limite},
        ).fetchall()


def actualizar_movimiento(movimiento_id: int, usuario_id: int, *, monto: Decimal, categoria_id: int,
                          descripcion: str, fecha: date, repartos: dict[int, Decimal] | None) -> bool:
    """Reemplaza los datos de un movimiento propio. Mantiene el tipo de cambio con que se registró."""
    with conectar() as conn, conn.transaction():
        cur = conn.execute(
            """
            UPDATE movimientos
            SET monto = %(monto)s, monto_base = ROUND(%(monto)s * tipo_cambio_usado, 2), categoria_id = %(cat)s,
                descripcion = %(desc)s, fecha = %(fecha)s, es_compartido = %(comp)s
            WHERE id = %(id)s AND pagado_por = %(yo)s
            """,
            {"monto": monto, "cat": categoria_id, "desc": descripcion or None, "fecha": fecha,
             "comp": bool(repartos), "id": movimiento_id, "yo": usuario_id},
        )
        if cur.rowcount == 0:
            return False
        conn.execute("DELETE FROM repartos WHERE movimiento_id = %s", (movimiento_id,))
        for uid, porcentaje in (repartos or {}).items():
            conn.execute("INSERT INTO repartos (movimiento_id, usuario_id, porcentaje) VALUES (%s, %s, %s)",
                         (movimiento_id, uid, porcentaje))
        return True


def borrar_pruebas() -> int:
    """Borra todos los movimientos y pagos de prueba. Devuelve cuántos borró."""
    with conectar() as conn:
        return (conn.execute("DELETE FROM movimientos WHERE es_prueba").rowcount
                + conn.execute("DELETE FROM liquidaciones WHERE es_prueba").rowcount)


def registrar_liquidacion(de_usuario: int, a_usuario: int, monto: Decimal, fecha: date | None,
                          mensaje_original: str, es_prueba: bool = False) -> int:
    """Pago entre ellos para saldar deudas (solo CLP por ahora)."""
    with conectar() as conn:
        return conn.execute(
            """
            INSERT INTO liquidaciones (de_usuario, a_usuario, monto, moneda, monto_base, tipo_cambio_usado,
                                       fecha, mensaje_original, es_prueba)
            VALUES (%s, %s, %s, 'CLP', %s, 1, %s, %s, %s) RETURNING id
            """,
            (de_usuario, a_usuario, monto, monto, fecha or hoy(), mensaje_original, es_prueba),
        ).fetchone()["id"]


def borrar_liquidacion(liquidacion_id: int, usuario_id: int) -> bool:
    """Borra un pago si `usuario_id` participó en él."""
    with conectar() as conn:
        return conn.execute(
            "DELETE FROM liquidaciones WHERE id = %s AND %s IN (de_usuario, a_usuario)",
            (liquidacion_id, usuario_id),
        ).rowcount > 0


def balances(incluir_pruebas: bool = False) -> list[dict]:
    """Balance de cada usuario en CLP. Positivo = le deben; negativo = debe."""
    with conectar() as conn:
        return conn.execute(
            """
            WITH pagado AS (
                SELECT pagado_por AS usuario_id, SUM(monto_base) AS total
                FROM movimientos
                WHERE es_compartido AND tipo = 'gasto' AND (%(pruebas)s OR NOT es_prueba)
                GROUP BY pagado_por
            ), cuota AS (
                SELECT r.usuario_id, SUM(m.monto_base * r.porcentaje / 100) AS total
                FROM repartos r
                JOIN movimientos m ON m.id = r.movimiento_id
                WHERE m.es_compartido AND m.tipo = 'gasto' AND (%(pruebas)s OR NOT m.es_prueba)
                GROUP BY r.usuario_id
            ), liq_pagadas AS (
                SELECT de_usuario AS usuario_id, SUM(monto_base) AS total FROM liquidaciones
                WHERE (%(pruebas)s OR NOT es_prueba) GROUP BY de_usuario
            ), liq_recibidas AS (
                SELECT a_usuario AS usuario_id, SUM(monto_base) AS total FROM liquidaciones
                WHERE (%(pruebas)s OR NOT es_prueba) GROUP BY a_usuario
            )
            SELECT u.id, u.nombre,
                   COALESCE(p.total, 0) - COALESCE(c.total, 0)
                   + COALESCE(lp.total, 0) - COALESCE(lr.total, 0) AS balance
            FROM usuarios u
            LEFT JOIN pagado p ON p.usuario_id = u.id
            LEFT JOIN cuota c ON c.usuario_id = u.id
            LEFT JOIN liq_pagadas lp ON lp.usuario_id = u.id
            LEFT JOIN liq_recibidas lr ON lr.usuario_id = u.id
            ORDER BY u.id
            """,
            {"pruebas": incluir_pruebas},
        ).fetchall()


def resumen_mes(usuario_id: int, mes: date | None = None) -> dict:
    """Gastos del mes por categoría (de ambos) e ingresos del mes del usuario que consulta."""
    mes = (mes or hoy()).replace(day=1)
    with conectar() as conn:
        gastos = conn.execute(
            """
            SELECT COALESCE(c.emoji, '') AS emoji, COALESCE(c.nombre, 'Sin categoría') AS categoria,
                   SUM(m.monto_base) AS total
            FROM movimientos m
            LEFT JOIN categorias c ON c.id = m.categoria_id
            WHERE m.tipo = 'gasto' AND NOT m.es_prueba AND date_trunc('month', m.fecha) = %s
            GROUP BY 1, 2
            ORDER BY total DESC
            """,
            (mes,),
        ).fetchall()
        por_persona = conn.execute(
            """
            SELECT u.nombre, COALESCE(SUM(m.monto_base), 0) AS total
            FROM usuarios u
            LEFT JOIN movimientos m
                   ON m.pagado_por = u.id AND m.tipo = 'gasto' AND NOT m.es_prueba AND date_trunc('month', m.fecha) = %s
            GROUP BY u.id, u.nombre
            ORDER BY u.id
            """,
            (mes,),
        ).fetchall()
        ingresos = conn.execute(
            """
            SELECT COALESCE(SUM(monto_base), 0) AS total
            FROM movimientos
            WHERE tipo = 'ingreso' AND NOT es_prueba AND pagado_por = %s AND date_trunc('month', fecha) = %s
            """,
            (usuario_id, mes),
        ).fetchone()["total"]
    return {"mes": mes, "gastos": gastos, "por_persona": por_persona, "mis_ingresos": ingresos}
