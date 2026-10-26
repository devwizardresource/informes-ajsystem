/* AJ System — informe: fotos del equipo, reconocimiento de texto (OCR) y firmas.
 *
 * Flujo de la foto: se reduce en el navegador (subida rápida también con datos móviles), se anexa
 * al informe en el servidor y se reconoce el texto con Tesseract.js (alojado en /static/vendor,
 * sin servicios externos). El servidor traduce el texto a campos del formato y aquí solo se
 * rellenan los campos vacíos; nada se guarda hasta pulsar «Guardar cambios».
 */
(function () {
  "use strict";

  var form = document.getElementById("report-form");
  if (!form) return;
  var CSRF = form.getAttribute("data-csrf");
  var UPLOAD_URL = form.getAttribute("data-upload-url");
  var AUTOFILL_URL = form.getAttribute("data-autofill-url");
  var OCR_BASE = form.getAttribute("data-ocr-base");
  var MAX_SIDE = 2000;
  var photoStatus = document.querySelector("[data-photo-status]");

  function markDirty() { form.dispatchEvent(new Event("input", { bubbles: true })); }

  function post(url, data) {
    var fd = new FormData();
    fd.append("csrf_token", CSRF);
    Object.keys(data).forEach(function (k) { fd.append(k, data[k]); });
    return fetch(url, { method: "POST", body: fd, credentials: "same-origin",
                        headers: { "X-CSRF-Token": CSRF, "Accept": "application/json" } })
      .then(function (r) {
        return r.text().then(function (raw) {
          var body = {};
          try { body = raw ? JSON.parse(raw) : {}; } catch (_) {}
          if (!r.ok) {
            throw new Error(body.error || ("No se pudo completar la operación (HTTP " + r.status + ")."));
          }
          return body;
        });
      });
  }

  // ---------------------------------------------------------------- imágenes
  function loadImage(file) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () { resolve({ img: img, url: url }); };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error("No se pudo leer la imagen.")); };
      img.src = url;
    });
  }

  function drawScaled(img, maxSide, filter) {
    var scale = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight));
    var c = document.createElement("canvas");
    c.width = Math.round(img.naturalWidth * scale);
    c.height = Math.round(img.naturalHeight * scale);
    var ctx = c.getContext("2d");
    if (filter) ctx.filter = filter;
    ctx.drawImage(img, 0, 0, c.width, c.height);
    return c;
  }

  function toBlob(canvas, type, quality) {
    return new Promise(function (resolve, reject) {
      canvas.toBlob(function (blob) {
        if (blob) resolve(blob);
        else reject(new Error("El navegador no pudo preparar la imagen."));
      }, type, quality);
    });
  }

  // ---------------------------------------------------------------- estado en pantalla
  var panel = document.getElementById("ocr-status");
  function status(title, msg, pct) {
    if (!panel) return;
    panel.hidden = false;
    panel.querySelector("[data-ocr-title]").textContent = title;
    panel.querySelector("[data-ocr-msg]").textContent = msg || "";
    panel.querySelector("[data-ocr-bar]").style.width = (pct == null ? 0 : pct) + "%";
  }
  function photoMessage(msg) {
    if (photoStatus) photoStatus.textContent = msg;
  }
  function showText(text) {
    var d = panel.querySelector("[data-ocr-details]");
    panel.querySelector("[data-ocr-text]").textContent = text;
    d.hidden = !text;
  }

  function refreshChoices() {
    form.querySelectorAll("[data-choice-group]").forEach(function (group) {
      var selected = group.querySelector("input[type=radio]:checked"), output = group.querySelector(".choice-current");
      if (output) output.textContent = selected ? "Seleccionado: " + selected.value : "Sin seleccionar";
    });
  }

  function refreshConditionalFields() {
    var toggle = form.querySelector('[name="observaciones_habilitadas"]');
    var row = form.querySelector('[data-conditional-field="observaciones"]');
    if (toggle && row) row.hidden = !toggle.checked;
  }

  form.addEventListener("change", function () { refreshChoices(); refreshConditionalFields(); });
  refreshChoices();
  refreshConditionalFields();

  // ---------------------------------------------------------------- OCR (carga diferida)
  var workerPromise = null;
  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = src; s.onload = resolve;
      s.onerror = function () { reject(new Error("No se pudo cargar el motor de reconocimiento.")); };
      document.head.appendChild(s);
    });
  }
  function getWorker() {
    if (!workerPromise) {
      workerPromise = loadScript(OCR_BASE + "tesseract.min.js").then(function () {
        return window.Tesseract.createWorker(["spa", "eng"], 1, {
          workerPath: OCR_BASE + "worker.min.js",
          corePath: OCR_BASE + "core",
          langPath: OCR_BASE + "lang",
          workerBlobURL: false,
          gzip: true,
          logger: function (m) {
            if (m.status === "recognizing text") status("Reconociendo texto…", "Esto puede tardar unos segundos.", Math.round(60 + m.progress * 40));
            else if (m.progress != null) status("Preparando el reconocimiento…", "La primera vez se descargan los datos del idioma (≈5 MB).", Math.round(30 + m.progress * 30));
          }
        });
      });
      workerPromise.catch(function () { workerPromise = null; });
    }
    return workerPromise;
  }

  // ---------------------------------------------------------------- autocompletado
  function fieldIsEmpty(cell) {
    var radios = cell.querySelectorAll("input[type=radio]");
    if (radios.length) return !Array.prototype.some.call(radios, function (r) { return r.checked; });
    var input = cell.querySelector("input, textarea, select");
    return input && !input.value.trim();
  }
  function setField(cell, value) {
    var radios = cell.querySelectorAll("input[type=radio]");
    if (radios.length) {
      Array.prototype.forEach.call(radios, function (r) { r.checked = (r.value === value); });
      var selected = Array.prototype.find.call(radios, function (r) { return r.checked; });
      if (selected) selected.dispatchEvent(new Event("change", { bubbles: true }));
    } else {
      var input = cell.querySelector("input, textarea, select");
      input.value = value;
      if (input.hasAttribute("data-autogrow")) input.dispatchEvent(new Event("input", { bubbles: true }));
    }
    cell.classList.add("autofilled");
  }
  function applySuggestions(result) {
    var filled = [], kept = [];
    Object.keys(result.campos || {}).forEach(function (id) {
      var cell = form.querySelector('.val[data-field="' + id + '"]');
      if (!cell) return;
      var label = (result.etiquetas || {})[id] || id;
      if (fieldIsEmpty(cell)) { setField(cell, result.campos[id]); filled.push(label); }
      else kept.push(label);
    });
    if (filled.length) markDirty();
    return { filled: filled, kept: kept };
  }

  // ---------------------------------------------------------------- galería
  function addToGallery(info, name) {
    var gallery = document.querySelector("[data-gallery]");
    if (!gallery) return;
    var empty = gallery.querySelector("[data-gallery-empty]");
    if (empty) empty.remove();
    var fig = document.createElement("figure");
    var a = document.createElement("a");
    a.href = info.url; a.target = "_blank"; a.rel = "noopener";
    var img = document.createElement("img");
    img.src = info.thumb; img.alt = "Foto del equipo"; img.width = 180; img.height = 130;
    a.appendChild(img);
    var cap = document.createElement("figcaption");
    cap.textContent = "Foto anexada · " + (name || "") + " (recargue para gestionarla)";
    fig.appendChild(a); fig.appendChild(cap);
    gallery.appendChild(fig);
  }

  // ---------------------------------------------------------------- flujo de una foto
  function processPhoto(file) {
    var loaded;
    status("Preparando la foto…", file.name, 5);
    photoMessage("Preparando «" + file.name + "»…");
    return loadImage(file).then(function (r) {
      loaded = r;
      return toBlob(drawScaled(r.img, MAX_SIDE), "image/jpeg", 0.88);
    }).then(function (blob) {
      status("Anexando la foto al informe…", "", 15);
      photoMessage("Anexando «" + file.name + "» al informe…");
      return post(UPLOAD_URL, { tipo: "FOTO", archivo: new File([blob], "foto.jpg", { type: "image/jpeg" }) });
    }).then(function (info) {
      addToGallery(info, file.name);
      status("Foto anexada. Cargando el reconocimiento de texto…", "", 25);
      photoMessage("Foto anexada. Intentando reconocer el texto…");
      // OCR es opcional: si el navegador no puede cargar Tesseract, la foto
      // ya quedó guardada y no debe presentarse como un fallo de la subida.
      return Promise.resolve().then(function () {
        // Escala de grises y contraste: mejora la lectura de pantallas y etiquetas.
        var ocrCanvas = drawScaled(loaded.img, MAX_SIDE, "grayscale(1) contrast(1.35)");
        return getWorker().then(function (worker) {
          return worker.recognize(ocrCanvas, { rotateAuto: true }).then(function (res) {
            var text = (res.data && res.data.text) || "";
            if (text.trim().length >= 6) return res;
            // A second, high-contrast pass helps with small labels and low-light photos.
            status("Mejorando la lectura…", "Probando contraste alto para recuperar texto pequeño.", 65);
            var binary = drawScaled(loaded.img, MAX_SIDE, "grayscale(1) contrast(2.1)");
            return worker.recognize(binary, { rotateAuto: true });
          });
        });
      }).then(function (res) {
        var text = (res.data && res.data.text) || "";
        showText(text);
        if (!text.trim()) {
          status("Foto anexada", "No se reconoció texto. La foto sí quedó guardada.", 100);
          photoMessage("Foto anexada correctamente. No se encontró texto para autocompletar.");
          return;
        }
        return post(AUTOFILL_URL, { texto: text, adjunto: info.id }).then(function (result) {
          var r = applySuggestions(result);
          var msg = r.filled.length
            ? "Campos completados: " + r.filled.join(", ") + ". Revíselos y pulse «Guardar cambios»."
            : "No se encontraron datos nuevos para los campos vacíos.";
          if (r.kept.length) msg += " Se respetaron los valores ya escritos en: " + r.kept.join(", ") + ".";
          status("Foto anexada y texto reconocido", msg, 100);
          photoMessage("Foto anexada y campos sugeridos. Revise y guarde los cambios.");
        });
      }).catch(function (err) {
        status("Foto anexada", "No se pudo reconocer el texto, pero la foto sí quedó guardada.", 100);
        photoMessage("Foto anexada correctamente. El reconocimiento de texto no está disponible en este navegador.");
        if (window.console && console.warn) console.warn("OCR no disponible:", err);
      });
    }).catch(function (err) {
      status("No se pudo anexar la foto", err.message, 0);
      photoMessage("No se pudo anexar la foto: " + err.message);
    }).then(function () {
      if (loaded) URL.revokeObjectURL(loaded.url);
    });
  }

  document.querySelectorAll("[data-photo-input]").forEach(function (input) {
    input.addEventListener("change", function () {
      var files = Array.prototype.slice.call(input.files || []);
      input.value = "";
      files.reduce(function (p, f) { return p.then(function () { return processPhoto(f); }); }, Promise.resolve());
    });
  });

  // ---------------------------------------------------------------- «Limpiar formulario» (no guarda)
  var clearBtn = document.querySelector("[data-clear-form]");
  if (clearBtn) clearBtn.addEventListener("click", function () {
    if (!window.confirm("¿Borrar todos los datos escritos en la hoja? No se guarda nada hasta pulsar «Guardar cambios».")) return;
    form.querySelectorAll(".val input[type=text], .val textarea").forEach(function (i) {
      i.value = ""; i.dispatchEvent(new Event("input", { bubbles: true }));
    });
    form.querySelectorAll(".val input[type=radio]").forEach(function (r) { r.checked = false; });
    form.querySelectorAll('.val input[type="checkbox"]').forEach(function (c) { c.checked = false; });
    refreshChoices(); refreshConditionalFields();
    form.querySelectorAll(".val.autofilled").forEach(function (c) { c.classList.remove("autofilled"); });
    markDirty();
  });

  // ---------------------------------------------------------------- firmas
  document.querySelectorAll(".sig[data-kind]").forEach(function (sig) {
    var pad = sig.querySelector(".pad"), cv = pad.querySelector("canvas"), img = pad.querySelector("img"),
        file = sig.querySelector("input[type=file]"), state = sig.querySelector(".status");
    if (!cv) return;
    var ctx = cv.getContext("2d"), drawing = false, last = null, pending = null;
    function fit() {
      var r = pad.getBoundingClientRect(), d = window.devicePixelRatio || 1;
      if (!r.width) return;
      cv.width = r.width * d; cv.height = r.height * d; ctx.setTransform(d, 0, 0, d, 0, 0);
      ctx.lineWidth = 1.8; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = "#14213d";
    }
    function pos(e) { var r = cv.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; }
    pad.addEventListener("pointerdown", function (e) {
      if (!img.hidden) { img.hidden = true; ctx.clearRect(0, 0, cv.width, cv.height); }
      drawing = true; last = pos(e); pad.setPointerCapture(e.pointerId); sig.classList.add("has");
      ctx.beginPath(); ctx.arc(last.x, last.y, 0.9, 0, 7); ctx.fillStyle = "#14213d"; ctx.fill();
      pending = "canvas"; state.textContent = "Sin guardar";
    });
    pad.addEventListener("pointermove", function (e) {
      if (!drawing) return; var p = pos(e);
      ctx.beginPath(); ctx.moveTo(last.x, last.y); ctx.lineTo(p.x, p.y); ctx.stroke(); last = p;
    });
    ["pointerup", "pointercancel"].forEach(function (t) { pad.addEventListener(t, function () { drawing = false; }); });
    sig.querySelector("[data-act=clear]").addEventListener("click", function () {
      ctx.clearRect(0, 0, cv.width, cv.height); img.hidden = true; img.removeAttribute("src");
      file.value = ""; sig.classList.remove("has"); pending = null;
      state.textContent = "Dibuje la firma y pulse «Guardar firma»";
    });
    sig.querySelector("[data-act=load]").addEventListener("click", function () { file.click(); });
    file.addEventListener("change", function () {
      var f = file.files[0]; if (!f) return;
      ctx.clearRect(0, 0, cv.width, cv.height);
      img.src = URL.createObjectURL(f); img.hidden = false; sig.classList.add("has");
      pending = f; state.textContent = "Sin guardar";
    });
    sig.querySelector("[data-act=save]").addEventListener("click", function () {
      if (!pending) { state.textContent = "No hay una firma nueva que guardar"; return; }
      var signForm = document.getElementById("sign-form");
      if (pending === "profile" && signForm) { signForm.requestSubmit(); return; }
      state.textContent = "Guardando…";
      var blobPromise = pending === "canvas" ? toBlob(cv, "image/png") : Promise.resolve(pending);
      blobPromise.then(function (blob) {
        return post(UPLOAD_URL, { tipo: sig.getAttribute("data-kind"), archivo: new File([blob], "firma.png", { type: blob.type || "image/png" }) });
      }).then(function (info) {
        pending = null; state.textContent = "Firma guardada ✓";
        img.src = info.url; img.hidden = false; ctx.clearRect(0, 0, cv.width, cv.height);
      }).catch(function (err) { state.textContent = err.message; });
    });

    // Director con el informe cerrado: «Firmar» coloca la firma guardada y «Guardar cambios» la registra.
    var signForm = document.getElementById("sign-form");
    if (sig.getAttribute("data-kind") !== "FIRMA_RECIBE" || !signForm) { fit(); window.addEventListener("resize", fit); return; }
    var profileBtn = sig.querySelector("[data-act=profile]");
    var saveBtns = document.querySelectorAll("[data-sign-save]");
    function setReady(ready) { saveBtns.forEach(function (b) { b.disabled = !ready; }); }
    function useProfile() {
      if (!profileBtn) return;
      ctx.clearRect(0, 0, cv.width, cv.height); file.value = "";
      img.src = profileBtn.getAttribute("data-src"); img.hidden = false; sig.classList.add("has");
      pending = "profile"; state.textContent = "Firma colocada: pulse «Guardar cambios»";
      setReady(true);
      signForm.dispatchEvent(new Event("change", { bubbles: true }));
      sig.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    if (profileBtn) profileBtn.addEventListener("click", useProfile);
    document.querySelectorAll("[data-sign-profile]").forEach(function (b) { b.addEventListener("click", useProfile); });
    ["pointerdown"].forEach(function (t) { pad.addEventListener(t, function () { setReady(true); }); });
    file.addEventListener("change", function () { if (file.files[0]) setReady(true); });
    sig.querySelector("[data-act=clear]").addEventListener("click", function () { setReady(false); });
    signForm.addEventListener("submit", function (e) {
      if (pending === "profile" || !pending) return;  // el servidor copia la firma guardada del perfil
      e.preventDefault();  // firma dibujada o cargada: se sube y se recarga el informe
      state.textContent = "Guardando…";
      (pending === "canvas" ? toBlob(cv, "image/png") : Promise.resolve(pending)).then(function (blob) {
        return post(UPLOAD_URL, { tipo: "FIRMA_RECIBE", archivo: new File([blob], "firma.png", { type: blob.type || "image/png" }) });
      }).then(function () { window.location.reload(); })
        .catch(function (err) {
          state.textContent = err.message; signForm.dataset.submitting = "";
          saveBtns.forEach(function (b) { b.textContent = "Guardar cambios"; b.removeAttribute("aria-busy"); });
          setReady(true);
        });
    });
    setReady(false);
    fit(); window.addEventListener("resize", fit);
  });
})();
