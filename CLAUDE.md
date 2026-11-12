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

## Evolución a asistente proactivo (plan aprobado 2026-11-05)
- Fase 0 (hecha): filtro del grupo, estado persistente, privacidad de regalos, precio de caché en /costo, tests.
- Fase A: conversación en el grupo con iniciativa. Buffer temporal de mensajes (48 h), se evalúa el tramo cuando la
  charla se pausa; clasificador con **Claude Haiku 5.5** (variable CLAUDE_MODEL_CLASIFICADOR), redacción con el
  modelo principal. **Horario de descanso 22:30–8:30**. **Nivel de iniciativa inicial: bajo** (cooldown, máx. por
  día, botones 👍/🤫). Nunca fingir emociones ni recuerdos. **Hecha** (ver "Iniciativa" abajo). Decidido: también
  por privado, pero solo avisos de sorpresas a partir de deseos dichos en el grupo; nunca temas sensibles.
- Fase B: memoria `recuerdos` (migra `perfil`): usuario, categoría, confianza, origen, visibilidad, vigencia;
  lo inferido se pregunta antes de guardar. Búsqueda con full-text de Postgres (sin embeddings por ahora). Tabla `planes`.
  **Hecha** (ver "Memoria y planes" abajo).
- Fase C: disparadores proactivos con motivo verificable por SQL, cooldown y horario de descanso.
- Fase D (postergada): Google Maps/Places y Spotify más adelante; Calendar/Sheets/Drive no por ahora.

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
- Costo de la IA: cada llamada a Claude se registra en `uso_ia` (tokens, modelo, motivo) y /costo calcula
  el gasto del mes con SQL usando los precios de `core/costos.py` (actualizarlos si cambian). Ahorros: los
  correos sin ningún monto se descartan sin llamar a Claude, y en avisos automáticos con intención "otro"
  no se hace el paso 2 (`extraer_otro=False`).
- Hilo de la conversación (`bot/historial.py`): por chat, los últimos 10 mensajes de los últimos 30 min (del
  usuario y del bot; `BotConMemoria` anota todo lo que el bot envía o edita). Va al intérprete como "Conversación
  reciente" junto con el mensaje del bot que el usuario cita (responder). Solo en memoria: un deploy lo olvida.
- Tarjetas, cuotas y fijos (`core/compromisos.py` + `bot/compromisos.py`, migración 014; /cuotas y /fijos muestran lo
  mismo, en el grupo se manda por privado). Tarjetas: cada persona las suyas (`tarjetas`: día de facturación y de
  pago); aviso por privado el día que factura y 2 días antes de pagar. Cuotas: el movimiento trae `cuotas` (monto =
  TOTAL) y `tarjeta`; al confirmar se crea `planes_cuotas` en vez del gasto. 1ª cuota = la primera facturación
  DESPUÉS de la compra (sin tarjeta: mismo día del mes siguiente); cada cuota se anota como movimiento normal
  (`plan_cuotas_id`) el día que toca, vía `anotar_cuotas_vencidas` (idempotente, FOR UPDATE). Cancelar = deja de
  anotar (conserva lo anotado); Deshacer (`dcuotas:`) = borra plan + cuotas (ON DELETE CASCADE). Fijos (tabla
  `recurrentes`, ahora con tipo/usuario_id): el día del mes que toca se PROPONE con Confirmar/Corregir/Cancelar
  (nunca se anotan solos); parten desde su creación. Todo corre en `avisos_diarios` a las 9:00.
  Apple Pay y los correos no traen las cuotas: se dicen al Corregir la propuesta o después con editar_movimiento
  (`cambios.cuotas`: borra el gasto y crea el plan). `origenes_similares` cuenta también los planes (monto_total),
  así el correo del mismo monto no se duplica.
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
  Requiere privacy mode desactivado en BotFather (verificado: `getMe` → can_read_all_group_messages = true;
  si se cambia, hay que sacar y volver a agregar el bot al grupo).
- Filtro del grupo (`bot/conversacion.py`): el bot recibe TODA la charla, pero solo pasa a la IA lo que le
  habla, trae un monto o suena a algo que sabe hacer (palabras clave + nombres de tareas). El resto no gasta
  tokens. Fotos del grupo: solo si el texto dice boleta/anota/un monto o responden al bot. En `uso_ia` los
  mensajes del grupo van con motivo "grupo". La pregunta pimpolla (privada) no se responde con charla del grupo.
