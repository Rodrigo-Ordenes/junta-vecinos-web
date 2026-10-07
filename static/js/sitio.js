/* Interacciones básicas del sitio (menú, desplegables y avisos) y los ajustes
   de accesibilidad pensados para vecinas y vecinos mayores. No usa librerías. */
(function () {
  var raiz = document.documentElement;
  var CLAVES = { texto: "jv-texto-grande", contraste: "jv-alto-contraste", fuente: "jv-escala-fuente" };
  var NIVELES_FUENTE = {
    "-1": "14.5px",
    "0": "16px",
    "1": "18px",
    "2": "20.5px",
    "3": "23px"
  };
  var MIN_NIVEL = -1;
  var MAX_NIVEL = 3;

  function leer(clave) {
    try { return window.localStorage.getItem(clave) === "1"; } catch (e) { return false; }
  }

  function guardar(clave, valor) {
    try { window.localStorage.setItem(clave, valor ? "1" : "0"); } catch (e) { /* sin almacenamiento */ }
  }

  function leerNivelFuente() {
    try {
      var val = window.localStorage.getItem(CLAVES.fuente);
      if (val !== null) {
        var num = parseInt(val, 10);
        if (!isNaN(num) && num >= MIN_NIVEL && num <= MAX_NIVEL) return num;
      }
      if (leer(CLAVES.texto)) return 2;
    } catch (e) {}
    return 0;
  }

  function guardarNivelFuente(nivel) {
    try { window.localStorage.setItem(CLAVES.fuente, String(nivel)); } catch (e) {}
  }

  function aplicarPreferencias() {
    var nivel = leerNivelFuente();
    raiz.style.fontSize = NIVELES_FUENTE[String(nivel)] || "16px";
    raiz.classList.toggle("texto-grande", nivel >= 2);
    raiz.classList.toggle("alto-contraste", leer(CLAVES.contraste));
  }

  function cerrarDesplegables(excepto) {
    var abiertos = document.querySelectorAll(".dropdown-menu.show");
    for (var i = 0; i < abiertos.length; i++) {
      if (abiertos[i] !== excepto) abiertos[i].classList.remove("show");
    }
  }

  document.addEventListener("click", function (evento) {
    var objetivo = evento.target;

    // Accesibilidad
    var botonAccesible = objetivo.closest("[data-accion]");
    if (botonAccesible) {
      var accion = botonAccesible.getAttribute("data-accion");
      var nivelActual = leerNivelFuente();
      if (accion === "texto-aumentar") {
        if (nivelActual < MAX_NIVEL) guardarNivelFuente(nivelActual + 1);
      } else if (accion === "texto-reducir") {
        if (nivelActual > MIN_NIVEL) guardarNivelFuente(nivelActual - 1);
      } else if (accion === "texto-normal" || accion === "reiniciar") {
        guardarNivelFuente(0);
        guardar(CLAVES.texto, false);
      } else if (accion === "texto-grande") {
        guardarNivelFuente(nivelActual === 2 ? 0 : 2);
      } else if (accion === "alto-contraste") {
        guardar(CLAVES.contraste, !leer(CLAVES.contraste));
      }
      aplicarPreferencias();
      return;
    }

    // Menú principal en el teléfono
    var boton = objetivo.closest("[data-bs-toggle='collapse']");
    if (boton) {
      evento.preventDefault();
      var destino = document.querySelector(boton.getAttribute("data-bs-target"));
      if (destino) destino.classList.toggle("show");
      return;
    }

    // Menús desplegables
    var enlace = objetivo.closest("[data-bs-toggle='dropdown']");
    if (enlace) {
      evento.preventDefault();
      var menu = enlace.parentNode.querySelector(".dropdown-menu");
      if (menu) {
        var estaAbierto = menu.classList.contains("show");
        cerrarDesplegables(null);
        if (!estaAbierto) menu.classList.add("show");
      }
      return;
    }

    // Cerrar avisos
    var cerrar = objetivo.closest("[data-bs-dismiss='alert']");
    if (cerrar) {
      var aviso = cerrar.closest(".alert");
      if (aviso && aviso.parentNode) aviso.parentNode.removeChild(aviso);
      return;
    }

    // Modales (abrir)
    var btnAbrirModal = objetivo.closest("[data-bs-toggle='modal']");
    if (btnAbrirModal) {
      evento.preventDefault();
      var idModal = btnAbrirModal.getAttribute("data-bs-target");
      if (idModal) {
        var modal = document.querySelector(idModal);
        if (modal) {
          modal.classList.add("show");
          modal.style.display = "block";
          modal.removeAttribute("aria-hidden");
          modal.setAttribute("aria-modal", "true");
          document.body.classList.add("modal-open");
          var tel = modal.querySelector("input, button");
          if (tel) tel.focus();
        }
      }
      return;
    }

    // Modales (cerrar con botón dismiss o backdrop)
    var btnCerrarModal = objetivo.closest("[data-bs-dismiss='modal']");
    if (btnCerrarModal) {
      evento.preventDefault();
      var modalCerrar = btnCerrarModal.closest(".modal");
      if (modalCerrar) {
        modalCerrar.classList.remove("show");
        modalCerrar.style.display = "none";
        modalCerrar.setAttribute("aria-hidden", "true");
        modalCerrar.removeAttribute("aria-modal");
        document.body.classList.remove("modal-open");
      }
      return;
    }

    if (objetivo.classList.contains("modal")) {
      objetivo.classList.remove("show");
      objetivo.style.display = "none";
      objetivo.setAttribute("aria-hidden", "true");
      objetivo.removeAttribute("aria-modal");
      document.body.classList.remove("modal-open");
      return;
    }

    if (!objetivo.closest(".dropdown")) cerrarDesplegables(null);
  });

  document.addEventListener("keydown", function (evento) {
    if (evento.key === "Escape") {
      cerrarDesplegables(null);
      var modales = document.querySelectorAll(".modal.show");
      for (var i = 0; i < modales.length; i++) {
        modales[i].classList.remove("show");
        modales[i].style.display = "none";
        modales[i].setAttribute("aria-hidden", "true");
        modales[i].removeAttribute("aria-modal");
      }
      document.body.classList.remove("modal-open");
    }
  });

  // Formateo automático uniforme para prefijo chileno (+569) en todos los campos telefónicos
  function formatearTelInput(input) {
    if (!input) return;
    if (!input.placeholder || input.placeholder === "") {
      input.placeholder = "+569XXXXXXXX";
    }
    input.addEventListener("focus", function () {
      if (!this.value || this.value.trim() === "") {
        this.value = "+569";
      }
    });
    input.addEventListener("input", function () {
      var val = this.value;
      if (!val) return;
      if (!val.startsWith("+569")) {
        var soloNums = val.replace(/[^\d]/g, "");
        if (soloNums.startsWith("569")) {
          this.value = "+" + soloNums;
        } else if (soloNums.startsWith("9")) {
          this.value = "+56" + soloNums;
        } else if (soloNums.length > 0) {
          this.value = "+569" + soloNums;
        }
      }
    });
    input.addEventListener("blur", function () {
      if (this.value.trim() === "+569" && !this.required) {
        this.value = "";
      }
    });
  }

  function inicializarTelefonos() {
    var selectores = 'input[type="tel"], input[name="telefono"], input[name="whatsapp"], input[id="telefono"], input[id="whatsapp"]';
    var telInputs = document.querySelectorAll(selectores);
    for (var i = 0; i < telInputs.length; i++) {
      formatearTelInput(telInputs[i]);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", inicializarTelefonos);
  } else {
    inicializarTelefonos();
  }

  aplicarPreferencias();
})();

  // Protección anti-descarga e impresión en visor de documentos
  var visor = document.querySelector("[data-visor-protegido]");
  if (visor) {
    // Inhabilitar clic derecho
    visor.addEventListener("contextmenu", function (e) {
      e.preventDefault();
      return false;
    });

    // Inhabilitar combinaciones de guardado e impresión (Ctrl+S, Ctrl+P, Cmd+S, Cmd+P)
    window.addEventListener("keydown", function (e) {
      if ((e.ctrlKey || e.metaKey) && (e.key === "p" || e.key === "P" || e.key === "s" || e.key === "S")) {
        e.preventDefault();
        return false;
      }
    });
  }
