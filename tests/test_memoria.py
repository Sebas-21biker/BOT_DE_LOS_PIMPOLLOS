"""Fase B: memoria (recuerdos) y planes. Sin base ni API."""

from datetime import date, datetime, time, timedelta

import pytest

from bot import conversacion as cv
from bot import interprete, pareja
from core import perfil
from core.finanzas import ZONA

RICARDO, CAMI = 1, 2
AHORA = datetime(2026, 11, 7, 16, 0, tzinfo=ZONA)
PREFS = {"nivel": "baja", "ajuste": 0, "silencio_hasta": None}


def rec(id_, usuario_id, visibilidad="todos", creado_por=RICARDO, categoria="gustos", dias=0):
    return {"id": id_, "usuario_id": usuario_id, "categoria": categoria, "dato": f"dato {id_}",
            "creado_por": creado_por, "visibilidad": visibilidad, "creado_en": AHORA - timedelta(days=dias)}


# ---------- visibilidad ----------

def test_visibilidad_por_defecto():
    assert perfil.visibilidad_por_defecto(CAMI, "regalos", RICARDO) == "autor"   # sorpresa
    assert perfil.visibilidad_por_defecto(CAMI, "regalos", CAMI) == "todos"
    assert perfil.visibilidad_por_defecto(None, "regalos", RICARDO) == "todos"
    assert perfil.visibilidad_por_defecto(CAMI, "comida", RICARDO) == "todos"


def test_lo_privado_solo_lo_ve_quien_lo_anoto():
    filas = [rec(1, CAMI, "autor", RICARDO), rec(2, RICARDO, "autor", RICARDO), rec(3, CAMI), rec(4, None)]
    assert [f["id"] for f in perfil.visibles_para(filas, RICARDO)] == [1, 2, 3, 4]
    assert [f["id"] for f in perfil.visibles_para(filas, CAMI)] == [3, 4]
    assert [f["id"] for f in perfil.visibles_para(filas, RICARDO, en_grupo=True)] == [3, 4]


# ---------- búsqueda ----------

def test_consulta_texto():
    assert perfil.consulta_texto("¿Le gusta el cilantro?") == "gusta or cilantro"
    assert perfil.consulta_texto("a y o") == ""


def test_relevantes_con_memoria_grande():
    filas = [rec(i, CAMI, dias=i) for i in range(1, 101)]
    assert perfil.relevantes(filas[:30], []) == filas[:30]               # chica: va todo
    elegidos = perfil.relevantes(filas, [rec(90, CAMI), rec(999, CAMI)], maximo=10)
    ids = [f["id"] for f in elegidos]
    assert len(ids) == 10 and ids[0] == 90 and 999 not in ids          # lo encontrado (y visible) primero
    assert ids[1:] == list(range(1, 10))                                # y luego lo más reciente


# ---------- lo que extrae la IA ----------

def test_validar_dato_y_plan():
    d = interprete._validar_dato_perfil({"sobre": "yo", "categoria": "comida", "dato": " a dieta ",
                                         "explicito": True, "privado": False, "vigente_hasta": "2026-11-30"})
    assert d["vigente_hasta"] == date(2026, 11, 30) and d["dato"] == "a dieta"
    assert interprete._validar_dato_perfil({**d, "vigente_hasta": "pronto"})["vigente_hasta"] is None
    p = interprete._validar_plan({"titulo": "Asado de Juan", "fecha": "2026-11-08", "hora": "14:00",
                                  "lugar": "", "con_quien": "Juan"}, AHORA.date())
    assert (p["fecha"], p["hora"]) == (date(2026, 11, 8), time(14, 0))
    assert interprete._validar_plan({**p, "fecha": "x", "hora": "a la tarde"}, AHORA.date())["hora"] is None


def test_plan_sin_items_pasa_a_conversacion():
    base = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
            for k, v in interprete._VACIO.items()}
    datos = interprete._validar({**base, "intencion": "plan_nuevo", "crudo": "{}"}, AHORA, ["Otros"],
                                ["Otros ingresos"], [], [])
    assert datos["intencion"] == "otro"


