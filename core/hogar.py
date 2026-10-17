"""Lista de compras, recordatorios, panoramas y notas de la casa."""

from datetime import datetime

from core.db import conectar

# ---------- lista de compras ----------


def agregar_items(items: list[tuple[str, str | None]], usuario_id: int) -> list[int]:
    with conectar() as conn:
        return [conn.execute(
            "INSERT INTO items_compra (nombre, cantidad, agregado_por) VALUES (%s, %s, %s) RETURNING id",
            (nombre, cantidad or None, usuario_id),
        ).fetchone()["id"] for nombre, cantidad in items]


def lista_compras() -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            "SELECT id, nombre, cantidad, comprado FROM items_compra ORDER BY creado_en, id"
        ).fetchall()


def alternar_item(item_id: int) -> None:
    with conectar() as conn:
        conn.execute("UPDATE items_compra SET comprado = NOT comprado WHERE id = %s", (item_id,))


def quitar_comprados() -> list[str]:
    """Elimina los ítems marcados como comprados y devuelve sus nombres."""
    with conectar() as conn:
        return [r["nombre"] for r in conn.execute("DELETE FROM items_compra WHERE comprado RETURNING nombre")]


def borrar_items(ids: list[int]) -> int:
    with conectar() as conn:
        return conn.execute("DELETE FROM items_compra WHERE id = ANY(%s)", (ids,)).rowcount


# ---------- recordatorios ----------

_INTERVALOS = {"diario": "1 day", "semanal": "7 days", "mensual": "1 month", "anual": "1 year"}


def crear_recordatorio(texto: str, fecha_hora: datetime, repetir: str, para_usuario_id: int | None,
                       creado_por: int) -> int:
    with conectar() as conn:
        return conn.execute(
            """
            INSERT INTO recordatorios (texto, fecha_hora, repetir, para_usuario_id, creado_por)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (texto, fecha_hora, repetir, para_usuario_id, creado_por),
        ).fetchone()["id"]


def proximos_recordatorios(usuario_id: int, limite: int = 15) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            """
            SELECT id, texto, fecha_hora, repetir, para_usuario_id
            FROM recordatorios
            WHERE NOT enviado AND (para_usuario_id IS NULL OR para_usuario_id = %s)
            ORDER BY fecha_hora
            LIMIT %s
            """,
            (usuario_id, limite),
        ).fetchall()


def borrar_recordatorio(recordatorio_id: int) -> bool:
    with conectar() as conn:
        return conn.execute("DELETE FROM recordatorios WHERE id = %s", (recordatorio_id,)).rowcount > 0


def tomar_recordatorios_vencidos() -> list[dict]:
    """Devuelve los recordatorios que ya tocan y los deja reprogramados (si repiten) o marcados como enviados.

    Devuelve los destinatarios como lista de telegram_id.
    """
    with conectar() as conn, conn.transaction():
        vencidos = conn.execute(
            """
            SELECT r.id, r.texto, r.repetir,
                   ARRAY(SELECT u.telegram_id FROM usuarios u
                         WHERE r.para_usuario_id IS NULL OR u.id = r.para_usuario_id) AS destinos
            FROM recordatorios r
            WHERE NOT r.enviado AND r.fecha_hora <= NOW()
            FOR UPDATE SKIP LOCKED
            """
        ).fetchall()
        for r in vencidos:
            if r["repetir"] == "nunca":
                conn.execute("UPDATE recordatorios SET enviado = TRUE WHERE id = %s", (r["id"],))
            else:
                # Suma el intervalo hasta dejarlo en el futuro (por si el bot estuvo caído varios periodos).
                conn.execute(
                    """
                    WITH RECURSIVE s(f) AS (
                        SELECT fecha_hora + %(i)s::interval FROM recordatorios WHERE id = %(id)s
                        UNION ALL SELECT f + %(i)s::interval FROM s WHERE f <= NOW()
                    )
                    UPDATE recordatorios SET fecha_hora = (SELECT MAX(f) FROM s) WHERE id = %(id)s
                    """,
                    {"i": _INTERVALOS[r["repetir"]], "id": r["id"]},
                )
        return vencidos


# ---------- panoramas ----------


def agregar_panoramas(panoramas: list[tuple[str, str, str | None]], usuario_id: int) -> list[int]:
    with conectar() as conn:
        return [conn.execute(
            "INSERT INTO panoramas (titulo, tipo, notas, agregado_por) VALUES (%s, %s, %s, %s) RETURNING id",
            (titulo, tipo, notas or None, usuario_id),
        ).fetchone()["id"] for titulo, tipo, notas in panoramas]


def panoramas_pendientes(tipo: str | None = None) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            """
            SELECT id, titulo, tipo, notas FROM panoramas
            WHERE NOT hecho AND (%s::text IS NULL OR tipo = %s::text)
            ORDER BY creado_en
            """,
            (tipo, tipo),
        ).fetchall()


def sortear_panorama(tipo: str | None = None) -> dict | None:
    with conectar() as conn:
        return conn.execute(
            """
            SELECT id, titulo, tipo, notas FROM panoramas
            WHERE NOT hecho AND (%s::text IS NULL OR tipo = %s::text)
            ORDER BY random() LIMIT 1
            """,
            (tipo, tipo),
        ).fetchone()


def marcar_panorama_hecho(panorama_id: int) -> dict | None:
    with conectar() as conn:
        return conn.execute(
            "UPDATE panoramas SET hecho = TRUE, hecho_en = NOW() WHERE id = %s AND NOT hecho RETURNING titulo",
            (panorama_id,),
        ).fetchone()


def borrar_panoramas(ids: list[int]) -> int:
    with conectar() as conn:
        return conn.execute("DELETE FROM panoramas WHERE id = ANY(%s)", (ids,)).rowcount


# ---------- tareas de la casa ----------


def crear_tarea(nombre: str, cada_dias: int, responsable_id: int, rotar: bool, proxima, creado_por: int) -> int:
    with conectar() as conn:
        return conn.execute(
            """
            INSERT INTO tareas (nombre, cada_dias, responsable_id, rotar, proxima, creado_por)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (nombre, cada_dias, responsable_id, rotar, proxima, creado_por),
        ).fetchone()["id"]


