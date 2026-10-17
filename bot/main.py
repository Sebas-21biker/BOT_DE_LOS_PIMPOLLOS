"""Punto de entrada del bot. En local corre en modo polling.

Uso: python -m bot.main
"""

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from core import config, db

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usuario_tg = update.effective_user
    if config.database_configurada():
        usuario = db.registrar_usuario(usuario_tg.id, usuario_tg.first_name)
        await update.message.reply_text(f"¡Hola {usuario['nombre']}! Ya estás registrado 💸")
    else:
        await update.message.reply_text(f"¡Hola {usuario_tg.first_name}! El bot funciona (sin base de datos aún).")


def main() -> None:
    autorizados = filters.User(user_id=config.allowed_telegram_ids())

    app = Application.builder().token(config.telegram_bot_token()).build()
    app.add_handler(CommandHandler("start", start, filters=autorizados))

    log.info("Bot iniciado en modo polling")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
