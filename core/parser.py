"""Interpreta los argumentos de /gasto e /ingreso (sin IA).

Formato libre: el monto puede ir en cualquier posición.
    /gasto 15000 super lider
    /gasto 32.500 cena sushi compartido
    /gasto 20k arriendo 40/60      -> 40% quien paga, 60% la otra persona
"""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

MONEDAS = {"clp", "usd", "mxn"}

# Palabras clave -> nombre de categoría (deben coincidir con migrations/002).
PALABRAS_CATEGORIA = {
    "Supermercado": ["super", "supermercado", "lider", "jumbo", "unimarc", "tottus", "santa isabel",
                     "feria", "verduleria", "almacen", "panaderia", "pan"],
    "Arriendo": ["arriendo", "renta", "dividendo", "alquiler"],
    "Cuentas": ["cuenta", "cuentas", "luz", "agua", "gas", "internet", "celular", "telefono",
                "plan", "gastos comunes", "netflix", "spotify", "suscripcion"],
    "Transporte": ["uber", "didi", "cabify", "taxi", "bencina", "nafta", "metro", "micro", "bip",
                   "peaje", "tag", "estacionamiento", "transporte"],
    "Comida fuera": ["cena", "almuerzo", "desayuno", "restaurant", "restaurante", "sushi", "pizza",
                     "cafe", "completo", "delivery", "rappi", "pedidosya", "comida", "bar", "cerveza"],
    "Salud": ["farmacia", "remedio", "remedios", "doctor", "medico", "dentista", "isapre",
              "fonasa", "examen", "salud", "consulta"],
    "Ocio": ["cine", "concierto", "entrada", "entradas", "juego", "juegos", "carrete", "ocio", "panorama"],
    "Viajes": ["viaje", "vuelo", "pasaje", "pasajes", "hotel", "airbnb", "hostal", "mexico"],
    "Mascotas": ["veterinario", "vet", "mascota", "perro", "gato", "alimento perro", "alimento gato"],
    "Sueldo": ["sueldo", "salario", "pago mensual", "remuneracion"],
    "Ventas": ["venta", "ventas", "vendi"],
}

_RE_REPARTO = re.compile(r"^(\d{1,3})/(\d{1,3})$")
_SUFIJOS_MILES = ("lucas", "luca", "mil", "k")
_PALABRAS_COMPARTIDO = {"compartido", "compartida", "medias", "50/50"}


@dataclass
class Interpretado:
    monto: Decimal
    moneda: str
    descripcion: str
    categoria: str | None           # nombre de categoría detectada, o None
    porcentaje_pagador: Decimal | None  # None = gasto individual


class ErrorFormato(ValueError):
    pass


def normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sin_tildes.lower().strip()


def parsear_monto(token: str) -> Decimal | None:
    """'15000', '15.000', '$15.000', '15k', '15lucas', '12,5' -> Decimal. None si no es un monto."""
    t = normalizar(token).replace("$", "")
    multiplicador = 1
    for sufijo in _SUFIJOS_MILES:
        if t.endswith(sufijo) and t != sufijo:
            multiplicador = 1000
            t = t.removesuffix(sufijo)
            break
    if not t or not re.fullmatch(r"[\d.,]+", t) or not any(c.isdigit() for c in t):
        return None
    t = t.replace(".", "").replace(",", ".")  # punto = miles, coma = decimales
    try:
        valor = Decimal(t) * multiplicador
    except InvalidOperation:
        return None
    return valor if valor > 0 else None


def detectar_categoria(texto: str, nombres_categorias: list[str]) -> str | None:
    texto_n = f" {normalizar(texto)} "
    for nombre in nombres_categorias:
        if f" {normalizar(nombre)} " in texto_n:
            return nombre
    for nombre, palabras in PALABRAS_CATEGORIA.items():
        if nombre in nombres_categorias and any(f" {p} " in texto_n for p in palabras):
            return nombre
    return None


def interpretar(args: list[str], nombres_categorias: list[str], permitir_reparto: bool = True) -> Interpretado:
    monto = None
    moneda = "CLP"
    porcentaje_pagador = None
    resto = []
    saltar_siguiente = False

    for i, token in enumerate(args):
        t = normalizar(token)
        siguiente = normalizar(args[i + 1]) if i + 1 < len(args) else ""
        if permitir_reparto and t == "a" and siguiente == "medias":
            continue  # "a medias" -> lo toma "medias"
        if monto is None and (valor := parsear_monto(token)) is not None:
            monto = valor
            if siguiente in _SUFIJOS_MILES:  # "45 lucas", "20 mil"
                monto *= 1000
                saltar_siguiente = True
                continue
        elif saltar_siguiente:
            saltar_siguiente = False
        elif t in MONEDAS:
            moneda = t.upper()
        elif permitir_reparto and t in _PALABRAS_COMPARTIDO:
            porcentaje_pagador = Decimal(50)
        elif permitir_reparto and (m := _RE_REPARTO.match(t)):
            a, b = int(m.group(1)), int(m.group(2))
            if a + b != 100:
                raise ErrorFormato(f"El reparto {a}/{b} no suma 100.")
            porcentaje_pagador = Decimal(a)
        else:
            resto.append(token)

    if monto is None:
        raise ErrorFormato("No encontré el monto.")

    descripcion = " ".join(resto).strip()
    return Interpretado(
        monto=monto,
        moneda=moneda,
        descripcion=descripcion,
        categoria=detectar_categoria(descripcion, nombres_categorias),
        porcentaje_pagador=porcentaje_pagador,
    )
