"""Memoria corta de la conversación: los últimos mensajes de cada chat (usuario y bot) de los últimos minutos.

Sirve para que el intérprete entienda referencias ("eso", "añádelo de nuevo", "estaba bien") en vez de leer
cada mensaje aislado. Vive en memoria del proceso (si el bot se reinicia, se olvida; es a propósito: es solo
el hilo de la charla, no un recuerdo). Cada chat tiene su propio hilo: lo privado no se mezcla con el grupo.
"""

import re
import time

from telegram.ext import ExtBot

MINUTOS = 30          # lo más viejo que se considera parte del hilo
MAXIMO = 10           # mensajes que se guardan por chat
LARGO = 350           # caracteres por mensaje

_hilos: dict[int, list[tuple[float, str, str]]] = {}
_RE_TAGS = re.compile(r"<[^>]+>")


def _limpiar(texto: str) -> str:
    return " ".join(_RE_TAGS.sub("", texto or "").split())[:LARGO]


def anotar(chat_id: int | None, quien: str, texto: str, ahora: float | None = None) -> None:
    texto = _limpiar(texto)
    if chat_id is None or not texto:
        return
    hilo = _hilos.setdefault(chat_id, [])
    hilo.append((ahora or time.time(), quien, texto))
    del hilo[:-MAXIMO]


def reciente(chat_id: int, ahora: float | None = None) -> str:
    """El hilo reciente del chat como texto ("Quién: mensaje" por línea), o "" si no hay."""
    limite = (ahora or time.time()) - MINUTOS * 60
    return "\n".join(f"{quien}: {texto}" for t, quien, texto in _hilos.get(chat_id, []) if t >= limite)


def olvidar(chat_id: int) -> None:
    _hilos.pop(chat_id, None)


class BotConMemoria(ExtBot):
    """El bot de siempre, pero anota en el hilo lo que envía o edita (respuestas, propuestas, 'Borrado'...)."""

    async def send_message(self, chat_id, text, *args, **kwargs):
        mensaje = await super().send_message(chat_id, text, *args, **kwargs)
        anotar(mensaje.chat_id, "Pimpollo", text)
        return mensaje

    async def edit_message_text(self, text, chat_id=None, *args, **kwargs):
        resultado = await super().edit_message_text(text, chat_id, *args, **kwargs)
        destino = getattr(resultado, "chat_id", None) or chat_id
        anotar(destino, "Pimpollo", f"(editó su mensaje) {text}")
        return resultado
