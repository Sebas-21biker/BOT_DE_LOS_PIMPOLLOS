"""Filtro del grupo: qué mensajes pasan a la IA y cuáles se ignoran gratis."""

import pytest

from bot.conversacion import foto_merece_ia, merece_ia, tiene_monto


@pytest.mark.parametrize("texto", [
    "gasté 3500 en uber",
    "cena 40 lucas a medias",
    "le transferí 25.000 a la Cami",
    "pagué la luz, 32k",
    "$15000 el súper",
    "fueron 20 dólares",
    "agrega leche y huevos a la lista",
    "hay que comprar pan",
    "recuérdame pagar la luz el 5",
    "el 3 de marzo es el cumple de la Cami",
    "anota el restaurante Liguria para ir",
    "¿qué hacemos hoy?",
    "¿cuál era la clave del wifi?",
    "¿cuánto gastamos este mes?",
    "a la Cami le encanta el sushi",
    "aparté 50 lucas para México",
    "me toca sacar la basura?",
])
def test_pasan_a_la_ia(texto):
    assert merece_ia(texto)


@pytest.mark.parametrize("texto", [
    "jajajaja",
    "ok",
    "voy saliendo",
    "te amo ❤️",
    "llego a las 8",
    "el 15 nos juntamos con los chiquillos",
    "qué lata el tráfico",
    "😂😂😂",
])
def test_charla_normal_no_gasta_ia(texto):
    assert not merece_ia(texto)


def test_tarea_activa_por_nombre():
    assert merece_ia("ya regué las plantas", ["Regar las plantas"])
    assert not merece_ia("ya regué las plantas", [])


@pytest.mark.parametrize("texto,esperado", [
    ("5000", True), ("5.000", True), ("5 lucas", True), ("2 millones", True), ("usd 30", True),
    ("a las 8", False), ("el 15", False), ("somos 2", False),
])
def test_tiene_monto(texto, esperado):
    assert tiene_monto(texto) is esperado


def test_fotos_del_grupo():
    assert not foto_merece_ia("")
    assert not foto_merece_ia("mira este perro jaja")
    assert foto_merece_ia("boleta del súper")
    assert foto_merece_ia("anótala a medias")
    assert foto_merece_ia("fueron 12 lucas")
