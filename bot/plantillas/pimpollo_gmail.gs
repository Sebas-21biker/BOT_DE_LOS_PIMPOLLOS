/**
 * 🐣 Pimpollo — avisos del banco desde Gmail
 *
 * Revisa cada 5 minutos los correos con la etiqueta "Pimpollo" y se los manda al bot,
 * que te pregunta en Telegram si los anota. Nada se guarda sin tu confirmación.
 *
 * Cómo usarlo (una sola vez):
 *   1. Arriba, elige la función  instalar  y aprieta ▶ Ejecutar. Autoriza los permisos.
 *   2. Elige la función  probar  y ▶ Ejecutar: te debería llegar un mensaje de prueba del bot.
 * Para apagarlo: ejecuta  desinstalar  (o borra este proyecto).
 *
 * Este script corre en TU cuenta de Google. Al bot solo le llega el asunto y el texto de los
 * correos con la etiqueta "Pimpollo"; nunca tu clave ni el resto de tu correo.
 */

const URL_PIMPOLLO = "__URL__";   // tu dirección secreta (no la compartas)
const ETIQUETA = "Pimpollo";       // la etiqueta que le pone tu filtro de Gmail a los correos del banco
const MAX_TEXTO = 1500;            // caracteres del correo que se mandan

function revisarCorreos() {
  const props = PropertiesService.getScriptProperties();
  const instalado = Number(props.getProperty("INSTALADO_EN") || 0);
  const enviados = JSON.parse(props.getProperty("ENVIADOS") || "[]");
  const hilos = GmailApp.search("label:" + ETIQUETA + " newer_than:3d", 0, 30);

  for (const hilo of hilos) {
    for (const correo of hilo.getMessages()) {
      const id = correo.getId();
      // Solo correos nuevos (llegados después de instalar) y que no se hayan mandado antes.
      if (correo.getDate().getTime() < instalado || enviados.indexOf(id) !== -1) continue;

      const cuerpo = correo.getPlainBody().replace(/\s+/g, " ").trim().slice(0, MAX_TEXTO);
      const texto = "Asunto: " + correo.getSubject() + "\nFecha: " + correo.getDate().toISOString() +
                    "\n" + cuerpo;
      const respuesta = UrlFetchApp.fetch(URL_PIMPOLLO, {
        method: "post",
        contentType: "application/json",
        payload: JSON.stringify({ texto: texto, origen: "correo", id: id }),
        muteHttpExceptions: true,
      });
      const codigo = respuesta.getResponseCode();
      if (codigo === 404) {
        console.error("La dirección del bot ya no es válida. Pide una nueva con /correo en Telegram.");
        return;
      }
      if (codigo === 200) {
        enviados.push(id);
      } else {
        console.warn("El bot respondió " + codigo + "; se reintenta en 5 minutos.");
      }
    }
  }
  props.setProperty("ENVIADOS", JSON.stringify(enviados.slice(-300)));
}

function instalar() {
  desinstalar();
  ScriptApp.newTrigger("revisarCorreos").timeBased().everyMinutes(5).create();
  PropertiesService.getScriptProperties().setProperty("INSTALADO_EN", String(Date.now()));
  if (!GmailApp.getUserLabelByName(ETIQUETA)) GmailApp.createLabel(ETIQUETA);
  console.log("✅ Listo: reviso tus correos con la etiqueta \"" + ETIQUETA + "\" cada 5 minutos.");
}

function desinstalar() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === "revisarCorreos") ScriptApp.deleteTrigger(t);
  });
}

/**
 * Muestra (sin enviar nada) qué está viendo el script: si la etiqueta existe, si el revisor automático
 * está activo y qué correos con la etiqueta encontró, indicando por qué se mandarían o no.
 * Resultado en el "Registro de ejecución" de abajo.
 */
function diagnosticar() {
  const props = PropertiesService.getScriptProperties();
  const instalado = Number(props.getProperty("INSTALADO_EN") || 0);
  const enviados = JSON.parse(props.getProperty("ENVIADOS") || "[]");
  const disparadores = ScriptApp.getProjectTriggers().filter(function (t) {
    return t.getHandlerFunction() === "revisarCorreos";
  });

  console.log("1) Etiqueta \"" + ETIQUETA + "\": " + (GmailApp.getUserLabelByName(ETIQUETA) ? "✅ existe" :
              "❌ NO existe (revisa el nombre exacto de la etiqueta del filtro)"));
  console.log("2) Revisor automático (cada 5 min): " + (disparadores.length ? "✅ activo" :
              "❌ NO está activo → ejecuta la función instalar"));
  console.log("3) Instalado el: " + (instalado ? new Date(instalado).toLocaleString() :
              "❌ nunca → ejecuta la función instalar") + " · correos enviados al bot hasta ahora: " + enviados.length);

  const hilos = GmailApp.search("label:" + ETIQUETA + " newer_than:7d", 0, 20);
  console.log("4) Correos con la etiqueta en los últimos 7 días: " + hilos.length + " conversación(es)");
  if (!hilos.length) {
    console.log("   ❌ Ninguno: el filtro de Gmail no les está poniendo la etiqueta. Abre un correo de compra del " +
                "banco y revisa que tenga la etiqueta \"" + ETIQUETA + "\" y que el remitente del filtro sea igual.");
  }
  hilos.forEach(function (hilo) {
    hilo.getMessages().forEach(function (correo) {
      const fecha = correo.getDate();
      let estado = "✅ se mandaría";
      if (enviados.indexOf(correo.getId()) !== -1) estado = "✔️ ya se mandó al bot";
      else if (fecha.getTime() < instalado) estado = "⏭️ llegó ANTES de instalar (se ignora a propósito)";
      console.log("   • " + fecha.toLocaleString() + " | " + correo.getFrom() + " | " + correo.getSubject() +
                  " → " + estado);
    });
  });
}

function probar() {
  const respuesta = UrlFetchApp.fetch(URL_PIMPOLLO, {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify({
      texto: "test Asunto: Compra con tarjeta de débito\nSe realizó una compra por $1.990 en CAFETERIA PRUEBA PIMPOLLO",
      origen: "correo",
      id: "prueba-" + Date.now(),
    }),
    muteHttpExceptions: true,
  });
  console.log("Respuesta del bot: " + respuesta.getResponseCode() + " " + respuesta.getContentText() +
              "  → revisa Telegram 📱");
}