# ---------- lo deducido se pregunta antes de guardar ----------

class _Mensaje:
    def __init__(self):
        self.respuestas = []

    async def reply_text(self, texto, **_):
        self.respuestas.append(texto)


class _Update:
    def __init__(self):
        self.effective_message = _Mensaje()
        self.effective_chat = type("C", (), {"type": "private"})()


class _Contexto:
    def __init__(self):
        self.bot_data = {}


@pytest.fixture
def guardados(monkeypatch):
    lista = []
    monkeypatch.setattr(pareja.finanzas, "otro_usuario", lambda uid: {"id": CAMI, "nombre": "Cami"})
    monkeypatch.setattr(pareja.perfil, "guardar", lambda *a, **k: lista.append((a, k)) or len(lista))
    return lista


async def test_lo_explicito_se_guarda_y_lo_deducido_se_pregunta(guardados):
    update, contexto = _Update(), _Contexto()
    datos = [{"sobre": "pareja", "categoria": "comida", "dato": "le encanta el sushi", "explicito": True,
              "privado": False, "vigente_hasta": None},
             {"sobre": "yo", "categoria": "comida", "dato": "no le gusta el cilantro", "explicito": False,
              "privado": False, "vigente_hasta": None}]
    await pareja.guardar_perfil(update, contexto, {"id": RICARDO, "nombre": "Ricardo"}, datos)
    assert len(guardados) == 1 and guardados[0][0][2] == "le encanta el sushi"
    pendiente = next(iter(contexto.bot_data["recuerdos_pendientes"].values()))
    assert pendiente["datos"][0]["dato"] == "no le gusta el cilantro" and pendiente["origen"] == "inferido"
    assert "¿lo guardo?" in update.effective_message.respuestas[1]


# ---------- datos escuchados en el grupo ----------

def test_decidir_dato():
    dato = "no le gusta el cilantro"
    assert cv.decidir_dato(dato, AHORA, PREFS, [], ya_lo_se=False)
    assert not cv.decidir_dato(dato, AHORA, PREFS, [], ya_lo_se=True)
    assert not cv.decidir_dato(dato, AHORA, PREFS, [{"texto": "No le gusta el cilantro", "creado_en": AHORA - timedelta(days=9)}], False)
    assert not cv.decidir_dato(dato, AHORA, PREFS, [{"texto": "otro", "creado_en": AHORA - timedelta(hours=3)}], False)
    assert cv.decidir_dato(dato, AHORA, PREFS, [{"texto": "otro", "creado_en": AHORA - timedelta(days=2)}], False)
    assert not cv.decidir_dato(dato, AHORA, {**PREFS, "nivel": "off"}, [], False)


class _Bot:
    def __init__(self):
        self.enviados = []

    async def send_message(self, chat_id, texto, **_):
        self.enviados.append(chat_id)


USUARIOS = [{"id": RICARDO, "nombre": "Ricardo", "telegram_id": 111},
            {"id": CAMI, "nombre": "Cami", "telegram_id": 222}]


async def test_el_dato_se_le_pregunta_a_quien_lo_dijo(monkeypatch):
    monkeypatch.setattr(cv.iniciativa, "preferencias", lambda chat: PREFS)
    monkeypatch.setattr(cv.iniciativa, "intervenciones_recientes", lambda *a: [])
    monkeypatch.setattr(cv.iniciativa, "registrar_intervencion", lambda *a: 5)
    monkeypatch.setattr(cv.perfil, "ya_existe", lambda uid, dato: False)
    app = type("A", (), {"bot": _Bot(), "bot_data": {}})()
    ev = {"datos": [{"quien": "Cami", "categoria": "comida", "dato": "no le gusta el cilantro"}]}
    await cv._preguntar_datos(app, ev, USUARIOS, AHORA)
    assert app.bot.enviados == [222]   # a la Cami, no a Ricardo
