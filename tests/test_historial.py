"""Memoria corta de la conversación (el hilo). Sin base ni API."""

from datetime import datetime

import pytest

from bot import historial, interprete
from core.finanzas import ZONA


@pytest.fixture(autouse=True)
def limpio():
    historial._hilos.clear()
    yield
    historial._hilos.clear()


def test_hilo_reciente_y_acotado():
    historial.anotar(1, "Pimpollo", "<b>✅ ¡Listo, anotado!</b>\n💸 Gasto de $1.100", ahora=1000)
    historial.anotar(1, "Ricardo", "me equivoqué, estaba bien", ahora=1100)
    historial.anotar(2, "Cami", "otro chat", ahora=1100)
    assert historial.reciente(1, ahora=1200) == ("Pimpollo: ✅ ¡Listo, anotado! 💸 Gasto de $1.100\n"
                                                 "Ricardo: me equivoqué, estaba bien")
    assert historial.reciente(1, ahora=1100 + historial.MINUTOS * 60 - 1) == "Ricardo: me equivoqué, estaba bien"
    assert historial.reciente(3) == ""
    for i in range(30):
        historial.anotar(1, "Ricardo", f"m{i}", ahora=2000)
    assert len(historial._hilos[1]) == historial.MAXIMO
    historial.anotar(None, "x", "sin chat")
    historial.anotar(1, "x", "   ")
    assert len(historial._hilos[1]) == historial.MAXIMO


async def test_el_interprete_recibe_el_hilo_y_lo_citado(monkeypatch):
    vistos = []

    async def falso_llamar(sistema, mensajes, schema, motivo, modelo=None):
        vistos.append(mensajes[0]["content"])
        return {"intencion": "otro", "respuesta": "dale"}

    monkeypatch.setattr(interprete, "_llamar", falso_llamar)
    monkeypatch.setattr(interprete.feriados, "proximos", lambda *a, **k: [])
    await interprete.interpretar("añádelo nuevamente", datetime(2026, 11, 11, 19, 0, tzinfo=ZONA), ["Otros"],
                                 ["Otros ingresos"], [], hilo="Ricardo: me equivoqué",
                                 responde_a="🗑️ Borrado ✅ Gasto de $1.100 Ruta 68")
    assert "Conversación reciente" in vistos[0] and "Ricardo: me equivoqué" in vistos[0]
    assert "RESPONDIENDO a este mensaje tuyo: «🗑️ Borrado ✅ Gasto de $1.100 Ruta 68»" in vistos[0]
    assert vistos[0].endswith("Mensaje: añádelo nuevamente") or "Mensaje: añádelo nuevamente" in vistos[0]


async def test_lo_que_dice_el_bot_queda_en_el_hilo(monkeypatch):
    class _Msj:
        chat_id = 7

    async def enviar(self, chat_id, text, *a, **k):
        return _Msj()

    async def editar(self, text, chat_id=None, *a, **k):
        return _Msj()

    monkeypatch.setattr(historial.ExtBot, "send_message", enviar)
    monkeypatch.setattr(historial.ExtBot, "edit_message_text", editar)
    bot = historial.BotConMemoria("1:x")
    await bot.send_message(chat_id=7, text="Gasto de $1.100", parse_mode="HTML")
    await bot.edit_message_text(text="Borrado, como si nada", chat_id=7, message_id=3)
    assert historial.reciente(7) == "Pimpollo: Gasto de $1.100\nPimpollo: (editó su mensaje) Borrado, como si nada"
