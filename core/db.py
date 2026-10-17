"""Acceso a PostgreSQL."""

import psycopg
from psycopg.rows import dict_row

from core import config


def conectar() -> psycopg.Connection:
    return psycopg.connect(config.database_url(), row_factory=dict_row)


def registrar_usuario(telegram_id: int, nombre: str) -> dict:
    """Crea el usuario si no existe y lo devuelve."""
    with conectar() as conn:
        return conn.execute(
            """
            INSERT INTO usuarios (nombre, telegram_id)
            VALUES (%s, %s)
            ON CONFLICT (telegram_id) DO UPDATE SET telegram_id = EXCLUDED.telegram_id
            RETURNING *
            """,
            (nombre, telegram_id),
        ).fetchone()
