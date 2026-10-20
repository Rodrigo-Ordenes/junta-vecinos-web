/* Asistente del inicio: responde sin recargar la página. Si algo falla, el
   formulario se envía de forma normal y el servidor muestra la respuesta. */
(function () {
  var caja = document.querySelector("[data-asistente]");
  if (!caja) return;

  var formulario = caja.querySelector("form");
  var campo = formulario.querySelector("input[name='q']");
  var conversacion = caja.querySelector("[data-asistente-conversacion]");
  var api = caja.getAttribute("data-api");

  function burbuja(clase) {
    var div = document.createElement("div");
    div.className = "asistente-burbuja " + clase;
    conversacion.appendChild(div);
    return div;
  }

  function mostrarRespuesta(div, datos) {
    div.textContent = "";
    div.classList.remove("asistente-escribiendo");
    if (datos.tipo === "emergencia") div.classList.add("asistente-alerta");

    var mensaje = document.createElement("p");
    mensaje.textContent = datos.mensaje;
    div.appendChild(mensaje);

    if (datos.destinos && datos.destinos.length) {
      var lista = document.createElement("div");
      lista.className = "asistente-destinos";
      datos.destinos.forEach(function (d) {
        var enlace = document.createElement("a");
        enlace.className = "asistente-destino";
        enlace.href = d.url;
        var textos = document.createElement("span");
        var titulo = document.createElement("strong");
        titulo.textContent = d.titulo;
        var descripcion = document.createElement("small");
        descripcion.textContent = d.descripcion;
        textos.appendChild(titulo);
        textos.appendChild(descripcion);
        var flecha = document.createElement("span");
        flecha.className = "asistente-flecha";
        flecha.setAttribute("aria-hidden", "true");
        flecha.textContent = "→";
        enlace.appendChild(textos);
        enlace.appendChild(flecha);
        lista.appendChild(enlace);
      });
      div.appendChild(lista);
    }
  }

  function preguntar(texto) {
    texto = (texto || "").trim();
    if (!texto) {
      campo.focus();
      return;
    }
    burbuja("asistente-vecino").textContent = texto;
    var respuesta = burbuja("asistente-respuesta asistente-escribiendo");
    respuesta.textContent = "Buscando…";
    campo.value = "";
    respuesta.scrollIntoView({ block: "nearest", behavior: "smooth" });

    fetch(api + "?q=" + encodeURIComponent(texto), { headers: { Accept: "application/json" } })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (datos) {
        mostrarRespuesta(respuesta, datos);
        respuesta.scrollIntoView({ block: "nearest", behavior: "smooth" });
      })
      .catch(function () {
        window.location.href = formulario.getAttribute("action").split("#")[0] +
          "?q=" + encodeURIComponent(texto) + "#asistente";
      });
  }

  formulario.addEventListener("submit", function (evento) {
    evento.preventDefault();
    preguntar(campo.value);
  });

  caja.addEventListener("click", function (evento) {
    var ejemplo = evento.target.closest("[data-pregunta]");
    if (!ejemplo) return;
    evento.preventDefault();
    preguntar(ejemplo.getAttribute("data-pregunta"));
  });
})();
