"""Metas de ahorro. Lo ahorrado se calcula sumando aportes, nunca se guarda."""

from datetime import date
from decimal import Decimal

from core.db import conectar
from core.finanzas import hoy


def crear(nombre: str, emoji: str | None, monto_objetivo: Decimal, fecha_objetivo: date | None,
          creado_por: int) -> int:
    with conectar() as conn:
        return conn.execute(
            """
            INSERT INTO metas_ahorro (nombre, emoji, monto_objetivo, moneda, fecha_objetivo, creado_por)
            VALUES (%s, %s, %s, 'CLP', %s, %s) RETURNING id
            """,
            (nombre, emoji, monto_objetivo, fecha_objetivo, creado_por),
        ).fetchone()["id"]


def listar() -> list[dict]:
    """Metas activas con lo ahorrado y el porcentaje."""
    with conectar() as conn:
        return conn.execute(
            """
            SELECT m.id, m.nombre, COALESCE(m.emoji, '🎯') AS emoji, m.monto_objetivo, m.fecha_objetivo,
                   COALESCE(SUM(a.monto), 0) AS ahorrado,
                   LEAST(100, ROUND(COALESCE(SUM(a.monto), 0) * 100 / m.monto_objetivo)) AS porcentaje
            FROM metas_ahorro m
            LEFT JOIN aportes_meta a ON a.meta_id = m.id
            WHERE m.activa
            GROUP BY m.id
            ORDER BY m.creado_en
            """
        ).fetchall()


def obtener(meta_id: int) -> dict | None:
    return next((m for m in listar() if m["id"] == meta_id), None)


def aportar(meta_id: int, usuario_id: int, monto: Decimal) -> int:
    with conectar() as conn:
        return conn.execute(
            "INSERT INTO aportes_meta (meta_id, usuario_id, monto, fecha) VALUES (%s, %s, %s, %s) RETURNING id",
            (meta_id, usuario_id, monto, hoy()),
        ).fetchone()["id"]


def borrar_aporte(aporte_id: int, usuario_id: int) -> bool:
    with conectar() as conn:
        return conn.execute("DELETE FROM aportes_meta WHERE id = %s AND usuario_id = %s",
                            (aporte_id, usuario_id)).rowcount > 0


def archivar(meta_id: int) -> bool:
    with conectar() as conn:
        return conn.execute("UPDATE metas_ahorro SET activa = FALSE WHERE id = %s AND activa",
                            (meta_id,)).rowcount > 0


def borrar(meta_id: int, usuario_id: int) -> bool:
    """Borra una meta recién creada (deshacer). Solo quien la creó."""
    with conectar() as conn:
        return conn.execute("DELETE FROM metas_ahorro WHERE id = %s AND creado_por = %s",
                            (meta_id, usuario_id)).rowcount > 0
