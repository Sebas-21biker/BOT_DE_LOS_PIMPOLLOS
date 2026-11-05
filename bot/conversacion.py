"""Cuándo vale la pena que Pimpollo procese un mensaje del grupo.

Fase 0: filtro gratis (sin IA). En el grupo el bot recibe TODA la conversación; antes cada mensaje costaba dos
llamadas a Claude aunque fuera un "jajaja". Ahora solo pasa a la IA lo que:
  - le habla al bot (lo nombran o le responden),
  - trae un monto (posible gasto/pago),
  - suena a algo que el bot sabe hacer (lista, recordatorio, meta, tarea, panorama, nota, perfil, consulta).
El resto se ignora. En la fase A aquí se sumará la clasificación de la conversación (Haiku) para intervenir
con iniciativa, con cooldown y horario de descanso.
"""

import re

from core.parser import normalizar

# Un monto: "5000", "5.000", "$5000", "5 lucas", "20k", "2 millones", "usd 30". Números chicos sueltos
# ("a las 8", "el 15") no cuentan.
_RE_MONTO = re.compile(
    r"\$\s?\d"
    r"|\b\d{1,3}(?:[.,]\d{3})+\b"
    r"|\b\d{4,}\b"
    r"|\b\d+(?:[.,]\d+)?\s?(?:k|lucas?|mil|millon(?:es)?|palos?|usd|mxn|clp|dolares?|pesos)\b"
    r"|\b(?:usd|mxn|clp)\s?\d"
)

# Raíces de palabras (sin tildes, en minúsculas) de cosas que el bot sabe hacer.
_RAICES = (
    # plata
    "gaste", "gastamos", "gasto", "pague", "pagamos", "pago", "transferi", "transfiri", "deposite", "compre",
    "a medias", "compartido", "saldo", "debo", "debe ", "debes", "cuanto", "presupuesto", "boleta",
    # metas
    "meta", "ahorr", "aparte ", "juntar",
    # lista de compras
    "lista", "agrega", "hay que comprar", "falta ", "faltan ", "se acabo", "se acabaron",
    # recordatorios y fechas
    "recuerda", "recordatorio", "acuerdate", "avisame", "avisanos", "cumple", "aniversario", "santo de",
    # panoramas e ideas
    "anota", "anotar", "guarda", "panorama", "pelicula", "peli ", "serie", "restaurant", "que hacemos",
    "que podemos hacer", "ideas", "recomienda", "regalo", "menu", "que cocinamos", "que comemos",
    # notas de la casa
    "clave", "wifi", "contrasena", "talla",
    # tareas
    "tarea", "me toca", "te toca", "le toca", "turno",
    # perfil
    "le gusta", "le encanta", "me encanta", "le carga", "suena con", "sueno con", "sonamos",
)


def tiene_monto(texto: str) -> bool:
    return bool(_RE_MONTO.search(normalizar(texto)))


def merece_ia(texto: str, tareas_activas: list[str] = ()) -> bool:
    """¿Este mensaje del grupo (que NO le habla al bot) podría ser algo que el bot debe registrar o responder?"""
    t = f" {normalizar(texto)} "
    if len(t.strip()) < 4:
        return False
    if tiene_monto(texto):
        return True
    if any(raiz in t for raiz in _RAICES):
        return True
    # "ya saqué la basura": la tarea se menciona por nombre.
    for tarea in tareas_activas:
        palabras = [p for p in normalizar(tarea).split() if len(p) > 3]
        if palabras and any(p in t for p in palabras):
            return True
    return False


_PALABRAS_FOTO = ("boleta", "voucher", "comprobante", "factura", "recibo", "ticket", "anota", "pimpollo",
                  "test", "prueba", "medias", "compartido")


def foto_merece_ia(caption: str) -> bool:
    """En el grupo las fotos (memes, selfies...) no se mandan a la IA salvo que el texto pida anotarla."""
    t = normalizar(caption or "")
    return bool(t) and (any(p in t for p in _PALABRAS_FOTO) or tiene_monto(t))
