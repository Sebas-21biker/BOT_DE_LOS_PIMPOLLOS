"""Persistencia del estado del bot (sin base de datos: se reemplazan las funciones que leen y escriben)."""

import pickle
from datetime import date
from decimal import Decimal

import pytest

from core import estado


@pytest.fixture
def base(monkeypatch):
    """Una 'tabla' estado_bot en memoria."""
    tabla: dict[str, bytes] = {}
    escrituras: list[str] = []

    def escribir(clave, datos):
        tabla[clave] = datos
        escrituras.append(clave)

    monkeypatch.setattr(estado, "_leer_todo", lambda: dict(tabla))
    monkeypatch.setattr(estado, "_escribir", escribir)
    monkeypatch.setattr(estado, "_borrar", lambda clave: tabla.pop(clave, None))
    return tabla, escrituras


PENDIENTE = {"pendientes": {"ab12": {"tipo": "movimientos", "items": [
    {"monto": Decimal("3500.00"), "fecha": date(2026, 11, 5), "porcentaje_pagador": None}],
    "mensaje": "uber 3500", "crudo": "{}", "es_prueba": False, "dueno": 1}}}


async def test_sobrevive_a_un_reinicio(base):
    p = estado.PersistenciaPostgres()
    await p.update_bot_data(PENDIENTE)
    await p.update_user_data(111, {"esperando": {"tipo": "pregunta", "pregunta": "¿Tu comida favorita?"}})

    nueva = estado.PersistenciaPostgres()  # "después del deploy"
    assert await nueva.get_bot_data() == PENDIENTE
    assert await nueva.get_user_data() == {111: {"esperando": {"tipo": "pregunta",
                                                               "pregunta": "¿Tu comida favorita?"}}}


async def test_no_reescribe_si_no_cambio(base):
    _, escrituras = base
    p = estado.PersistenciaPostgres()
    await p.get_bot_data()
    await p.update_bot_data({"a": 1})
    await p.update_bot_data({"a": 1})
    await p.update_bot_data({"a": 2})
    assert escrituras == ["bot", "bot"]


async def test_estado_ilegible_parte_de_cero(base):
    tabla, _ = base
    tabla["bot"] = b"esto no es pickle"
    assert await estado.PersistenciaPostgres().get_bot_data() == {}


async def test_falla_de_base_no_bota_el_bot(monkeypatch):
    def falla(*_):
        raise OSError("sin conexión")
    monkeypatch.setattr(estado, "_leer_todo", falla)
    monkeypatch.setattr(estado, "_escribir", falla)
    p = estado.PersistenciaPostgres()
    assert await p.get_bot_data() == {}
    await p.update_bot_data({"a": 1})  # no lanza


def test_podar_deja_los_mas_recientes():
    datos = {"ideas": {str(i): i for i in range(150)}, "ideas_recientes": list(range(150)), "otro": 5}
    estado.podar(datos)
    assert list(datos["ideas"]) == [str(i) for i in range(50, 150)]
    assert datos["ideas_recientes"] == list(range(50, 150))
    assert datos["otro"] == 5


def test_pickle_de_lo_que_guarda_el_bot():
    assert pickle.loads(pickle.dumps(PENDIENTE)) == PENDIENTE
