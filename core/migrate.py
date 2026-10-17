"""Aplica en orden los archivos .sql de migrations/ que aún no se han aplicado.

Uso: python -m core.migrate
"""

from pathlib import Path

from core.db import conectar

CARPETA_MIGRACIONES = Path(__file__).resolve().parent.parent / "migrations"


def migrar() -> None:
    with conectar() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                nombre     TEXT PRIMARY KEY,
                aplicada_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        aplicadas = {r["nombre"] for r in conn.execute("SELECT nombre FROM schema_migrations")}
        conn.commit()

        for archivo in sorted(CARPETA_MIGRACIONES.glob("*.sql")):
            if archivo.name in aplicadas:
                continue
            print(f"Aplicando {archivo.name}...")
            with conn.transaction():
                conn.execute(archivo.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (nombre) VALUES (%s)", (archivo.name,))

    print("Migraciones al día.")


if __name__ == "__main__":
    migrar()
