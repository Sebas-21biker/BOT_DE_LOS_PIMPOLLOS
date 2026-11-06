"""Fase A: cuándo Pimpollo se mete solo en la charla y a quién le avisa un deseo (sin base ni API)."""

from datetime import datetime, timedelta

import pytest

from bot import conversacion as cv
from core.finanzas import ZONA

AHORA = datetime(2026, 11, 7, 16, 0, tzinfo=ZONA)  # sábado 16:00
PREFS = {"nivel": "baja", "ajuste": 0, "silencio_hasta": None}
EV = {"tema": "qué hacer el finde", "sensible": False, "ya_resuelto": False,
      "oportunidad": "planificar_salida", "utilidad": 9, "que_aportaria": "", "deseos": []}


def _decidir(ev=None, ahora=AHORA, prefs=None, anteriores=(), ultimo=None, al_bot=None):
    return cv.decidir_grupo({**EV, **(ev or {})}, ahora, {**PREFS, **(prefs or {})}, list(anteriores),
                            ultimo or ahora - timedelta(minutes=3), al_bot)


def test_interviene_si_es_util():
    si, razon = _decidir()
    assert si, razon


@pytest.mark.parametrize("cambio,razon", [
    ({"ev": {"sensible": True}}, "tema sensible"),
    ({"ev": {"ya_resuelto": True}}, "nada que aportar"),
    ({"ev": {"oportunidad": "ninguna"}}, "nada que aportar"),
    ({"ev": {"utilidad": 7}}, "puntaje 7 < umbral 8"),
    ({"prefs": {"nivel": "off"}}, "iniciativa apagada"),
    ({"prefs": {"ajuste": 2}}, "puntaje 9 < umbral 10"),
    ({"prefs": {"silencio_hasta": AHORA + timedelta(hours=1)}}, "le pidieron silencio"),
    ({"ahora": AHORA.replace(hour=23)}, "horario de descanso"),
    ({"ahora": AHORA.replace(hour=8, minute=29)}, "horario de descanso"),
    ({"ultimo": AHORA - timedelta(minutes=30)}, "ya pasó el momento"),
    ({"al_bot": AHORA - timedelta(minutes=4)}, "el bot acaba de hablar"),
    ({"anteriores": [AHORA - timedelta(hours=2)]}, "cooldown"),
    ({"anteriores": [AHORA - timedelta(hours=4), AHORA - timedelta(hours=7)]}, "máximo del día"),
])
def test_no_interviene(cambio, razon):
    if "ahora" in cambio:
        cambio["ultimo"] = cambio["ahora"] - timedelta(minutes=3)
    assert _decidir(**cambio) == (False, razon)


def test_cada_intervencion_del_dia_resta_y_la_alta_es_mas_suelta():
    assert _decidir(ev={"utilidad": 8})[0] is True
    assert _decidir(ev={"utilidad": 8}, anteriores=[AHORA - timedelta(hours=4)]) == (False, "puntaje 7 < umbral 8")
    assert _decidir(ev={"utilidad": 6}, prefs={"nivel": "alta"})[0] is True
    assert _decidir(ev={"utilidad": 8}, prefs={"ajuste": -1})[0] is True    # 👍 baja el umbral


def test_ayer_no_cuenta_para_el_maximo_del_dia():
    ayer = AHORA - timedelta(days=1)
    assert _decidir(anteriores=[ayer, ayer - timedelta(hours=1)])[0] is True


def test_descanso_y_silencio_por_hoy():
    assert cv.en_descanso(AHORA.replace(hour=22, minute=30)) and not cv.en_descanso(AHORA.replace(hour=8, minute=30))
    assert cv.fin_del_descanso(AHORA) == datetime(2026, 11, 8, 8, 30, tzinfo=ZONA)
    assert cv.fin_del_descanso(AHORA.replace(hour=7)) == datetime(2026, 11, 7, 8, 30, tzinfo=ZONA)


def test_pedir_silencio_o_volver():
    assert cv.pide_silencio("Pimpollo, ¡cállate un rato!") and cv.pide_silencio("pimpollo silencio por hoy")
    assert not cv.pide_silencio("pimpollo qué hacemos hoy")
    assert cv.pide_hablar("pimpollo ya puedes hablar")


@pytest.mark.parametrize("texto", ["estoy muy enojada contigo", "mañana tengo hora al médico",
                                   "me siento triste", "murió el tata"])
def test_temas_sensibles(texto):
    assert cv.es_sensible(texto)


