// PrintBridge - PDF tools (jodna, chhota karna) + Passport size photo
(() => {
  const PB = window.PB;
  if (!PB) return;
  const { $ } = PB;
  const extOf = f => (f.name.split(".").pop() || "").toLowerCase();
  const IMG = ["jpg", "jpeg", "png", "bmp", "gif", "webp"];
  const isPdf = f => extOf(f) === "pdf";
  const isImg = f => IMG.includes(extOf(f));
  const stamp = () => { const d = new Date(), z = n => String(n).padStart(2, "0"); return `${d.getFullYear()}${z(d.getMonth() + 1)}${z(d.getDate())}_${z(d.getHours())}${z(d.getMinutes())}`; };
  const MM = 72 / 25.4;
  let lastResult = null;

  async function loadPdfLib() {
    if (window.PDFLib) return window.PDFLib;
    await new Promise((res, rej) => { const s = document.createElement("script"); s.src = "/pdf-lib.min.js"; s.onload = res; s.onerror = rej; document.head.appendChild(s); });
    return window.PDFLib;
  }
  function info(text) { $("toolInfo").innerHTML = text; $("toolResult").classList.remove("hidden"); }
  function busy(btn, on, text) { btn.disabled = on; if (text) btn.textContent = text; }

  // Kisi bhi photo ko JPEG bytes mein (pdf-lib sirf JPG/PNG samajhta hai)
  async function imageToJpeg(file, quality = 0.9, maxSide = 3000) {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" }).catch(() => createImageBitmap(file));
    const s = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas"); c.width = Math.round(bmp.width * s); c.height = Math.round(bmp.height * s);
    const x = c.getContext("2d"); x.fillStyle = "#fff"; x.fillRect(0, 0, c.width, c.height); x.drawImage(bmp, 0, 0, c.width, c.height);
    bmp.close && bmp.close();
    const blob = await new Promise(r => c.toBlob(r, "image/jpeg", quality));
    return { bytes: new Uint8Array(await blob.arrayBuffer()), w: c.width, h: c.height };
  }

  // ---------------------------------------------------------------- tools dikhana
  function update() {
    const files = PB.getFiles();
    const usable = files.filter(f => isPdf(f) || isImg(f));
    $("mergeBtn").classList.toggle("hidden", !(files.length > 1 && usable.length === files.length));
    $("compressBtn").classList.toggle("hidden", !(files.length === 1 && isPdf(files[0])));
    $("pdfTools").classList.toggle("hidden", $("mergeBtn").classList.contains("hidden") && $("compressBtn").classList.contains("hidden"));
    if (!files.length || (lastResult && files[0] !== lastResult)) $("toolResult").classList.add("hidden");
    if (!files.length) $("compressBox").classList.add("hidden");
  }

  // ---------------------------------------------------------------- 1. jodna
  $("mergeBtn").onclick = async () => {
    const files = PB.getFiles(), btn = $("mergeBtn");
    busy(btn, true, "Merging…");
    try {
      const { PDFDocument } = await loadPdfLib();
      const out = await PDFDocument.create();
      let total = 0;
      for (const f of files) {
        total += f.size;
        if (isPdf(f)) {
          let src;
          try { src = await PDFDocument.load(await f.arrayBuffer()); }
          catch (e) { throw new Error(`"${f.name}" is password-protected or damaged and cannot be merged`); }
          (await out.copyPages(src, src.getPageIndices())).forEach(p => out.addPage(p));
        } else {
          const j = await imageToJpeg(f);
          const img = await out.embedJpg(j.bytes);
          const land = j.w > j.h, W = land ? 842 : 595, H = land ? 595 : 842;  // A4
          const s = Math.min(W / j.w, H / j.h), w = j.w * s, h = j.h * s;
          out.addPage([W, H]).drawImage(img, { x: (W - w) / 2, y: (H - h) / 2, width: w, height: h });
        }
      }
      const file = new File([await out.save()], `Merged_${stamp()}.pdf`, { type: "application/pdf" });
      lastResult = file;
      PB.setFiles([file]);
      info(`✅ Merged <b>${files.length} files</b> into one PDF: <b>${out.getPageCount()} pages</b>, ${PB.fmt(file.size)}.<br>You can now print, save or share it.`);
    } catch (e) {
      PB.msg($("msg"), "err", e.message || "Could not merge the files");
    } finally { busy(btn, false, "🔗 Merge into one PDF"); update(); }
  };

  // ---------------------------------------------------------------- 2. chhota karna
  let level = "low";
  $("compressBtn").onclick = () => { $("compressBox").classList.remove("hidden"); $("toolResult").classList.add("hidden"); };
  $("cCancel").onclick = () => $("compressBox").classList.add("hidden");
  $("cLevel").onclick = e => { const b = e.target.closest("button"); if (!b) return; level = b.dataset.v; $("cLevel").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); };
  $("cGo").onclick = async () => {
    const f = PB.getFiles()[0], btn = $("cGo");
    if (!f || !isPdf(f)) return;
    const dpi = level === "high" ? 96 : 150, q = level === "high" ? 0.5 : 0.7;
    busy(btn, true, "Compressing…");
    try {
      const [lib, { PDFDocument }] = await Promise.all([PB.loadPdfJs(), loadPdfLib()]);
      let doc;
      try { doc = await lib.getDocument({ data: await f.arrayBuffer(), password: PB.getPassword(f) }).promise; }
      catch (e) { throw new Error(e && e.name === "PasswordException" ? "Enter the PDF password in the preview first" : "Could not open the PDF"); }
      const out = await PDFDocument.create();
      for (let i = 1; i <= doc.numPages; i++) {
        btn.textContent = `Page ${i} / ${doc.numPages}…`;
        const page = await doc.getPage(i), vp1 = page.getViewport({ scale: 1 }), vp = page.getViewport({ scale: dpi / 72 });
        const c = document.createElement("canvas"); c.width = Math.ceil(vp.width); c.height = Math.ceil(vp.height);
        const x = c.getContext("2d"); x.fillStyle = "#fff"; x.fillRect(0, 0, c.width, c.height);
        await page.render({ canvasContext: x, viewport: vp }).promise;
        const blob = await new Promise(r => c.toBlob(r, "image/jpeg", q));
        const img = await out.embedJpg(new Uint8Array(await blob.arrayBuffer()));
        out.addPage([vp1.width, vp1.height]).drawImage(img, { x: 0, y: 0, width: vp1.width, height: vp1.height });
        c.width = c.height = 0;
      }
      const bytes = await out.save();
      $("compressBox").classList.add("hidden");
      if (bytes.length >= f.size * 0.95) {
        info(`ℹ️ This PDF is already compact (${PB.fmt(f.size)}) and cannot be reduced further.`);
        return;
      }
      const file = new File([bytes], f.name.replace(/\.pdf$/i, "") + "_chhoti.pdf", { type: "application/pdf" });
      lastResult = file;
      PB.setFiles([file]);
      info(`✅ PDF compressed: <b>${PB.fmt(f.size)} → ${PB.fmt(file.size)}</b> (${Math.round((1 - file.size / f.size) * 100)}% smaller).<br>You can now save, share or print it.`);
    } catch (e) {
      PB.msg($("msg"), "err", e.message || "Could not compress the PDF");
    } finally { busy(btn, false, "Compress"); update(); }
  };

  async function share(file, msgEl) {
    try {
      if (navigator.canShare && navigator.canShare({ files: [file] })) { await navigator.share({ files: [file], title: file.name }); return; }
    } catch (e) { if (e.name === "AbortError") return; }
    const a = document.createElement("a"); a.href = URL.createObjectURL(file); a.download = file.name;
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    PB.msg(msgEl, "ok", `✅ ${file.name} downloaded`);
  }
  $("toolShare").onclick = () => { const f = PB.getFiles()[0]; if (f) share(f, $("msg")); };

  // ---------------------------------------------------------------- 3. Passport photo
  const SIZES = { "35x45": [35, 45], "51x51": [51, 51] };
  const pp = { img: null, size: "35x45", count: 8, zoom: 1, ox: 0, oy: 0 };  // ox/oy: photo ka beech (0..1)
  $("openPP").onclick = () => PB.show("pp");
  $("ppBack").onclick = () => PB.show("main");
  const ppMsg = (t, m) => PB.msg($("ppMsg"), t, m);

  async function ppLoad(file) {
    if (!file) return;
    try {
      const bmp = await createImageBitmap(file, { imageOrientation: "from-image" }).catch(() => createImageBitmap(file));
      const s = Math.min(1, 2400 / Math.max(bmp.width, bmp.height));
      const c = document.createElement("canvas"); c.width = Math.round(bmp.width * s); c.height = Math.round(bmp.height * s);
      c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height); bmp.close && bmp.close();
      Object.assign(pp, { img: c, zoom: 1, ox: 0.5, oy: 0.42 });
      $("ppZoom").value = 100;
      $("ppStage").classList.remove("hidden"); $("ppControls").classList.remove("hidden"); $("ppMsg").className = "msg";
      drawPP();
    } catch (e) { ppMsg("err", "Could not open the image"); }
  }
  $("ppCam").onchange = e => { ppLoad(e.target.files[0]); e.target.value = ""; };
  $("ppGal").onchange = e => { ppLoad(e.target.files[0]); e.target.value = ""; };

  // Crop: dabbe ka aspect = photo size. zoom=1 par photo dabbe ko poora dhakti hai.
  function cropRect() {
    const [mw, mh] = SIZES[pp.size], ar = mw / mh, I = pp.img;
    let ch = I.height / pp.zoom, cw = ch * ar;
    if (cw > I.width / pp.zoom) { cw = I.width / pp.zoom; ch = cw / ar; }
    let x = pp.ox * I.width - cw / 2, y = pp.oy * I.height - ch / 2;
    x = Math.max(0, Math.min(I.width - cw, x)); y = Math.max(0, Math.min(I.height - ch, y));
    pp.ox = (x + cw / 2) / I.width; pp.oy = (y + ch / 2) / I.height;
    return { x, y, w: cw, h: ch };
  }
  function renderCrop(outW, outH) {
    const r = cropRect(), c = document.createElement("canvas"); c.width = outW; c.height = outH;
    const x = c.getContext("2d"); x.imageSmoothingQuality = "high";
    if ($("ppBright").checked) x.filter = "brightness(1.08) contrast(1.06) saturate(1.05)";
    x.drawImage(pp.img, r.x, r.y, r.w, r.h, 0, 0, outW, outH);
    return c;
  }
  function drawPP() {
    if (!pp.img) return;
    const [mw, mh] = SIZES[pp.size], cv = $("ppCanvas");
    cv.width = 600; cv.height = Math.round(600 * mh / mw);
    cv.getContext("2d").drawImage(renderCrop(cv.width, cv.height), 0, 0);
  }
  // khiskana (drag)
  let dragPP = null;
  $("ppCanvas").addEventListener("pointerdown", e => { if (!pp.img) return; dragPP = { x: e.clientX, y: e.clientY }; $("ppCanvas").setPointerCapture(e.pointerId); });
  $("ppCanvas").addEventListener("pointermove", e => {
    if (!dragPP) return;
    const r = cropRect(), box = $("ppCanvas").getBoundingClientRect();
    pp.ox -= (e.clientX - dragPP.x) / box.width * r.w / pp.img.width;
    pp.oy -= (e.clientY - dragPP.y) / box.height * r.h / pp.img.height;
    dragPP = { x: e.clientX, y: e.clientY }; drawPP();
  });
  ["pointerup", "pointercancel"].forEach(ev => $("ppCanvas").addEventListener(ev, () => { dragPP = null; }));
  $("ppZoom").oninput = e => { pp.zoom = e.target.value / 100; drawPP(); };
  $("ppBright").onchange = drawPP;
  const segPick = (id, key, conv) => $(id).onclick = e => {
    const b = e.target.closest("button"); if (!b) return;
    pp[key] = conv(b.dataset.v); $(id).querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); drawPP();
  };
  segPick("ppSize", "size", v => v);
  segPick("ppCount", "count", v => +v);

  async function ppPdf() {
    const { PDFDocument, rgb } = await loadPdfLib();
    const [mw, mh] = SIZES[pp.size];
    const px = [Math.round(mw / 25.4 * 300), Math.round(mh / 25.4 * 300)];  // 300 DPI
    const blob = await new Promise(r => renderCrop(px[0], px[1]).toBlob(r, "image/jpeg", 0.92));
    const doc = await PDFDocument.create(), img = await doc.embedJpg(new Uint8Array(await blob.arrayBuffer()));
    const W = 595, H = 842, w = mw * MM, h = mh * MM, gap = 3 * MM, margin = 10 * MM;
    const cols = Math.floor((W - 2 * margin + gap) / (w + gap)), rows = Math.floor((H - 2 * margin + gap) / (h + gap));
    const n = pp.count ? Math.min(pp.count, cols * rows) : cols * rows;
    const useCols = Math.min(cols, n), x0 = (W - (useCols * w + (useCols - 1) * gap)) / 2, top = H - margin;
    const page = doc.addPage([W, H]);
    for (let k = 0; k < n; k++) {
      const c = k % cols, r = Math.floor(k / cols), x = x0 + c * (w + gap), y = top - (r + 1) * h - r * gap;
      page.drawImage(img, { x, y, width: w, height: h });
      page.drawRectangle({ x, y, width: w, height: h, borderColor: rgb(0.6, 0.6, 0.6), borderWidth: 0.5, borderDashArray: [3, 2] });
    }
    return new File([await doc.save()], `Passport_${pp.size}_${stamp()}.pdf`, { type: "application/pdf" });
  }
  $("ppPrint").onclick = async () => {
    if (!pp.img) return ppMsg("err", "Please choose a photo first");
    $("ppPrint").disabled = true;
    try {
      const f = await ppPdf();
      window.PB.printFile(f, { fit: "actual", nup: "1" }, "🧑 Passport photos are ready. \"Actual size\" has been selected automatically. Tap Print.");
    } catch (e) { ppMsg("err", "Could not create the PDF"); }
    finally { $("ppPrint").disabled = false; }
  };
  $("ppShare").onclick = async () => { if (!pp.img) return ppMsg("err", "Please choose a photo first"); share(await ppPdf(), $("ppMsg")); };

  window.PBTools = { version: "2.5.1", update, ppPdf, pp };
})();