- Estado persistente (`core/estado.py`, tabla `estado_bot`): bot_data y user_data de python-telegram-bot se
  guardan en Postgres (pickle) cada 15 s y al recibir SIGTERM. Las colecciones de bot_data se podan a las
  últimas 100. Si se cambia user_data fuera de un handler, llamar `app.mark_data_for_update_persistence`.
- Ideas de regalo sorpresa: un dato "regalos" que una persona anotó sobre la otra no lo ve la festejada (ni en
  /perfil ni en el contexto de las ideas), y en el grupo no se muestra (`perfil.visibles_para`).
- Iniciativa (fase A, `bot/conversacion.py` + `core/iniciativa.py`, migración 012): todo texto del grupo se guarda en
  `mensajes_grupo` (se borra a las 48 h; `al_bot` = ya lo procesó el bot). `bucle_iniciativa` (cada 30 s, nunca en
  22:30–8:30) toma los grupos con 2 min sin mensajes y evalúa el tramo nuevo UNA vez (`iniciativa.evaluado_hasta`).
  Antes de la IA: tramos cortos o con palabras sensibles (`_RAICES_SENSIBLES`) se descartan; tope 80 clasificaciones
  al día. Haiku (`interprete.clasificar_conversacion`, motivo "clasificador") devuelve tema, sensible, ya_resuelto,
  oportunidad, utilidad 0-10 y deseos. El código decide (`decidir_grupo`): nivel, silencio, descanso, momento pasado
  (20 min), bot recién habló (10 min), cooldown y máximo diario por nivel (`NIVELES`; baja = umbral 8, 3 h, 2/día),
  puntaje = utilidad − intervenciones de hoy ≥ umbral + ajuste. Redacta Opus (`redactar_intervencion`, motivo
  "iniciativa", contexto sin sorpresas) y puede desistir. Botones 👍/🤫 mueven `iniciativa.ajuste` (−2..3).
  "pimpollo, silencio" calla el grupo hasta las 8:30; "ya puedes hablar" lo revierte. /iniciativa baja|media|alta|off:
  en el grupo, la charla; en privado, los avisos de sorpresas de esa persona.
  Deseos ("me encantaría ir a…") se avisan por privado SOLO a la otra persona (`decidir_sorpresa`: sin repetir en 30
  días, cada 3 días en nivel baja), con botones guardar como idea de regalo secreta / ideas / menos avisos. Lo escrito
  en privado nunca se usa para escribirle al otro.
- Memoria y planes (fase B, migración 013): la tabla `perfil` se renombró a `recuerdos` (el módulo sigue siendo
  `core/perfil.py`) con confianza, origen (dicho/pregunta/grupo/inferido), visibilidad (todos/autor), vigente_hasta y
  columna `busqueda` (tsvector español, índice GIN). Queda una VISTA `perfil` solo por compatibilidad durante el
  deploy: se puede borrar en una migración futura. Visibilidad "autor" = solo lo ve quien lo anotó (sorpresas de
  regalo, "no le digas"); `visibles_para` la aplica y en el grupo oculta todo lo privado. Lo vencido no se lista.
  En perfil_guardar la IA marca `explicito`: lo explícito se guarda con Deshacer, lo deducido se propone con
  Sí/No (`recp:`, `bot_data["recuerdos_pendientes"]`). La respuesta a la pregunta pimpolla se guarda con origen
  "pregunta". En el grupo, Haiku también extrae `datos` que alguien dice de sí mismo: se le pregunta por privado a
  ESA persona (`decidir_dato`: máx. 1 al día, sin repetir, no si ya existe), botones `rec:`. perfil_ver acepta
  busqueda (full-text); las ideas usan `perfil.relevantes` cuando hay más de 40 recuerdos.
  `planes` (intenciones plan_nuevo / ver_planes, /planes, cancelar con `plan:cancelado:`): planes con fecha, distintos
  de panoramas (ideas sin fecha) y recordatorios. Se avisan a la pareja por privado y van al contexto de las
  intervenciones. La fase C usará planes y recuerdos para disparadores.
- Probar SQL sin tocar producción: hay PostgreSQL 18 local en C:\Program Files\PostgreSQL\18 (initdb + pg_ctl en un
  directorio temporal, puerto aparte); los tests de pytest no usan base.
- Tests: `pytest` (requirements-dev.txt). No tocan la base ni la API: el .env local apunta a producción, así que
  tampoco correr el bot en local con ese .env (le robaría los mensajes al de Railway).

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
