"""Grupo: ventana de conversación y detector de "¿me hablan?". Correos del banco que no se pueden perder. Sin base ni API."""

from datetime import datetime, timedelta

import pytest

from bot import automatico
from bot import conversacion as cv
from core.finanzas import ZONA

TARDE = datetime(2026, 11, 12, 18, 0, tzinfo=ZONA)
NOCHE = datetime(2026, 11, 12, 23, 30, tzinfo=ZONA)
YO = {"id": 1, "nombre": "Ricardo", "telegram_id": 111}


@pytest.fixture(autouse=True)
def limpio():
    cv._ventanas.clear()
    cv._detecciones.clear()


# ---------- filtro gratis ----------

@pytest.mark.parametrize("texto", ["jajaja", "😂😂", "ok", "dale", "gracias", "sii", "jsjs ya"])
def test_relleno_nunca_pasa(texto):
    assert not cv.podria_ser_para_mi(texto, ventana=True)


def test_fuera_de_la_ventana_solo_preguntas_o_pedidos():
    assert cv.podria_ser_para_mi("¿qué peli podemos ver hoy?", False)
    assert cv.podria_ser_para_mi("oye recomiéndanos algo para el sábado", False)
    assert not cv.podria_ser_para_mi("llego tarde hoy", False)
    assert not cv.podria_ser_para_mi("¿vienes?", False)                  # muy corto
    assert cv.podria_ser_para_mi("y para el domingo", True)               # en la ventana, casi todo


def test_ventana_dura_unos_minutos():
    cv.abrir_ventana(-100, 1, TARDE)
    assert cv.en_ventana(-100, 1, TARDE + timedelta(minutes=2))
    assert not cv.en_ventana(-100, 1, TARDE + cv.VENTANA + timedelta(seconds=1))
    assert not cv.en_ventana(-100, 2, TARDE)                              # es por persona


# ---------- detector ----------

@pytest.fixture
def detector(monkeypatch):
    llamadas = []

    async def falso(hilo, autor, texto, nombres, ventana):
        llamadas.append((texto, ventana))
        return True

    monkeypatch.setattr(cv.interprete, "detectar_si_me_hablan", falso)
    monkeypatch.setattr(cv.finanzas, "todos_los_usuarios", lambda: [YO, {"id": 2, "nombre": "Cami"}])
    prefs = {"nivel": "baja", "ajuste": 0, "silencio_hasta": None}
    monkeypatch.setattr(cv.iniciativa, "preferencias", lambda chat: prefs)
    return llamadas, prefs


async def test_pregunta_al_aire_pasa_por_el_detector(detector):
    llamadas, _ = detector
    assert await cv.me_hablan(-100, YO, "¿cuánto llevamos gastado este mes?", "", TARDE)
    assert llamadas == [("¿cuánto llevamos gastado este mes?", False)]


async def test_charla_normal_no_gasta_ia(detector):
    llamadas, _ = detector
    assert not await cv.me_hablan(-100, YO, "voy saliendo del trabajo", "", TARDE)
    assert llamadas == []


async def test_respeta_silencio_off_y_descanso(detector):
    llamadas, prefs = detector
    assert not await cv.me_hablan(-100, YO, "¿qué peli podemos ver?", "", NOCHE)   # descanso, sin ventana
    prefs["silencio_hasta"] = TARDE + timedelta(hours=10)
    assert not await cv.me_hablan(-100, YO, "¿qué peli podemos ver?", "", TARDE)
    prefs.update(silencio_hasta=None, nivel="off")
    assert not await cv.me_hablan(-100, YO, "¿qué peli podemos ver?", "", TARDE)
    assert llamadas == []


async def test_en_la_ventana_sigue_la_conversacion_aunque_sea_de_noche(detector):
    llamadas, _ = detector
    cv.abrir_ventana(-100, 1, NOCHE - timedelta(minutes=1))
    assert await cv.me_hablan(-100, YO, "y para el domingo", "Pimpollo: ¿les tinca el sábado?", NOCHE)
    assert llamadas == [("y para el domingo", True)]


async def test_tope_diario_del_detector(detector, monkeypatch):
    monkeypatch.setattr(cv, "MAX_DETECCIONES_DIA", 2)
    resultados = [await cv.me_hablan(-100, YO, "¿qué peli podemos ver?", "", TARDE) for _ in range(3)]
    assert resultados == [True, True, False]


# ---------- correos del banco ----------

def test_montos_sin_signo_peso():
    assert automatico._RE_MONTO.search("Compra por 12.990 en LIDER")
    assert automatico._RE_MONTO.search("cargo de 5000 pesos")
    assert not automatico._RE_MONTO.search("Tu clave dinámica fue cambiada")


class _Bot:
    def __init__(self):
        self.enviados = []

    async def send_message(self, chat_id, texto, **_):
        self.enviados.append((chat_id, texto))


@pytest.fixture
def correo(monkeypatch):
    for modulo, nombre, valor in [(automatico.finanzas, "otro_usuario", lambda uid: None),
                                  (automatico.finanzas, "nombres_categorias", lambda tipo: ["Otros"]),
                                  (automatico.metas, "listar", lambda: []),
                                  (automatico.hogar, "listar_tareas", lambda: [])]:
        monkeypatch.setattr(modulo, nombre, valor)
    app = type("A", (), {"bot": _Bot(), "bot_data": {}})()

    def con(resultado):
        async def falso(*a, **k):
            if isinstance(resultado, Exception):
                raise resultado
            return resultado
        monkeypatch.setattr(automatico.interprete, "interpretar", falso)
        return app
    return con


async def test_promocion_se_ignora_callado(correo):
    app = correo({"intencion": "otro", "intencion_inicial": "otro"})
    await automatico.procesar_pago(app, YO, "Asunto: 30% dcto en tu próxima compra de $10.000", "correo")
    assert app.bot.enviados == []


async def test_cargo_que_no_se_pudo_leer_se_avisa(correo):
    app = correo({"intencion": "otro", "intencion_inicial": "movimientos"})
    await automatico.procesar_pago(app, YO, "Asunto: Cargo en cuenta por $45.990", "correo")
    assert len(app.bot.enviados) == 1 and "no lo pude leer" in app.bot.enviados[0][1]


async def test_si_la_ia_falla_tambien_se_avisa(correo):
    app = correo(automatico.interprete.ErrorInterprete("API caída"))
    await automatico.procesar_pago(app, YO, "Asunto: Compra con tarjeta por $8.500", "correo")
    assert len(app.bot.enviados) == 1 and app.bot.enviados[0][0] == 111
