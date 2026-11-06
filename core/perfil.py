"""Memoria de Pimpollo (tabla `recuerdos`, antes `perfil`): gustos, comidas, sueños, horarios, amigos...

Cada recuerdo guarda de quién es (usuario_id; NULL = de los dos), quién lo contó (creado_por), categoría,
confianza, origen (dicho / pregunta / grupo / inferido), visibilidad (todos / autor) y vigencia opcional.
Nunca se guarda nada que nadie haya dicho: lo deducido se le pregunta a la persona antes.
"""

import re
from datetime import date

from core.db import conectar

CATEGORIAS = {
    "gustos": "❤️ Gustos", "comida": "🍣 Comidas", "suenos": "✨ Sueños", "trabajo": "💼 Trabajo y horarios",
    "amigos": "👯 Amigos", "deportes": "⚽ Deportes", "regalos": "🎁 Ideas de regalo", "musica": "🎵 Música",
    "otro": "📝 Otros",
}
ORIGENES = ("dicho", "pregunta", "grupo", "inferido")

_COLUMNAS = """r.id, r.usuario_id, r.categoria, r.dato, r.creado_por, r.confianza, r.origen, r.visibilidad,
               r.vigente_hasta, r.creado_en, u.nombre"""
_VIGENTE = "(r.vigente_hasta IS NULL OR r.vigente_hasta >= (NOW() AT TIME ZONE 'America/Santiago')::date)"


def visibilidad_por_defecto(usuario_id: int | None, categoria: str, creado_por: int | None) -> str:
    """Una idea de regalo que alguien anota sobre la OTRA persona es sorpresa: solo la ve quien la anotó."""
    sorpresa = categoria == "regalos" and usuario_id is not None and creado_por is not None and usuario_id != creado_por
    return "autor" if sorpresa else "todos"


def guardar(usuario_id: int | None, categoria: str, dato: str, creado_por: int, origen: str = "dicho",
            confianza: float = 1.0, privado: bool = False, vigente_hasta: date | None = None) -> int:
    visibilidad = "autor" if privado else visibilidad_por_defecto(usuario_id, categoria, creado_por)
    with conectar() as conn:
        return conn.execute(
            """INSERT INTO recuerdos (usuario_id, categoria, dato, creado_por, origen, confianza, visibilidad,
                                      vigente_hasta)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (usuario_id, categoria, dato.strip(), creado_por, origen, confianza, visibilidad, vigente_hasta),
        ).fetchone()["id"]


def listar(usuario_id: int | None = None, solo_de_los_dos: bool = False, categoria: str | None = None) -> list[dict]:
    """Recuerdos vigentes. Sin filtros: todo. `usuario_id` = de esa persona; `solo_de_los_dos` = los compartidos."""
    with conectar() as conn:
        return conn.execute(
            f"""
            SELECT {_COLUMNAS}
            FROM recuerdos r LEFT JOIN usuarios u ON u.id = r.usuario_id
            WHERE {_VIGENTE}
              AND (%(uid)s::int IS NULL OR r.usuario_id = %(uid)s::int)
              AND (NOT %(dos)s OR r.usuario_id IS NULL)
              AND (%(cat)s::text IS NULL OR r.categoria = %(cat)s::text)
            ORDER BY r.usuario_id NULLS LAST, r.categoria, r.creado_en
            """,
            {"uid": usuario_id, "dos": solo_de_los_dos, "cat": categoria},
        ).fetchall()


def consulta_texto(texto: str) -> str:
    """Palabras de búsqueda unidas con "or" para websearch_to_tsquery (calza cualquiera)."""
    palabras = [p for p in re.findall(r"\w+", texto.lower()) if len(p) > 2]
    return " or ".join(palabras[:12])


def buscar(texto: str, limite: int = 20) -> list[dict]:
    """Recuerdos vigentes que tienen que ver con `texto` (full-text de Postgres), los más relevantes primero."""
    consulta = consulta_texto(texto)
    if not consulta:
        return []
    with conectar() as conn:
        return conn.execute(
            f"""
            SELECT {_COLUMNAS}, ts_rank(r.busqueda, q) AS rango
            FROM recuerdos r LEFT JOIN usuarios u ON u.id = r.usuario_id,
                 websearch_to_tsquery('spanish', %s) q
            WHERE {_VIGENTE} AND r.busqueda @@ q
            ORDER BY rango DESC, r.creado_en DESC
            LIMIT %s
            """, (consulta, limite)).fetchall()


def ya_existe(usuario_id: int | None, dato: str) -> bool:
    """¿Hay un recuerdo de esa persona que contiene todas las palabras importantes de `dato`?"""
    with conectar() as conn:
        return conn.execute(
            """SELECT 1 FROM recuerdos
               WHERE usuario_id IS NOT DISTINCT FROM %s AND busqueda @@ plainto_tsquery('spanish', %s) LIMIT 1""",
            (usuario_id, dato)).fetchone() is not None


def borrar(dato_id: int) -> bool:
    with conectar() as conn:
        return conn.execute("DELETE FROM recuerdos WHERE id = %s", (dato_id,)).rowcount > 0


def es_privado(fila: dict) -> bool:
    """Solo lo ve quien lo anotó (sorpresas, "no le digas")."""
    if "visibilidad" in fila:
        return fila["visibilidad"] == "autor"
    return es_sorpresa(fila)


def es_sorpresa(fila: dict) -> bool:
    """Idea de regalo que una persona anotó sobre la OTRA: es secreta para la festejada."""
    return (fila["categoria"] == "regalos" and fila["usuario_id"] is not None
            and fila.get("creado_por") is not None and fila["usuario_id"] != fila["creado_por"])


def visibles_para(filas: list[dict], usuario_id: int, en_grupo: bool = False) -> list[dict]:
    """Quita lo privado de otros. En un grupo los dos ven el chat: se quita todo lo privado."""
    return [f for f in filas if not (es_privado(f) and (en_grupo or f.get("creado_por") != usuario_id))]


def relevantes(filas: list[dict], encontrados: list[dict], maximo: int = 40) -> list[dict]:
    """Si la memoria es grande, se manda a la IA lo que calza con el pedido (`encontrados`) + lo más reciente."""
    if len(filas) <= maximo:
        return filas
    visibles = {f["id"] for f in filas}
    elegidos = [f for f in encontrados if f["id"] in visibles][:maximo // 2]
    ids = {f["id"] for f in elegidos}
    recientes = sorted((f for f in filas if f["id"] not in ids), key=lambda f: f["creado_en"], reverse=True)
    return elegidos + recientes[:maximo - len(elegidos)]
