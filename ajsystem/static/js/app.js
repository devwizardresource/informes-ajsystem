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

  // «Imprimir / Guardar como PDF» de la plantilla
  document.querySelectorAll("[data-print]").forEach(function (b) {
    b.addEventListener("click", function () { window.print(); });
  });

  // Permite verificar lo escrito; las contraseñas guardadas nunca se pueden recuperar.
  document.querySelectorAll("input[data-reveal-password]").forEach(function (input) {
    var wrap = document.createElement("div");
    wrap.className = "password-field";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    var button = document.createElement("button");
    button.type = "button";
    button.className = "password-reveal";
    button.textContent = "Mostrar";
    button.setAttribute("aria-label", "Mostrar contraseña escrita");
    button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", function () {
      var visible = input.type === "password";
      input.type = visible ? "text" : "password";
      button.textContent = visible ? "Ocultar" : "Mostrar";
      button.setAttribute("aria-label", (visible ? "Ocultar" : "Mostrar") + " contraseña escrita");
      button.setAttribute("aria-pressed", visible ? "true" : "false");
      input.focus();
    });
    wrap.appendChild(button);
  });

  // Áreas de texto que crecen con su contenido (celdas del formato de informe)
  function grow(t) { t.style.height = "auto"; t.style.height = (t.scrollHeight + 2) + "px"; }
  document.querySelectorAll("textarea[data-autogrow]").forEach(function (t) {
    grow(t);
    t.addEventListener("input", function () { grow(t); });
  });
  window.addEventListener("resize", function () {
    document.querySelectorAll("textarea[data-autogrow]").forEach(grow);
  });

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
      // Preview opens a separate tab with the current values. Do not lock the
      // editor or mark unsaved changes as persisted while it is loading.
      if (submitter && submitter.hasAttribute("data-no-busy")) return;
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

  // Casilla «Seleccionar todos» (firma en lote): marca o desmarca las casillas con el mismo nombre
  document.querySelectorAll("input[data-check-all]").forEach(function (all) {
    var form = all.getAttribute("form");
    var boxes = document.querySelectorAll('input[type=checkbox][name="' + all.getAttribute("data-check-all") +
                                          '"][form="' + form + '"]');
    all.addEventListener("change", function () {
      boxes.forEach(function (b) { b.checked = all.checked; });
    });
    boxes.forEach(function (b) {
      b.addEventListener("change", function () {
        var n = 0;
        boxes.forEach(function (o) { if (o.checked) n++; });
        all.checked = n === boxes.length;
        all.indeterminate = n > 0 && n < boxes.length;
      });
    });
  });

  // Gráficas: información emergente para cualquier marca con data-tip (texto plano, nunca HTML)
  var tip = null;
  function showTip(target, x, y) {
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "chart-tip";
      tip.setAttribute("role", "tooltip");
      document.body.appendChild(tip);
    }
    tip.textContent = target.getAttribute("data-tip");
    tip.hidden = false;
    var w = tip.offsetWidth, h = tip.offsetHeight;
    var left = Math.min(Math.max(8, x - w / 2), window.innerWidth - w - 8);
    var top = y - h - 12 < 8 ? y + 18 : y - h - 12;
    tip.style.left = (left + window.scrollX) + "px";
    tip.style.top = (top + window.scrollY) + "px";
  }
  function hideTip() { if (tip) tip.hidden = true; }
  document.addEventListener("pointerover", function (e) {
    var target = e.target.closest && e.target.closest("[data-tip]");
    if (target) showTip(target, e.clientX, e.clientY); else hideTip();
  });
  document.addEventListener("pointermove", function (e) {
    var target = e.target.closest && e.target.closest("[data-tip]");
    if (target && tip && !tip.hidden) showTip(target, e.clientX, e.clientY);
  });
  document.addEventListener("scroll", hideTip, { passive: true });

  // Calendarios anchos: en pantallas pequeñas se muestran desde la fecha más reciente
  document.querySelectorAll("[data-scroll-end]").forEach(function (box) { box.scrollLeft = box.scrollWidth; });

  // Tablas ordenables: clic en el encabezado (data-sort="num" usa data-v de la celda)
  document.querySelectorAll("table[data-sortable]").forEach(function (table) {
    table.querySelectorAll("th[data-sort]").forEach(function (th, index) {
      th.tabIndex = 0;
      th.setAttribute("aria-sort", "none");
      function sort() {
        var asc = th.getAttribute("aria-sort") !== "ascending";
        var numeric = th.getAttribute("data-sort") === "num";
        var body = table.tBodies[0];
        var rows = Array.prototype.slice.call(body.rows).filter(function (r) { return r.cells.length > index; });
        rows.sort(function (a, b) {
          var ca = a.cells[index], cb = b.cells[index];
          var va = numeric ? parseFloat(ca.getAttribute("data-v") || ca.textContent) : ca.textContent.trim().toLowerCase();
          var vb = numeric ? parseFloat(cb.getAttribute("data-v") || cb.textContent) : cb.textContent.trim().toLowerCase();
          if (numeric) { va = isNaN(va) ? -Infinity : va; vb = isNaN(vb) ? -Infinity : vb; }
          return (va > vb ? 1 : va < vb ? -1 : 0) * (asc ? 1 : -1);
        });
        rows.forEach(function (r) { body.appendChild(r); });
        table.querySelectorAll("th[data-sort]").forEach(function (o) { o.setAttribute("aria-sort", "none"); });
        th.setAttribute("aria-sort", asc ? "ascending" : "descending");
      }
      th.addEventListener("click", sort);
      th.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); } });
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
