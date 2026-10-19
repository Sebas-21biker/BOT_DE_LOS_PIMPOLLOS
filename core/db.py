"""Acceso a PostgreSQL."""

import secrets

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


MAX_USUARIOS = 2  # es un bot de pareja


def usuario_por_token(token: str) -> dict | None:
    """Usuario dueño del token de pagos automáticos."""
    if not token:
        return None
    with conectar() as conn:
        return conn.execute("SELECT * FROM usuarios WHERE token_auto = %s", (token,)).fetchone()


def token_automatico(usuario_id: int, nuevo: bool = False) -> str:
    """Devuelve el token de pagos automáticos del usuario (lo crea si no tiene o si se pide uno nuevo)."""
    with conectar() as conn:
        actual = conn.execute("SELECT token_auto FROM usuarios WHERE id = %s", (usuario_id,)).fetchone()["token_auto"]
        if actual and not nuevo:
            return actual
        token = secrets.token_urlsafe(18)
        conn.execute("UPDATE usuarios SET token_auto = %s WHERE id = %s", (token, usuario_id))
        return token


def telegram_ids_registrados() -> set[int]:
    with conectar() as conn:
        return {r["telegram_id"] for r in conn.execute("SELECT telegram_id FROM usuarios")}


def hay_cupo() -> bool:
    with conectar() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM usuarios").fetchone()["n"] < MAX_USUARIOS


def crear_invitacion(creado_por: int, dias_validez: int = 7) -> str:
    codigo = secrets.token_urlsafe(9)
    with conectar() as conn:
        conn.execute(
            "INSERT INTO invitaciones (codigo, creado_por, expira_en) VALUES (%s, %s, NOW() + %s * interval '1 day')",
            (codigo, creado_por, dias_validez),
        )
    return codigo


def usar_invitacion(codigo: str, telegram_id: int, nombre: str) -> tuple[dict, dict] | None:
    """Registra al usuario si la invitación es válida. Devuelve (nuevo_usuario, quien_invito) o None."""
    with conectar() as conn, conn.transaction():
        invitacion = conn.execute(
            """
            SELECT i.codigo, u.* FROM invitaciones i JOIN usuarios u ON u.id = i.creado_por
            WHERE i.codigo = %s AND i.usado_por IS NULL AND i.expira_en > NOW()
            FOR UPDATE OF i
            """,
            (codigo,),
        ).fetchone()
        if invitacion is None:
            return None
        if conn.execute("SELECT COUNT(*) AS n FROM usuarios").fetchone()["n"] >= MAX_USUARIOS:
            return None
        nuevo = conn.execute(
            "INSERT INTO usuarios (nombre, telegram_id) VALUES (%s, %s) RETURNING *", (nombre, telegram_id)
        ).fetchone()
        conn.execute("UPDATE invitaciones SET usado_por = %s, usado_en = NOW() WHERE codigo = %s",
                     (nuevo["id"], codigo))
        return nuevo, invitacion