@pytest.mark.parametrize("texto", ["qué hacemos el sábado?", "veamos una peli", "tengo hambre, pidamos sushi"])
def test_temas_normales(texto):
    assert not cv.es_sensible(texto)


# ---------- avisos de sorpresas por privado ----------

def _anterior(texto, hace_dias):
    return {"texto": texto, "creado_en": AHORA - timedelta(days=hace_dias)}


def test_decidir_sorpresa():
    deseo = "le encantaría ir a Valparaíso"
    assert cv.decidir_sorpresa(deseo, AHORA, PREFS, [])
    assert not cv.decidir_sorpresa(deseo, AHORA, PREFS, [_anterior("Le encantaria ir a Valparaiso", 10)])  # repetido
    assert not cv.decidir_sorpresa(deseo, AHORA, PREFS, [_anterior("otra cosa", 1)])           # cada 3 días
    assert cv.decidir_sorpresa(deseo, AHORA, PREFS, [_anterior("otra cosa", 4)])
    assert not cv.decidir_sorpresa(deseo, AHORA, {**PREFS, "ajuste": 1}, [_anterior("otra cosa", 4)])  # 🤫
    assert not cv.decidir_sorpresa(deseo, AHORA, {**PREFS, "nivel": "off"}, [])
    assert not cv.decidir_sorpresa(deseo, AHORA.replace(hour=23), PREFS, [])


class _Bot:
    def __init__(self):
        self.enviados = []

    async def send_message(self, chat_id, texto, **_):
        self.enviados.append((chat_id, texto))


class _App:
    def __init__(self):
        self.bot, self.bot_data = _Bot(), {}


USUARIOS = [{"id": 1, "nombre": "Ricardo", "telegram_id": 111}, {"id": 2, "nombre": "Cami", "telegram_id": 222}]


@pytest.fixture
def base_falsa(monkeypatch):
    registradas = []
    monkeypatch.setattr(cv.iniciativa, "preferencias", lambda chat: {**PREFS, "chat_id": chat})
    monkeypatch.setattr(cv.iniciativa, "intervenciones_recientes", lambda chat, tipo, dias=30: [])
    monkeypatch.setattr(cv.iniciativa, "registrar_intervencion",
                        lambda chat, tipo, tema, utilidad, texto: registradas.append((chat, tipo, texto)) or 7)
    return registradas


async def test_el_deseo_le_llega_solo_a_la_otra_persona(base_falsa):
    app = _App()
    ev = {**EV, "deseos": [{"quien": "Cami", "deseo": "le encantaría ir a Valparaíso", "tipo": "viaje"}]}
    await cv._avisar_deseos(app, ev, USUARIOS, AHORA)
    assert [chat for chat, _ in app.bot.enviados] == [111]          # a Ricardo, nunca a la Cami
    assert "Valparaíso" in app.bot.enviados[0][1]
    assert base_falsa == [(111, "sorpresa", "le encantaría ir a Valparaíso")]


async def test_deseo_de_alguien_desconocido_se_ignora(base_falsa):
    app = _App()
    ev = {**EV, "deseos": [{"quien": "Pedro", "deseo": "quiere una moto", "tipo": "regalo"}]}
    await cv._avisar_deseos(app, ev, USUARIOS, AHORA)
    assert app.bot.enviados == [] and base_falsa == []


# ---------- el tramo que se manda a Haiku ----------

def test_formatear_tramo_y_filtro_previo(monkeypatch):
    tramo = [{"creado_en": AHORA, "nombre": "Cami", "al_bot": False, "texto": "qué hacemos mañana?"},
             {"creado_en": AHORA, "nombre": "Ricardo", "al_bot": True, "texto": "pimpollo anota leche"}]
    assert cv.formatear_tramo(tramo) == ("[16:00] Cami: qué hacemos mañana?\n"
                                         "[16:00] Ricardo [al bot]: pimpollo anota leche")


async def test_tramo_sensible_no_llama_a_la_ia(monkeypatch):
    llamadas = []

    async def clasificar(*a, **k):
        llamadas.append(a)
        return EV
    monkeypatch.setattr(cv.interprete, "clasificar_conversacion", clasificar)
    tramo = [{"creado_en": AHORA, "nombre": "Cami", "al_bot": False, "texto": "estoy enojada por lo de ayer"}]
    await cv._evaluar(_App(), -100, tramo, AHORA)
    corto = [{"creado_en": AHORA, "nombre": "Cami", "al_bot": False, "texto": "jaja"}]
    await cv._evaluar(_App(), -100, corto, AHORA)
    assert llamadas == []
