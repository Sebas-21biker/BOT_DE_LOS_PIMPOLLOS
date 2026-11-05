"""Privacidad del perfil: las ideas de regalo sobre la pareja son sorpresa."""

from core.perfil import es_sorpresa, visibles_para

RICARDO, CAMI = 1, 2


def fila(id_, usuario_id, categoria, creado_por):
    return {"id": id_, "usuario_id": usuario_id, "categoria": categoria, "dato": "x", "creado_por": creado_por}


FILAS = [
    fila(1, CAMI, "regalos", RICARDO),     # Ricardo anotó una idea de regalo para la Cami -> sorpresa
    fila(2, CAMI, "regalos", CAMI),        # la Cami dijo qué quiere -> no es sorpresa
    fila(3, CAMI, "comida", RICARDO),      # gusto normal
    fila(4, None, "regalos", RICARDO),     # "de los dos" -> no es sorpresa
    fila(5, RICARDO, "regalos", None),     # sin autor (datos antiguos) -> no es sorpresa
]


def ids(filas):
    return [f["id"] for f in filas]


def test_es_sorpresa():
    assert [es_sorpresa(f) for f in FILAS] == [True, False, False, False, False]


def test_la_festejada_no_ve_su_sorpresa():
    assert ids(visibles_para(FILAS, CAMI)) == [2, 3, 4, 5]


def test_quien_la_anoto_si_la_ve():
    assert ids(visibles_para(FILAS, RICARDO)) == [1, 2, 3, 4, 5]


def test_en_el_grupo_no_se_ve_ninguna_sorpresa():
    assert ids(visibles_para(FILAS, RICARDO, en_grupo=True)) == [2, 3, 4, 5]
