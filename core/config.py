"""Configuración leída desde variables de entorno (y .env en local)."""

import os

from dotenv import load_dotenv

load_dotenv()


def _requerida(nombre: str) -> str:
    valor = os.getenv(nombre, "").strip()
    if not valor:
        raise RuntimeError(f"Falta la variable de entorno {nombre} (ver .env.example)")
    return valor


def telegram_bot_token() -> str:
    return _requerida("TELEGRAM_BOT_TOKEN")


def allowed_telegram_ids() -> set[int]:
    crudo = _requerida("ALLOWED_TELEGRAM_IDS")
    return {int(x) for x in crudo.split(",") if x.strip()}


def database_url() -> str:
    return _requerida("DATABASE_URL")


def anthropic_api_key() -> str:
    return _requerida("ANTHROPIC_API_KEY")


def claude_model() -> str:
    return os.getenv("CLAUDE_MODEL", "").strip() or "claude-opus-5-5"


def database_configurada() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())
