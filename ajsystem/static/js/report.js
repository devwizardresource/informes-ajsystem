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

  function markDirty() { form.dispatchEvent(new Event("input", { bubbles: true })); }

  function post(url, data) {
    var fd = new FormData();
    fd.append("csrf_token", CSRF);
    Object.keys(data).forEach(function (k) { fd.append(k, data[k]); });
    return fetch(url, { method: "POST", body: fd, credentials: "same-origin",
                        headers: { "X-CSRF-Token": CSRF, "Accept": "application/json" } })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          if (!r.ok) throw new Error(body.error || ("Error " + r.status + " del servidor"));
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
    return new Promise(function (resolve) { canvas.toBlob(resolve, type, quality); });
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
  function showText(text) {
    var d = panel.querySelector("[data-ocr-details]");
    panel.querySelector("[data-ocr-text]").textContent = text;
    d.hidden = !text;
  }

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
    cap.textContent = "Nueva foto · " + (name || "") + " (recargue para gestionarla)";
    fig.appendChild(a); fig.appendChild(cap);
    gallery.appendChild(fig);
  }

  // ---------------------------------------------------------------- flujo de una foto
  function processPhoto(file) {
    var loaded;
    status("Preparando la foto…", file.name, 5);
    return loadImage(file).then(function (r) {
      loaded = r;
      return toBlob(drawScaled(r.img, MAX_SIDE), "image/jpeg", 0.88);
    }).then(function (blob) {
      status("Anexando la foto al informe…", "", 15);
      return post(UPLOAD_URL, { tipo: "FOTO", archivo: new File([blob], "foto.jpg", { type: "image/jpeg" }) });
    }).then(function (info) {
      addToGallery(info, file.name);
      status("Foto anexada. Cargando el reconocimiento de texto…", "", 25);
      // Escala de grises y contraste: mejora la lectura de pantallas y etiquetas.
      var ocrCanvas = drawScaled(loaded.img, MAX_SIDE, "grayscale(1) contrast(1.35)");
      return getWorker().then(function (worker) { return worker.recognize(ocrCanvas); }).then(function (res) {
        var text = (res.data && res.data.text) || "";
        showText(text);
        if (!text.trim()) {
          status("Foto anexada", "No se reconoció texto. Intente con una foto más cercana, nítida y sin reflejos.", 100);
          return;
        }
        return post(AUTOFILL_URL, { texto: text, adjunto: info.id }).then(function (result) {
          var r = applySuggestions(result);
          var msg = r.filled.length
            ? "Campos completados: " + r.filled.join(", ") + ". Revíselos y pulse «Guardar cambios»."
            : "No se encontraron datos nuevos para los campos vacíos.";
          if (r.kept.length) msg += " Se respetaron los valores ya escritos en: " + r.kept.join(", ") + ".";
          status("Foto anexada y texto reconocido", msg, 100);
        });
      });
    }).catch(function (err) {
      status("No se pudo procesar la foto", err.message, 0);
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
      state.textContent = "Guardando…";
      var blobPromise = pending === "canvas" ? toBlob(cv, "image/png") : Promise.resolve(pending);
      blobPromise.then(function (blob) {
        return post(UPLOAD_URL, { tipo: sig.getAttribute("data-kind"), archivo: new File([blob], "firma.png", { type: blob.type || "image/png" }) });
      }).then(function (info) {
        pending = null; state.textContent = "Firma guardada ✓";
        img.src = info.url; img.hidden = false; ctx.clearRect(0, 0, cv.width, cv.height);
      }).catch(function (err) { state.textContent = err.message; });
    });
    fit(); window.addEventListener("resize", fit);
  });
})();
