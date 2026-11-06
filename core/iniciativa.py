"""Datos de la iniciativa de Pimpollo: charla reciente del grupo, preferencias e intervenciones."""

from datetime import datetime

from core.db import conectar

HORAS_MEMORIA = 48  # la charla del grupo se borra a las 48 h


def guardar_mensaje(chat_id: int, usuario_id: int, texto: str, al_bot: bool) -> None:
    with conectar() as conn:
        conn.execute("INSERT INTO mensajes_grupo (chat_id, usuario_id, texto, al_bot) VALUES (%s, %s, %s, %s)",
                     (chat_id, usuario_id, texto[:2000], al_bot))


def borrar_viejos() -> None:
    with conectar() as conn:
        conn.execute("DELETE FROM mensajes_grupo WHERE creado_en < NOW() - make_interval(hours => %s)",
                     (HORAS_MEMORIA,))


def chats_en_pausa(pausa_min: int) -> list[int]:
    """Grupos con mensajes nuevos sin evaluar y sin actividad en los últimos `pausa_min` minutos."""
    with conectar() as conn:
        return [r["chat_id"] for r in conn.execute(
            """
            SELECT m.chat_id
            FROM mensajes_grupo m LEFT JOIN iniciativa i ON i.chat_id = m.chat_id
            GROUP BY m.chat_id, i.evaluado_hasta
            HAVING MAX(m.id) > COALESCE(i.evaluado_hasta, 0)
               AND MAX(m.creado_en) < NOW() - make_interval(mins => %s)
            """, (pausa_min,))]


def tomar_tramo(chat_id: int, horas: int = 12, maximo: int = 40) -> list[dict]:
    """Mensajes aún no evaluados del chat (los últimos `maximo`, de las últimas `horas`) y los marca evaluados."""
    with conectar() as conn:
        desde = conn.execute("SELECT evaluado_hasta FROM iniciativa WHERE chat_id = %s", (chat_id,)).fetchone()
        filas = conn.execute(
            """
            SELECT * FROM (
                SELECT m.id, m.usuario_id, m.texto, m.al_bot, m.creado_en, u.nombre
                FROM mensajes_grupo m LEFT JOIN usuarios u ON u.id = m.usuario_id
                WHERE m.chat_id = %s AND m.id > %s AND m.creado_en > NOW() - make_interval(hours => %s)
                ORDER BY m.id DESC LIMIT %s
            ) t ORDER BY id
            """, (chat_id, desde["evaluado_hasta"] if desde else 0, horas, maximo)).fetchall()
        tope = conn.execute("SELECT COALESCE(MAX(id), 0) AS id FROM mensajes_grupo WHERE chat_id = %s",
                            (chat_id,)).fetchone()["id"]
        conn.execute(
            """INSERT INTO iniciativa (chat_id, evaluado_hasta) VALUES (%s, %s)
               ON CONFLICT (chat_id) DO UPDATE SET evaluado_hasta = EXCLUDED.evaluado_hasta""", (chat_id, tope))
        return filas


def preferencias(chat_id: int) -> dict:
    with conectar() as conn:
        fila = conn.execute("SELECT * FROM iniciativa WHERE chat_id = %s", (chat_id,)).fetchone()
    return fila or {"chat_id": chat_id, "nivel": "baja", "ajuste": 0, "silencio_hasta": None, "evaluado_hasta": 0}


def cambiar(chat_id: int, nivel: str | None = None, delta_ajuste: int = 0,
            silencio_hasta: datetime | None | bool = False) -> None:
    """Cambia el nivel, mueve el ajuste (acotado entre -2 y 3) o fija/quita el silencio (False = no tocar)."""
    with conectar() as conn:
        conn.execute("INSERT INTO iniciativa (chat_id) VALUES (%s) ON CONFLICT DO NOTHING", (chat_id,))
        if nivel:
            conn.execute("UPDATE iniciativa SET nivel = %s, ajuste = 0 WHERE chat_id = %s", (nivel, chat_id))
        if delta_ajuste:
            conn.execute("UPDATE iniciativa SET ajuste = LEAST(3, GREATEST(-2, ajuste + %s)) WHERE chat_id = %s",
                         (delta_ajuste, chat_id))
        if silencio_hasta is not False:
            conn.execute("UPDATE iniciativa SET silencio_hasta = %s WHERE chat_id = %s", (silencio_hasta, chat_id))


def intervenciones_recientes(chat_id: int, tipo: str, dias: int = 30) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            """SELECT id, tema, texto, reaccion, creado_en FROM intervenciones
               WHERE chat_id = %s AND tipo = %s AND creado_en > NOW() - make_interval(days => %s)
               ORDER BY creado_en DESC""", (chat_id, tipo, dias)).fetchall()


def registrar_intervencion(chat_id: int, tipo: str, tema: str, utilidad: int, texto: str) -> int:
    with conectar() as conn:
        return conn.execute(
            """INSERT INTO intervenciones (chat_id, tipo, tema, utilidad, texto)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""", (chat_id, tipo, tema, utilidad, texto)).fetchone()["id"]


def reaccionar(intervencion_id: int, reaccion: str) -> dict | None:
    """Guarda la primera reacción (👍/🤫) a una intervención. Devuelve la intervención, o None si ya tenía."""
    with conectar() as conn:
        return conn.execute(
            """UPDATE intervenciones SET reaccion = %s WHERE id = %s AND reaccion IS NULL
               RETURNING id, chat_id, tipo""", (reaccion, intervencion_id)).fetchone()
