"""Persistencia del estado del bot en Postgres (bot_data y user_data de python-telegram-bot).

Así las propuestas con Confirmar/Corregir, la respuesta a la pregunta pimpolla, las ideas recientes, etc.
sobreviven a los reinicios y despliegues. El sistema de archivos de Railway es efímero: por eso Postgres y no
PicklePersistence.
"""

import asyncio
import logging
import pickle

from telegram.ext import BasePersistence, PersistenceInput

from core.db import conectar

log = logging.getLogger(__name__)

MAX_POR_COLECCION = 100  # pendientes, ideas, menús...: se guardan los últimos N de cada uno


def podar(bot_data: dict) -> dict:
    """Las colecciones de bot_data crecen con cada uso y nadie las limpia: se dejan solo las más recientes."""
    for clave, valor in list(bot_data.items()):
        if isinstance(valor, dict) and len(valor) > MAX_POR_COLECCION:
            bot_data[clave] = dict(list(valor.items())[-MAX_POR_COLECCION:])
        elif isinstance(valor, list) and len(valor) > MAX_POR_COLECCION:
            bot_data[clave] = valor[-MAX_POR_COLECCION:]
    return bot_data


def _leer_todo() -> dict[str, bytes]:
    with conectar() as conn:
        return {f["clave"]: bytes(f["datos"]) for f in conn.execute("SELECT clave, datos FROM estado_bot")}


def _escribir(clave: str, datos: bytes) -> None:
    with conectar() as conn:
        conn.execute(
            """INSERT INTO estado_bot (clave, datos) VALUES (%s, %s)
               ON CONFLICT (clave) DO UPDATE SET datos = EXCLUDED.datos, actualizado_en = NOW()""",
            (clave, datos))


def _borrar(clave: str) -> None:
    with conectar() as conn:
        conn.execute("DELETE FROM estado_bot WHERE clave = %s", (clave,))


def _deserializar(clave: str, crudo: bytes) -> dict:
    try:
        datos = pickle.loads(crudo)
        return datos if isinstance(datos, dict) else {}
    except Exception:
        log.exception("Estado guardado ilegible (%s): parto de cero para esa clave", clave)
        return {}


class PersistenciaPostgres(BasePersistence):
    """Guarda bot_data y user_data. No se usan chat_data, callback_data ni ConversationHandler."""

    def __init__(self, update_interval: float = 15):
        super().__init__(store_data=PersistenceInput(bot_data=True, user_data=True, chat_data=False,
                                                     callback_data=False),
                         update_interval=update_interval)
        self._cache: dict[str, bytes] | None = None
        self._ultimo: dict[str, bytes] = {}  # lo último escrito: si no cambió, no se vuelve a escribir

    async def _cargado(self) -> dict[str, bytes]:
        if self._cache is None:
            try:
                self._cache = await asyncio.to_thread(_leer_todo)
            except Exception:
                log.exception("No pude leer el estado guardado: parto de cero")
                self._cache = {}
            self._ultimo = dict(self._cache)
        return self._cache

    async def _guardar(self, clave: str, datos: dict) -> None:
        try:
            crudo = pickle.dumps(datos)
        except Exception:
            log.exception("No pude serializar el estado (%s)", clave)
            return
        if self._ultimo.get(clave) == crudo:
            return
        try:
            await asyncio.to_thread(_escribir, clave, crudo)
            self._ultimo[clave] = crudo
        except Exception:
            log.exception("No pude guardar el estado (%s)", clave)

    # ---- lectura al arrancar ----

    async def get_bot_data(self) -> dict:
        crudo = (await self._cargado()).get("bot")
        return podar(_deserializar("bot", crudo)) if crudo else {}

    async def get_user_data(self) -> dict[int, dict]:
        return {int(clave.removeprefix("user:")): _deserializar(clave, crudo)
                for clave, crudo in (await self._cargado()).items() if clave.startswith("user:")}

    async def get_chat_data(self) -> dict[int, dict]:
        return {}

    async def get_callback_data(self):
        return None

    async def get_conversations(self, name: str) -> dict:
        return {}

    # ---- escritura (python-telegram-bot llama cada `update_interval` segundos y al apagarse) ----

    async def update_bot_data(self, data: dict) -> None:
        await self._guardar("bot", data)

    async def update_user_data(self, user_id: int, data: dict) -> None:
        await self._guardar(f"user:{user_id}", data)

    async def drop_user_data(self, user_id: int) -> None:
        self._ultimo.pop(f"user:{user_id}", None)
        await asyncio.to_thread(_borrar, f"user:{user_id}")

    async def update_chat_data(self, chat_id: int, data: dict) -> None:
        pass

    async def drop_chat_data(self, chat_id: int) -> None:
        pass

    async def update_callback_data(self, data) -> None:
        pass

    async def update_conversation(self, name: str, key, new_state) -> None:
        pass

    async def refresh_user_data(self, user_id: int, user_data: dict) -> None:
        pass

    async def refresh_chat_data(self, chat_id: int, chat_data: dict) -> None:
        pass

    async def refresh_bot_data(self, bot_data: dict) -> None:
        pass

    async def flush(self) -> None:
        pass  # cada update_* ya escribe en la base
