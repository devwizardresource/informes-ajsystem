/* AJ System — interacciones del cliente.
 * Solo mejora la experiencia: toda validación y autorización real ocurre en el servidor.
 */
(function () {
  "use strict";

  // Menú en móviles
  var toggle = document.querySelector(".nav-toggle");
  var nav = document.getElementById("nav-principal");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  // Formularios con edición pendiente: aviso al abandonar la página sin guardar
  var dirty = false;
  document.querySelectorAll("form[data-dirty-guard]").forEach(function (form) {
    form.addEventListener("input", function () { dirty = true; });
    form.addEventListener("change", function () { dirty = true; });
  });
  window.addEventListener("beforeunload", function (e) {
    if (dirty) { e.preventDefault(); e.returnValue = ""; }
  });

  // Confirmación de operaciones críticas + prevención de doble envío + indicador de proceso
  document.querySelectorAll("form").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      var submitter = e.submitter || form.querySelector("[type=submit]");
      var question = submitter && submitter.getAttribute("data-confirm");
      if (question && !window.confirm(question)) {
        e.preventDefault();
        return;
      }
      if (!form.hasAttribute("data-busy")) { dirty = false; return; }
      if (form.dataset.submitting === "1") {  // segundo clic: se ignora
        e.preventDefault();
        return;
      }
      form.dataset.submitting = "1";
      dirty = false;
      // El botón deshabilitado no se envía: se conserva su nombre/valor en un campo oculto.
      if (submitter && submitter.name) {
        var hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = submitter.name;
        hidden.value = submitter.value;
        form.appendChild(hidden);
      }
      form.querySelectorAll("[type=submit]").forEach(function (btn) {
        btn.disabled = true;
        btn.setAttribute("aria-busy", "true");
      });
      if (submitter) { submitter.textContent = "Procesando…"; }
      form.setAttribute("aria-busy", "true");
    });
  });

  // Si el navegador restaura la página desde caché (botón Atrás), se reactivan los formularios
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    document.querySelectorAll("form[data-busy]").forEach(function (form) {
      form.dataset.submitting = "";
      form.querySelectorAll("[type=submit]").forEach(function (btn) { btn.disabled = false; btn.removeAttribute("aria-busy"); });
    });
  });
})();
