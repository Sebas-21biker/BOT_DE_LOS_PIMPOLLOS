# Finanzas en pareja — Bot de Telegram + Dashboard

## Qué es
Sistema para que dos personas (Ricardo y su pareja) registren y analicen gastos e ingresos
conversando con un bot de Telegram en lenguaje natural, y vean dashboards con sus finanzas.

## Stack
- Python 3.12
- Bot: `python-telegram-bot` (modo webhook en producción, polling en local)
- IA: API de Anthropic (Claude) para interpretar mensajes, responder preguntas y generar análisis
- Base de datos: PostgreSQL (servicio de Railway)
- Dashboard: Streamlit (protegido con contraseña)
- Hosting: Railway, despliegue automático desde GitHub (rama `main`)

## Estructura del repo
```
bot/          # handlers de Telegram, intérprete con Claude
dashboard/    # app Streamlit
core/         # modelos, acceso a DB, lógica de negocio (saldos, conversión de monedas)
migrations/   # esquema SQL
.env.example  # variables de entorno sin valores
```

## Principios clave
- Los cálculos (totales, saldos, quién debe a quién) los hace SIEMPRE el código con SQL, nunca la IA.
- Claude solo interpreta texto → datos estructurados, y redacta respuestas/análisis a partir de datos ya calculados.
- Antes de guardar un movimiento interpretado por IA, el bot muestra lo entendido con botones Confirmar / Corregir.
- Se guarda el mensaje original de Telegram en cada movimiento.
- Lista blanca de `telegram_id`: solo los dos usuarios pueden usar el bot.
- Secretos solo en variables de entorno (TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, DATABASE_URL, ALLOWED_TELEGRAM_IDS, DASHBOARD_PASSWORD). Nunca commitear `.env`.

## Monedas
- Moneda base: CLP.
- Monedas soportadas: CLP, USD, MXN (viaje futuro a México).
- Cada movimiento guarda monto original, moneda, tipo de cambio usado y monto convertido a CLP.

## Esquema de datos
- `usuarios`: id, nombre, telegram_id, moneda_preferida
- `categorias`: id, nombre, tipo (gasto/ingreso), emoji, categoria_padre_id
- `movimientos`: id, tipo, monto, moneda, monto_base, tipo_cambio_usado, categoria_id, pagado_por,
  fecha, descripcion, es_compartido, mensaje_original, creado_en
- `repartos`: movimiento_id, usuario_id, porcentaje
- `liquidaciones`: id, de_usuario, a_usuario, monto, moneda, fecha
- `tipos_cambio`: fecha, moneda_origen, moneda_destino, valor
- `presupuestos`: categoria_id, monto, moneda, mes, alerta_al_porcentaje
- `recurrentes`: descripcion, monto, moneda, categoria_id, frecuencia, dia_del_mes, activo
- `metas_ahorro`: nombre, monto_objetivo, moneda, fecha_objetivo, monto_actual

El saldo entre ambos NO se guarda: se calcula desde movimientos + repartos + liquidaciones.

## Hoja de ruta
1. Esquema en Postgres + migración inicial
2. Bot con comandos simples (/gasto, /ingreso, /resumen, /saldo) desplegado en Railway
3. Registro y preguntas en lenguaje natural con Claude
4. Dashboard en Streamlit
5. Presupuestos con alertas, recurrentes, metas de ahorro, resumen mensual automático

## Decisiones tomadas
- Los gastos son individuales por defecto. Solo son compartidos si el mensaje lo indica
  ("compartido", "a medias" → 50/50; "40/60" → 40% quien paga, 60% la otra persona).
  Hablar en plural ("gastamos", "salimos a comer") NO lo hace compartido.
- Registro principal: texto libre interpretado por Claude (`bot/interprete.py`, salida estructurada
  con JSON schema) → confirmación con botones. /gasto e /ingreso quedan como registro rápido sin IA.
- Modelo configurable con la variable CLAUDE_MODEL (por defecto claude-opus-5-5).
- Si el mensaje no indica categoría, se asigna automáticamente (en fase 2 por palabras clave en
  `core/parser.py`; desde la fase 3 la inferirá Claude). Si nada calza → "Otros".
- Categorías iniciales en `migrations/002_categorias_iniciales.sql`.
- `liquidaciones` también guarda `monto_base` y `tipo_cambio_usado` para calcular el saldo en CLP.
- Fechas en zona horaria America/Santiago.
- Movimientos de prueba: si el mensaje trae "test" o "#prueba" se guardan con `es_prueba = true`.
  Toda consulta de saldos/resúmenes/dashboards debe filtrar `NOT es_prueba`. /borrar_pruebas los elimina.

## Decisiones pendientes
- Visibilidad de ingresos individuales (por ahora /resumen muestra solo los ingresos propios)

## Personalidad del bot
"Pimpollo": amigo chileno buena onda. Modismos suaves (wena, cachai, al tiro, pucha), frases cortas,
1-2 emojis. Nunca toma partido entre la pareja ni juzga gastos. Aplica a textos fijos de `bot/main.py`
y a lo que redacta Claude (prompt en `bot/interprete.py`).

## Dashboard
Se hará como Mini App de Telegram (en vez de / antes que Streamlit).

## Forma de trabajar
- Idioma del código: nombres en español para dominio (movimientos, categorias), inglés aceptable para lo técnico.
- Avanzar por fases pequeñas y probar en local antes de hacer push.
