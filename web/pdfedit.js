// PrintBridge - PDF editor: pages ghumana, hatana, nikalna, jodna, khiskana, watermark, sign
(() => {
  const PB = window.PB;
  if (!PB) return;
  const { $, esc } = PB;
  const VERSION = "2.6";
  const ext = f => (f.name.split(".").pop() || "").toLowerCase();
  const IMG = ["jpg", "jpeg", "png", "bmp", "gif", "webp"];

  // ---- haal (state)
  // sources: [{ bytes, doc (pdf.js) }]   pages: [{ s, i, rot (0..3 extra), base (pdf ka /Rotate), w, h, thumb, sigs: [] }]
  let E = null;
  const sel = new Set();

  async function loadPdfLib() {
    if (window.PDFLib) return window.PDFLib;
    await new Promise((res, rej) => { const s = document.createElement("script"); s.src = "/pdf-lib.min.js"; s.onload = res; s.onerror = rej; document.head.appendChild(s); });
    return window.PDFLib;
  }
  const emsg = (t, m) => { const el = $("peMsg"); el.className = "msg " + t; el.textContent = m; if (t !== "err") setTimeout(() => { if (el.textContent === m) el.className = "msg"; }, 2500); };

  async function addSource(bytes) {
    const lib = await PB.loadPdfJs();
    const doc = await lib.getDocument({ data: bytes.slice(0) }).promise;
    const s = E.sources.push({ bytes, doc }) - 1, out = [];
    for (let i = 0; i < doc.numPages; i++) {
      const pg = await doc.getPage(i + 1), vp = pg.getViewport({ scale: 1, rotation: 0 });
      out.push({ s, i, rot: 0, base: pg.rotate || 0, w: vp.width, h: vp.height, thumb: "", sigs: [] });
    }
    return out;
  }
  async function imageToPdfBytes(file) {
    const { PDFDocument } = await loadPdfLib();
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" }).catch(() => createImageBitmap(file));
    const sc = Math.min(1, 2400 / Math.max(bmp.width, bmp.height)), c = document.createElement("canvas");
    c.width = Math.round(bmp.width * sc); c.height = Math.round(bmp.height * sc);
    const x = c.getContext("2d"); x.fillStyle = "#fff"; x.fillRect(0, 0, c.width, c.height); x.drawImage(bmp, 0, 0, c.width, c.height);
    const blob = await new Promise(r => c.toBlob(r, "image/jpeg", 0.88));
    const doc = await PDFDocument.create(), img = await doc.embedJpg(new Uint8Array(await blob.arrayBuffer()));
    const land = c.width > c.height, W = land ? 842 : 595, H = land ? 595 : 842, s = Math.min((W - 30) / c.width, (H - 30) / c.height);
    doc.addPage([W, H]).drawImage(img, { x: (W - c.width * s) / 2, y: (H - c.height * s) / 2, width: c.width * s, height: c.height * s });
    return new Uint8Array(await doc.save());
  }
  const R = p => ((p.base + p.rot * 90) % 360 + 360) % 360;   // dikhne wala ghumav

  async function renderPage(p, width) {
    const pg = await E.sources[p.s].doc.getPage(p.i + 1);
    const vp1 = pg.getViewport({ scale: 1, rotation: R(p) }), vp = pg.getViewport({ scale: width / vp1.width, rotation: R(p) });
    const c = document.createElement("canvas"); c.width = Math.round(vp.width); c.height = Math.round(vp.height);
    const x = c.getContext("2d"); x.fillStyle = "#fff"; x.fillRect(0, 0, c.width, c.height);
    await pg.render({ canvasContext: x, viewport: vp }).promise;
    return c;
  }
  // Watermark aur sign bhi chhoti tasveer mein dikhao (taaki pata chale kya banega)
  function overlay(c, p) {
    const x = c.getContext("2d"), W = c.width, H = c.height;
    for (const s of p.sigs) if (s.img) x.drawImage(s.img, s.fx * W, s.fy * H, s.fw * W, s.fw * W * s.ar);
    if (E.wm && E.wm.text) {
      x.save(); x.globalAlpha = E.wm.op; x.fillStyle = E.wm.color; x.translate(W / 2, H / 2); x.rotate(-Math.PI / 4);
      x.font = `bold ${Math.min(W, H) * E.wm.size / 100}px sans-serif`; x.textAlign = "center"; x.textBaseline = "middle";
      x.fillText(E.wm.text, 0, 0); x.restore();
    }
    return c;
  }
  let thumbTok = 0;
  async function renderGrid() {
    const tok = ++thumbTok;
    $("peGrid").innerHTML = E.pages.map((p, k) => `<div class="peP${sel.has(k) ? " on" : ""}" data-k="${k}"><div class="peImg"><img alt=""></div><span class="tick">✔</span><span class="no">${k + 1}</span></div>`).join("");
    updateBar();
    for (let k = 0; k < E.pages.length; k++) {
      const p = E.pages[k];
      if (!p.thumb) { const c = await renderPage(p, 220); if (tok !== thumbTok) return; p.thumb = c; }
      const t = document.createElement("canvas"); t.width = p.thumb.width; t.height = p.thumb.height; t.getContext("2d").drawImage(p.thumb, 0, 0);
      const img = $("peGrid").querySelector(`[data-k="${k}"] img`); if (img) img.src = overlay(t, p).toDataURL("image/jpeg", 0.75);
      if (k % 4 === 3) await new Promise(r => setTimeout(r, 0));
    }
  }
  function updateBar() {
    const n = sel.size;
    $("peSel").textContent = n ? `${n} of ${E.pages.length} selected` : `${E.pages.length} pages · tap pages to select`;
    ["peRot", "peDel", "peKeep", "peLeft", "peRight"].forEach(id => $(id).disabled = !n);
    $("peRight").disabled = !n || Math.max(...sel) >= E.pages.length - 1;
    $("peLeft").disabled = !n || Math.min(...sel) <= 0;
    $("peSave").disabled = !E.pages.length;
  }
  const changed = () => { E.dirty = true; };

  // ---- kholna / band karna
  async function open() {
    const f = PB.getFiles()[0];
    if (!f || ext(f) !== "pdf") return;
    if (PB.getPassword(f)) return PB.msg($("msg"), "err", "This PDF is password-protected. Editing is not possible; you can still print it.");
    E = { file: f, sources: [], pages: [], wm: null, dirty: false };
    sel.clear();
    $("pdfEd").classList.remove("hidden"); document.body.style.overflow = "hidden";
    $("peGrid").innerHTML = "<p class='hint' style='padding:30px;text-align:center'>Opening PDF…</p>"; $("peMsg").className = "msg";
    try {
      const bytes = new Uint8Array(await f.arrayBuffer());
      try { const { PDFDocument } = await loadPdfLib(); await PDFDocument.load(bytes); }
      catch (e) { throw new Error(/encrypt/i.test(String(e)) ? "This PDF is protected and cannot be edited. You can still print it." : "This PDF could not be opened for editing."); }
      E.pages = await addSource(bytes);
      renderGrid();
    } catch (e) { close(true); PB.msg($("msg"), "err", e.message || "Could not open the PDF"); }
  }
  function close(force) {
    if (!force && E && E.dirty && !confirm("Discard your changes to this PDF?")) return false;
    $("pdfEd").classList.add("hidden"); $("peSign").classList.add("hidden"); $("pePlace").classList.add("hidden"); $("peWm").classList.add("hidden");
    document.body.style.overflow = ""; thumbTok++; E = null; return true;
  }

  // ---- page chunna
  $("peGrid").onclick = e => {
    const el = e.target.closest(".peP"); if (!el) return;
    const k = +el.dataset.k;
    sel.has(k) ? sel.delete(k) : sel.add(k);
    el.classList.toggle("on", sel.has(k)); updateBar();
  };
  $("peAll").onclick = () => { if (sel.size === E.pages.length) sel.clear(); else E.pages.forEach((_, k) => sel.add(k)); renderGrid(); };

  // ---- kaam
  $("peRot").onclick = () => { for (const k of sel) { const p = E.pages[k]; p.rot = (p.rot + 1) % 4; p.thumb = ""; rotSigs(p); } changed(); renderGrid(); };
  function rotSigs(p) {   // page ghuma to sign bhi saath ghume: abhi ke liye hata do aur bata do
    if (p.sigs.length) { p.sigs = []; emsg("info", "Signatures on rotated pages were removed. Add them again after rotating."); }
  }
  $("peDel").onclick = () => {
    if (sel.size === E.pages.length) return emsg("err", "You can't delete every page");
    E.pages = E.pages.filter((_, k) => !sel.has(k)); const n = sel.size; sel.clear(); changed(); renderGrid();
    emsg("ok", `${n} page${n > 1 ? "s" : ""} deleted`);
  };
  $("peKeep").onclick = () => {
    E.pages = E.pages.filter((_, k) => sel.has(k)); sel.clear(); changed(); renderGrid();
    emsg("ok", `Kept ${E.pages.length} page${E.pages.length > 1 ? "s" : ""}`);
  };
  function move(dir) {
    const ks = [...sel].sort((a, b) => dir < 0 ? a - b : b - a), nsel = new Set();
    for (const k of ks) { const j = k + dir; [E.pages[k], E.pages[j]] = [E.pages[j], E.pages[k]]; nsel.add(j); }
    sel.clear(); nsel.forEach(k => sel.add(k)); changed(); renderGrid();
  }
  $("peLeft").onclick = () => move(-1);
  $("peRight").onclick = () => move(1);
  $("peAddIn").onchange = async e => {
    const list = [...e.target.files]; e.target.value = ""; if (!list.length) return;
    emsg("info", "Adding pages…");
    try {
      for (const f of list) {
        const bytes = ext(f) === "pdf" ? new Uint8Array(await f.arrayBuffer()) : IMG.includes(ext(f)) ? await imageToPdfBytes(f) : null;
        if (!bytes) continue;
        E.pages.push(...await addSource(bytes));
      }
      changed(); renderGrid(); emsg("ok", "Pages added at the end. Select them and use ◀ to move them.");
    } catch (err) { emsg("err", "Could not add that file (it may be password-protected)"); }
  };

  // ---- watermark
  $("peWmBtn").onclick = () => {
    const w = E.wm || { text: "COPY", op: 0.18, size: 14, color: "#9ca3af" };
    $("wmText").value = w.text; $("wmOp").value = Math.round(w.op * 100); $("wmSize").value = w.size;
    $("peWm").classList.remove("hidden"); $("wmText").focus();
  };
  document.querySelectorAll("#wmQuick button").forEach(b => b.onclick = () => { $("wmText").value = b.textContent; });
  $("wmCancel").onclick = () => $("peWm").classList.add("hidden");
  $("wmRemove").onclick = () => { E.wm = null; $("peWm").classList.add("hidden"); changed(); renderGrid(); };
  $("wmOk").onclick = () => {
    const t = $("wmText").value.trim().slice(0, 40);
    E.wm = t ? { text: t, op: $("wmOp").value / 100, size: +$("wmSize").value, color: "#6b7280" } : null;
    $("peWm").classList.add("hidden"); changed(); renderGrid();
  };

  // ---- sign: 1) ungli se sign banao  2) page par rakho
  let pad = null, sigImg = null, sigAr = 0.4;
  const K_SIG = "pb.signature";
  function padInit() {
    const c = $("sigPad"), r = c.getBoundingClientRect(), dpr = Math.min(2, devicePixelRatio || 1);
    c.width = r.width * dpr; c.height = r.height * dpr;
    const x = c.getContext("2d"); x.scale(dpr, dpr); x.lineWidth = 2.6; x.lineCap = x.lineJoin = "round"; x.strokeStyle = $("sigColor").value;
    pad = { x, drawing: false, any: false, last: null };
  }
  $("peSignBtn").onclick = () => {
    $("peSign").classList.remove("hidden"); padInit();
    let saved = null; try { saved = localStorage.getItem(K_SIG); } catch (e) {}
    $("sigSaved").classList.toggle("hidden", !saved);
    if (saved) $("sigSavedImg").src = saved;
  };
  const pt = e => { const r = $("sigPad").getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  $("sigPad").addEventListener("pointerdown", e => { e.preventDefault(); $("sigPad").setPointerCapture(e.pointerId); pad.drawing = true; pad.last = pt(e); pad.x.strokeStyle = $("sigColor").value; });
  $("sigPad").addEventListener("pointermove", e => {
    if (!pad || !pad.drawing) return;
    const p = pt(e); pad.x.beginPath(); pad.x.moveTo(...pad.last); pad.x.lineTo(...p); pad.x.stroke(); pad.last = p; pad.any = true;
  });
  ["pointerup", "pointercancel"].forEach(ev => $("sigPad").addEventListener(ev, () => { if (pad) pad.drawing = false; }));
  $("sigClear").onclick = padInit;
  $("sigCancel").onclick = () => $("peSign").classList.add("hidden");
  $("sigColor").onchange = () => { if (pad) pad.x.strokeStyle = $("sigColor").value; };
  // sign ke aas-paas ki khaali jagah kaat do
  function trim(c) {
    const x = c.getContext("2d"), d = x.getImageData(0, 0, c.width, c.height).data;
    let a = c.width, b = c.height, r = 0, t = 0;
    for (let y = 0; y < c.height; y++) for (let X = 0; X < c.width; X++) if (d[(y * c.width + X) * 4 + 3] > 10) { a = Math.min(a, X); r = Math.max(r, X); b = Math.min(b, y); t = Math.max(t, y); }
    if (r <= a || t <= b) return null;
    const pad2 = 6, o = document.createElement("canvas"); o.width = r - a + pad2 * 2; o.height = t - b + pad2 * 2;
    o.getContext("2d").drawImage(c, a - pad2, b - pad2, o.width, o.height, 0, 0, o.width, o.height);
    return o;
  }
  async function useSig(url) {
    const im = new Image(); im.src = url; await im.decode();
    sigImg = im; sigAr = im.naturalHeight / im.naturalWidth;
    $("peSign").classList.add("hidden");
    startPlace();
  }
  $("sigOk").onclick = async () => {
    if (!pad.any) return;
    const t = trim($("sigPad")); if (!t) return;
    const url = t.toDataURL("image/png");
    if ($("sigRemember").checked) try { localStorage.setItem(K_SIG, url); } catch (e) {}
    useSig(url);
  };
  $("sigSavedImg").onclick = () => { try { const u = localStorage.getItem(K_SIG); if (u) useSig(u); } catch (e) {} };
  $("sigForget").onclick = () => { try { localStorage.removeItem(K_SIG); } catch (e) {} $("sigSaved").classList.add("hidden"); };

  let place = null;   // { k, fx, fy, fw }
  async function startPlace() {
    const k = sel.size ? Math.min(...sel) : 0;
    place = { k, fx: 0.55, fy: 0.78, fw: 0.3 };
    $("pePlace").classList.remove("hidden");
    await showPlacePage();
  }
  async function showPlacePage() {
    const p = E.pages[place.k], box = $("plStage"), maxW = Math.min(box.clientWidth - 24, 520);
    $("plTitle").textContent = `Page ${place.k + 1} of ${E.pages.length}`;
    $("plPrev").disabled = place.k === 0; $("plNext").disabled = place.k >= E.pages.length - 1;
    const c = await renderPage(p, maxW * Math.min(2, devicePixelRatio || 1));
    let ar = c.height / c.width, w = maxW, h = w * ar;
    const maxH = box.clientHeight - 24; if (h > maxH) { h = maxH; w = h / ar; }
    const pg = $("plPage"); pg.style.width = w + "px"; pg.style.height = h + "px";
    $("plImg").src = c.toDataURL("image/jpeg", 0.85);
    $("plSig").src = sigImg.src; posSig();
  }
  function posSig() {
    const pg = $("plPage"), W = pg.clientWidth, H = pg.clientHeight, s = $("plSig");
    place.fw = Math.max(0.08, Math.min(0.9, place.fw));
    const h = place.fw * W * sigAr / H;
    place.fx = Math.max(0, Math.min(1 - place.fw, place.fx)); place.fy = Math.max(0, Math.min(1 - h, place.fy));
    Object.assign(s.style, { left: place.fx * 100 + "%", top: place.fy * 100 + "%", width: place.fw * 100 + "%" });
    $("plSize").value = Math.round(place.fw * 100);
  }
  let drag = null;
  $("plSig").addEventListener("pointerdown", e => { e.preventDefault(); $("plSig").setPointerCapture(e.pointerId); drag = { x: e.clientX, y: e.clientY, fx: place.fx, fy: place.fy }; });
  $("plSig").addEventListener("pointermove", e => {
    if (!drag) return; const pg = $("plPage");
    place.fx = drag.fx + (e.clientX - drag.x) / pg.clientWidth; place.fy = drag.fy + (e.clientY - drag.y) / pg.clientHeight; posSig();
  });
  ["pointerup", "pointercancel"].forEach(ev => $("plSig").addEventListener(ev, () => { drag = null; }));
  $("plPage").addEventListener("click", e => {   // page par tap = sign wahan
    if (e.target === $("plSig")) return;
    const r = $("plPage").getBoundingClientRect();
    place.fx = (e.clientX - r.left) / r.width - place.fw / 2; place.fy = (e.clientY - r.top) / r.height - place.fw * r.width * sigAr / r.height / 2; posSig();
  });
  $("plSize").oninput = e => { const c = place.fx + place.fw / 2; place.fw = e.target.value / 100; place.fx = c - place.fw / 2; posSig(); };
  $("plPrev").onclick = () => { place.k--; showPlacePage(); };
  $("plNext").onclick = () => { place.k++; showPlacePage(); };
  $("plCancel").onclick = () => $("pePlace").classList.add("hidden");
  $("plOk").onclick = () => {
    const pg = $("plPage"), p = E.pages[place.k];
    p.sigs.push({ fx: place.fx, fy: place.fy, fw: place.fw, ar: sigAr * pg.clientWidth / pg.clientWidth, img: sigImg, url: sigImg.src, R: R(p) });
    $("pePlace").classList.add("hidden"); changed(); renderGrid();
    emsg("ok", `Signature added on page ${place.k + 1}`);
  };

  // ---- save: pdf-lib se nayi PDF
  async function build() {
    const { PDFDocument, degrees, StandardFonts, rgb } = await loadPdfLib();
    const out = await PDFDocument.create(), libDocs = [];
    for (const s of E.sources) libDocs.push(await PDFDocument.load(s.bytes));
    const font = E.wm ? await out.embedFont(StandardFonts.HelveticaBold) : null;
    const sigCache = new Map();
    for (const p of E.pages) {
      const [pg] = await out.copyPages(libDocs[p.s], [p.i]);
      out.addPage(pg);
      const rot = R(p); pg.setRotation(degrees(rot));
      const { width: W, height: H } = pg.getSize();   // bina ghumaye naap
      const box = pg.getMediaBox ? pg.getMediaBox() : { x: 0, y: 0 }, ox = box.x || 0, oy = box.y || 0;
      const Wd = rot % 180 ? H : W, Hd = rot % 180 ? W : H;   // dikhne wale naap
      // dikhne wale (upar-baayein se) bindu -> PDF ke bindu
      const toPdf = (vx, vy) => {
        let ux, uy;
        if (rot === 0) { ux = vx; uy = vy; } else if (rot === 90) { ux = vy; uy = H - vx; }
        else if (rot === 180) { ux = W - vx; uy = H - vy; } else { ux = W - vy; uy = vx; }
        return [ox + ux, oy + H - uy];
      };
      for (const s of p.sigs) {
        let img = sigCache.get(s.url);
        if (!img) { img = await out.embedPng(await (await fetch(s.url)).arrayBuffer()); sigCache.set(s.url, img); }
        const w = s.fw * Wd, h = w * s.ar, [x, y] = toPdf(s.fx * Wd, s.fy * Hd + h);
        pg.drawImage(img, { x, y, width: w, height: h, rotate: degrees(rot) });
      }
      if (E.wm) {
        const size = Math.min(Wd, Hd) * E.wm.size / 100, tw = font.widthOfTextAtSize(E.wm.text, size);
        const a = 45 + rot, rad = a * Math.PI / 180, [cx, cy] = toPdf(Wd / 2, Hd / 2);
        const x = cx - Math.cos(rad) * tw / 2 + Math.sin(rad) * size * 0.35, y = cy - Math.sin(rad) * tw / 2 - Math.cos(rad) * size * 0.35;
        pg.drawText(E.wm.text, { x, y, size, font, color: rgb(0.42, 0.45, 0.5), opacity: E.wm.op, rotate: degrees(a) });
      }
    }
    return new Uint8Array(await out.save());
  }
  $("peSave").onclick = async () => {
    const b = $("peSave"); b.disabled = true; b.textContent = "Saving…";
    try {
      const bytes = await build();
      const name = E.file.name.replace(/\.pdf$/i, "").replace(/_edited$/, "") + "_edited.pdf";
      const file = new File([bytes], name, { type: "application/pdf" }), n = E.pages.length;
      close(true);
      PB.setFiles([file], `✅ PDF saved: ${n} page${n > 1 ? "s" : ""}, ${PB.fmt(file.size)}. Check the preview and tap Print.`);
    } catch (e) { emsg("err", "Could not save the PDF: " + (e.message || e)); }
    finally { b.disabled = false; b.textContent = "✓ Save"; }
  };
  $("peClose").onclick = () => close();
  $("pdfEditBtn").onclick = open;

  window.PBPdfEd = {
    version: VERSION, open, close,
    back() {   // phone ka Back
      if (!$("peSign").classList.contains("hidden")) { $("peSign").classList.add("hidden"); return true; }
      if (!$("pePlace").classList.contains("hidden")) { $("pePlace").classList.add("hidden"); return true; }
      if (!$("peWm").classList.contains("hidden")) { $("peWm").classList.add("hidden"); return true; }
      if (!$("pdfEd").classList.contains("hidden")) { close(); return true; }
      return false;
    },
    get state() { return E; },
  };
})();