def listar_tareas() -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            """
            SELECT t.id, t.nombre, t.cada_dias, t.rotar, t.proxima, t.responsable_id,
                   u.nombre AS responsable, u.telegram_id AS responsable_telegram_id
            FROM tareas t JOIN usuarios u ON u.id = t.responsable_id
            WHERE t.activa ORDER BY t.proxima, t.id
            """
        ).fetchall()


def marcar_tarea_hecha(tarea_id: int, usuario_id: int, hoy) -> dict | None:
    """Registra que se hizo, programa la próxima y pasa el turno si se turnan. Devuelve la tarea actualizada."""
    with conectar() as conn:
        return conn.execute(
            """
            UPDATE tareas t SET
                ultimo_hecho_por = %(yo)s,
                ultimo_hecho_en = NOW(),
                proxima = %(hoy)s::date + t.cada_dias,
                responsable_id = CASE WHEN t.rotar THEN
                    COALESCE((SELECT id FROM usuarios WHERE id <> %(yo)s ORDER BY id LIMIT 1), t.responsable_id)
                    ELSE t.responsable_id END
            WHERE t.id = %(id)s AND t.activa
            RETURNING t.id, t.nombre, t.proxima, t.rotar,
                      (SELECT nombre FROM usuarios WHERE id = t.responsable_id) AS responsable
            """,
            {"yo": usuario_id, "hoy": hoy, "id": tarea_id},
        ).fetchone()


def borrar_tarea(tarea_id: int) -> bool:
    with conectar() as conn:
        return conn.execute("DELETE FROM tareas WHERE id = %s", (tarea_id,)).rowcount > 0


# ---------- envíos programados ----------


def reservar_envio(clave: str) -> bool:
    """Marca un envío automático como hecho. Devuelve False si ya se había hecho (no repetir)."""
    with conectar() as conn:
        return conn.execute("INSERT INTO envios_programados (clave) VALUES (%s) ON CONFLICT DO NOTHING",
                            (clave,)).rowcount > 0


# ---------- notas de la casa ----------


def guardar_nota(clave: str, valor: str, usuario_id: int) -> int:
    with conectar() as conn:
        return conn.execute(
            """
            INSERT INTO notas (clave, valor, creado_por) VALUES (%s, %s, %s)
            ON CONFLICT (clave) DO UPDATE
                SET valor = EXCLUDED.valor, creado_por = EXCLUDED.creado_por, actualizado_en = NOW()
            RETURNING id
            """,
            (clave.strip().lower(), valor.strip(), usuario_id),
        ).fetchone()["id"]


def buscar_notas(texto: str) -> list[dict]:
    """Notas cuya clave o valor contiene alguna de las palabras buscadas."""
    palabras = [f"%{p}%" for p in texto.lower().split() if len(p) >= 3] or [f"%{texto.lower()}%"]
    with conectar() as conn:
        return conn.execute(
            """
            SELECT id, clave, valor FROM notas
            WHERE clave ILIKE ANY(%s) OR valor ILIKE ANY(%s)
            ORDER BY actualizado_en DESC LIMIT 5
            """,
            (palabras, palabras),
        ).fetchall()


def todas_las_notas() -> list[dict]:
    with conectar() as conn:
        return conn.execute("SELECT id, clave, valor FROM notas ORDER BY clave").fetchall()


def borrar_nota(nota_id: int) -> bool:
    with conectar() as conn:
        return conn.execute("DELETE FROM notas WHERE id = %s", (nota_id,)).rowcount > 0
