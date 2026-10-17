"""Perfil de pareja: gustos, comidas, sueños, horarios, amigos, deportes..."""

from core.db import conectar

CATEGORIAS = {
    "gustos": "❤️ Gustos", "comida": "🍣 Comidas", "suenos": "✨ Sueños", "trabajo": "💼 Trabajo y horarios",
    "amigos": "👯 Amigos", "deportes": "⚽ Deportes", "regalos": "🎁 Ideas de regalo", "musica": "🎵 Música",
    "otro": "📝 Otros",
}


def guardar(usuario_id: int | None, categoria: str, dato: str, creado_por: int) -> int:
    with conectar() as conn:
        return conn.execute(
            "INSERT INTO perfil (usuario_id, categoria, dato, creado_por) VALUES (%s, %s, %s, %s) RETURNING id",
            (usuario_id, categoria, dato.strip(), creado_por),
        ).fetchone()["id"]


def listar(usuario_id: int | None = None, solo_de_los_dos: bool = False, categoria: str | None = None) -> list[dict]:
    """Datos del perfil. Sin filtros: todo. `usuario_id` = de esa persona; `solo_de_los_dos` = los compartidos."""
    with conectar() as conn:
        return conn.execute(
            """
            SELECT p.id, p.usuario_id, p.categoria, p.dato, u.nombre
            FROM perfil p LEFT JOIN usuarios u ON u.id = p.usuario_id
            WHERE (%(uid)s::int IS NULL OR p.usuario_id = %(uid)s::int)
              AND (NOT %(dos)s OR p.usuario_id IS NULL)
              AND (%(cat)s::text IS NULL OR p.categoria = %(cat)s::text)
            ORDER BY p.usuario_id NULLS LAST, p.categoria, p.creado_en
            """,
            {"uid": usuario_id, "dos": solo_de_los_dos, "cat": categoria},
        ).fetchall()


def borrar(dato_id: int) -> bool:
    with conectar() as conn:
        return conn.execute("DELETE FROM perfil WHERE id = %s", (dato_id,)).rowcount > 0
