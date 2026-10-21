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

## Estructura del bot
- `bot/main.py`: arranque (bot + dashboard + bucle de recordatorios) y ruteo del texto libre.
- `bot/interprete.py`: interpretación en DOS pasos: (1) Claude elige la intención (schema mínimo),
  (2) extrae solo los campos de esa intención (`_CAMPOS_POR_INTENCION`). Un schema único con todo superó
  el límite de gramática de structured outputs; al agregar intenciones, sumar su campo ahí.
  Las correcciones saltan el paso 1. Las instrucciones (system) se cachean.
- `bot/pareja.py`: /invitar (link de un solo uso para registrar a la pareja, máx. 2 usuarios), perfil
  (gustos, comidas, sueños, horarios, amigos, deportes...) e ideas personalizadas generadas con el perfil.
- Autorización: ALLOWED_TELEGRAM_IDS + usuarios registrados (filtro dinámico en `bot/main.py`).
- Correcciones de movimientos ya guardados: intenciones editar_movimiento / borrar_movimiento y /ultimos;
  siempre con confirmación antes → después. Solo se editan los propios (pagado_por).
- Automáticos (`consultas.bucle_programados`): tareas 9:00, ideas anticipadas 7 días antes de
  cumpleaños/aniversarios (recordatorios anuales; no se envían a la persona festejada), pregunta pimpolla
  martes y viernes 20:30 (`core/preguntas.py`, respuesta → perfil), resumen semanal domingo 20:00.
- Feriados (`core/feriados.py`): feriados legales nacionales desde date.nager.at (guardados en `feriados`)
  + fechas especiales calculadas (San Valentín, Día de la Madre/Padre/Niño, Halloween, Nochebuena...).
  Aviso la víspera de cada feriado (con botón de ideas), 7 días antes de fechas especiales (ideas de regalo
  privadas para San Valentín/Navidad) y 3 semanas antes de Navidad. Van en el contexto del intérprete y
  de las ideas. Santos: se guardan como recordatorio anual ("el santo de X es el...").
- Ideas: el perfil es contexto opcional; solo se usa lo relacionado con lo pedido. Se recuerdan las
  últimas 20 ideas para no repetir.
- Pagos automáticos (`bot/automatico.py`): POST /auto/{token} (token por usuario en `usuarios.token_auto`,
  /automatico lo entrega con instrucciones para Atajos de iPhone y MacroDroid). El aviso se interpreta y
  SIEMPRE se propone con Confirmar/Corregir; duplicados en 3 minutos se ignoran.
- Correos del banco (/correo): el usuario instala en SU Gmail un Apps Script
  (`bot/plantillas/pimpollo_gmail.gs`, con su URL secreta incrustada) que cada 5 min manda al mismo
  /auto/{token} los correos con la etiqueta "Pimpollo" (origen "correo", id del correo). El bot nunca tiene
  acceso al Gmail. En correos: el paso 1 se acota a movimientos/pago_pareja/otro; lo que no es movimiento
  (promos, claves, estados de cuenta) se ignora en silencio; transferencias con la pareja → pago_pareja.
  Antiduplicados: un correo con el mismo monto (±1 día) que un pago llegado por otro camino (Apple Pay o a
  mano) se descarta como "eco" (registrado en envios_programados con prefijo eco:).
- `bot/plata.py`: gastos/ingresos/pagos (con Confirmar/Corregir), /saldo, /resumen, /pague, avisos.
- `bot/hogar.py`: metas, lista de compras, recordatorios, panoramas y notas de la casa.
- `bot/comun.py`: utilidades compartidas.
- Gastos y pagos interpretados por IA piden confirmación; lo demás (lista, recordatorios, etc.) se guarda
  al tiro con botón Deshacer.
- Avisos a la pareja por privado cuando hay gastos compartidos, pagos, aportes a metas o recordatorios
  para ambos. En grupos no se avisa (los dos ven el chat) y el bot no responde a conversación normal.
- Lo ahorrado en metas se calcula sumando `aportes_meta`; no se guarda.
- `bot/consultas.py`: preguntas en lenguaje natural (Claude elige filtros, SQL calcula en
  `core/reportes.consultar`), foto de boleta (visión) y envíos programados: aviso de tareas a las 9:00 y
  resumen semanal los domingos 20:00 (`envios_programados` evita duplicados).
- Monedas USD/MXN: `core/cambio.py` baja el tipo de cambio una vez al día (open.er-api.com) y lo guarda
  en `tipos_cambio`; `monto_base` queda en CLP.
- Tareas de la casa con turnos (`tareas`), menú semanal generado por Claude con ingredientes a la lista.
- En grupos: /panel fija accesos directos; el bot conversa solo si lo nombran ("pimpollo") o le responden.
  Requiere privacy mode desactivado en BotFather.

## Personalidad del bot
"Pimpollo": amigo chileno buena onda. Modismos suaves (wena, cachai, al tiro, pucha), frases cortas,
1-2 emojis. Nunca toma partido entre la pareja ni juzga gastos. Aplica a textos fijos de `bot/main.py`
y a lo que redacta Claude (prompt en `bot/interprete.py`).

## Dashboard
Mini App de Telegram (reemplaza a Streamlit). Corre en el mismo proceso que el bot (`bot/main.py`):
- `dashboard/web.py`: servidor aiohttp en $PORT. Cada llamada a `/api/*` valida el `initData`
  firmado por Telegram (HMAC con el token del bot) + lista blanca. Sin contraseña.
- `dashboard/static/index.html`: página con Chart.js y colores del tema de Telegram.
- `core/reportes.py`: consultas SQL del dashboard (gastos de ambos, ingresos solo propios).
- URL pública: `RAILWAY_PUBLIC_DOMAIN` (o `DASHBOARD_URL`). Si existe, el bot pone el botón
  "📊 Dashboard" junto al cuadro de texto y habilita /dashboard.
- Toggle "Incluir pruebas" para ver movimientos 🧪.

## Forma de trabajar
- Idioma del código: nombres en español para dominio (movimientos, categorias), inglés aceptable para lo técnico.
- Avanzar por fases pequeñas y probar en local antes de hacer push.
