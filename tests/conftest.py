"""Los tests NO usan la base de datos ni la API: el .env local apunta a producción.

Se fijan variables falsas ANTES de importar el código (load_dotenv no pisa variables ya definidas), así cualquier
conexión accidental falla al tiro en vez de tocar datos reales.
"""

import os
import sys
from pathlib import Path

os.environ["DATABASE_URL"] = "postgresql://tests@127.0.0.1:1/no_existe?connect_timeout=1"
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:TEST"
os.environ["ANTHROPIC_API_KEY"] = "sk-test"
os.environ["ALLOWED_TELEGRAM_IDS"] = "1,2"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
