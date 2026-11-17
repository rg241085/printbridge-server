// PrintBridge - Scan → PDF
// Camera/Gallery ya laptop ke scanner se pages lo, kone theek karo (tircha kaagaz seedha),
// "Document" filter lagao, aur sab pages ki ek PDF banao. Phir print ya share.
(() => {
  const PB = window.PB;
  if (!PB) return;
  const { $ } = PB;
  const MAX_SRC = 2200;   // camera photo ko itne pixel tak chhota karke rakhte hain (memory bachane ke liye)
  const MAX_OUT = 1800;   // scan page ka sabse lamba hissa (pixels)
  const pages = [];       // { src: canvas, corners: [[x,y]x4] (0..1), rot: 0..3, filter, blob, url, w, h }

  // ---------------------------------------------------------------- screen
  function openScan() {
    PB.show("scan");
    if (!$("scanName").value) {
      const d = new Date(), z = n => String(n).padStart(2, "0");
      $("scanName").value = `Scan_${d.getFullYear()}${z(d.getMonth() + 1)}${z(d.getDate())}_${z(d.getHours())}${z(d.getMinutes())}`;
    }
    renderGrid();
  }
  $("openScan").onclick = openScan;
  $("openLaptopScan").onclick = () => { openScan(); laptopScan(); };
  $("scanBack").onclick = () => PB.show("main");

  function smsg(type, text) { PB.msg($("scanMsg"), type, text); }

  function renderGrid() {
    $("scanGrid").innerHTML = pages.length ? pages.map((p, i) =>
      `<div class="sp" data-i="${i}"><img src="${p.url || ""}" alt=""><span class="no">${i + 1}</span></div>`).join("")
      : `<p class="hint" style="grid-column:1/-1">Abhi koi page nahi. Camera se photo lijiye.</p>`;
    $("scanPrint").disabled = $("scanShare").disabled = !pages.length;
    $("scanPrint").textContent = pages.length > 1 ? `🖨 ${pages.length} page ki PDF banakar print karein` : "🖨 PDF banakar print karein";
  }
  $("scanGrid").onclick = e => { const el = e.target.closest(".sp"); if (el) openEditor(+el.dataset.i); };

  // ---------------------------------------------------------------- pages jodna
  async function fileToCanvas(blob) {
    let bmp;
    try { bmp = await createImageBitmap(blob, { imageOrientation: "from-image" }); }
    catch (e) { bmp = await createImageBitmap(blob); }
    const s = Math.min(1, MAX_SRC / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * s); c.height = Math.round(bmp.height * s);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    bmp.close && bmp.close();
    return c;
  }
  async function addImages(list, filter = "doc", openFirst = true) {
    const start = pages.length;
    for (const f of list) {
      try {
        const src = await fileToCanvas(f);
        const page = { src, corners: [[0, 0], [1, 0], [1, 1], [0, 1]], rot: 0, filter };
        await processPage(page);
        pages.push(page);
      } catch (e) { smsg("err", "Photo khul nahi paayi: " + (f.name || "")); }
    }
    renderGrid();
    if (openFirst && pages.length > start) openEditor(start);
  }
  $("camIn").onchange = e => { const f = [...e.target.files]; e.target.value = ""; if (f.length) addImages(f); };
  $("galIn").onchange = e => { const f = [...e.target.files]; e.target.value = ""; if (f.length) addImages(f, "doc", f.length === 1); };

  // Laptop ke scanner se
  async function laptopScan() {
    const l = PB.cur();
    if (!l || !PB.isOnline()) return smsg("err", "Laptop offline hai");
    $("lapScanBtn").disabled = true;
    smsg("info", "🖨 Laptop ko scan ke liye bola… Kaagaz scanner ke glass par rakhiye.");
    try {
      const r = await PB.api(`/api/devices/${encodeURIComponent(l.id)}/scan`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-PB-Token": l.token },
        body: JSON.stringify({ color: "color", dpi: 200, phone: PB.phoneName }) });
      if (!r.ok) throw new Error(r.error || "Scan shuru nahi hua");
      const until = Date.now() + 3 * 60 * 1000;
      while (Date.now() < until) {
        await new Promise(res => setTimeout(res, 1500));
        const st = await PB.api(`/api/scans/${r.scan_id}`).catch(() => null);
        if (!st || !st.ok) continue;
        if (st.status === "scanning") smsg("info", "🖨 Scan ho raha hai…");
        if (st.status === "error") throw new Error(st.message || "Scan nahi ho paaya");
        if (st.status === "done") {
          const blob = await fetch(`/api/scans/${r.scan_id}/file?key=${encodeURIComponent(r.key)}`).then(x => { if (!x.ok) throw new Error("Scan download nahi hua"); return x.blob(); });
          await addImages([blob], "orig", true);
          return smsg("ok", "✅ Scan aa gaya. Aur page ke liye dobara dabaiye.");
        }
      }
      throw new Error("Scanner se jawab nahi aaya");
    } catch (e) { smsg("err", e.message); }
    finally { $("lapScanBtn").disabled = false; }
  }
  $("lapScanBtn").onclick = laptopScan;

  // ---------------------------------------------------------------- image processing
  // 4 kone -> seedha rectangle (perspective theek karna)
  function homography(src, dst) {
    // dst (x,y) -> src (u,v): 8 unknowns, Gaussian elimination
    const A = [], b = [];
    for (let i = 0; i < 4; i++) {
      const [x, y] = dst[i], [u, v] = src[i];
      A.push([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.push(u);
      A.push([0, 0, 0, x, y, 1, -v * x, -v * y]); b.push(v);
    }
    for (let c = 0; c < 8; c++) {
      let p = c; for (let r = c + 1; r < 8; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
      [A[c], A[p]] = [A[p], A[c]]; [b[c], b[p]] = [b[p], b[c]];
      for (let r = 0; r < 8; r++) if (r !== c) {
        const f = A[r][c] / A[c][c];
        for (let k = c; k < 8; k++) A[r][k] -= f * A[c][k];
        b[r] -= f * b[c];
      }
    }
    return b.map((v, i) => v / A[i][i]);
  }
  const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

  function warp(page) {
    const W = page.src.width, H = page.src.height;
    const q = page.corners.map(([x, y]) => [x * W, y * H]);
    const full = page.corners.every(([x, y], i) => x === [0, 1, 1, 0][i] && y === [0, 0, 1, 1][i]);
    let ow = Math.max(dist(q[0], q[1]), dist(q[3], q[2])), oh = Math.max(dist(q[0], q[3]), dist(q[1], q[2]));
    const s = Math.min(1, MAX_OUT / Math.max(ow, oh));
    ow = Math.max(1, Math.round(ow * s)); oh = Math.max(1, Math.round(oh * s));
    const out = document.createElement("canvas"); out.width = ow; out.height = oh;
    const octx = out.getContext("2d");
    if (full) { octx.drawImage(page.src, 0, 0, ow, oh); return out; }
    const sd = page.src.getContext("2d").getImageData(0, 0, W, H).data;
    const od = octx.createImageData(ow, oh), o = od.data;
    const h = homography(q, [[0, 0], [ow, 0], [ow, oh], [0, oh]]);
    for (let y = 0; y < oh; y++) {
      for (let x = 0; x < ow; x++) {
        const d = h[6] * x + h[7] * y + 1;
        let u = (h[0] * x + h[1] * y + h[2]) / d, v = (h[3] * x + h[4] * y + h[5]) / d;
        u = u < 0 ? 0 : u > W - 1.001 ? W - 1.001 : u; v = v < 0 ? 0 : v > H - 1.001 ? H - 1.001 : v;
        // bilinear: aas-paas ke 4 pixel milakar (kinaare saaf rehte hain)
        const x0 = u | 0, y0 = v | 0, fx = u - x0, fy = v - y0;
        const i00 = (y0 * W + x0) * 4, i10 = i00 + 4, i01 = i00 + W * 4, i11 = i01 + 4;
        const w00 = (1 - fx) * (1 - fy), w10 = fx * (1 - fy), w01 = (1 - fx) * fy, w11 = fx * fy;
        const oi = (y * ow + x) * 4;
        o[oi] = sd[i00] * w00 + sd[i10] * w10 + sd[i01] * w01 + sd[i11] * w11;
        o[oi + 1] = sd[i00 + 1] * w00 + sd[i10 + 1] * w10 + sd[i01 + 1] * w01 + sd[i11 + 1] * w11;
        o[oi + 2] = sd[i00 + 2] * w00 + sd[i10 + 2] * w10 + sd[i01 + 2] * w01 + sd[i11 + 2] * w11;
        o[oi + 3] = 255;
      }
    }
    octx.putImageData(od, 0, 0);
    return out;
  }

  function rotate(c, rot) {
    if (!rot) return c;
    const r = document.createElement("canvas");
    r.width = rot % 2 ? c.height : c.width; r.height = rot % 2 ? c.width : c.height;
    const x = r.getContext("2d");
    x.translate(r.width / 2, r.height / 2); x.rotate(rot * Math.PI / 2);
    x.drawImage(c, -c.width / 2, -c.height / 2);
    return r;
  }

  // "Document" filter: chhaaya hatao (background se divide), safed kaagaz aur gehre akshar
  function applyFilter(c, filter) {
    if (filter === "orig") return c;
    const w = c.width, h = c.height, ctx = c.getContext("2d");
    const img = ctx.getImageData(0, 0, w, h), d = img.data;
    const gray = new Uint8ClampedArray(w * h);
    for (let i = 0, j = 0; i < d.length; i += 4, j++) gray[j] = (d[i] * 299 + d[i + 1] * 587 + d[i + 2] * 114) / 1000;
    if (filter === "doc") {
      // background (kaagaz ki roshni) ka andaza: chhota karke blur
      const bw = Math.max(8, Math.round(w / 16)), bh = Math.max(8, Math.round(h / 16));
      const small = document.createElement("canvas"); small.width = bw; small.height = bh;
      const sx = small.getContext("2d"); sx.filter = "blur(3px)"; sx.drawImage(c, 0, 0, bw, bh);
      const big = document.createElement("canvas"); big.width = w; big.height = h;
      const bx = big.getContext("2d"); bx.imageSmoothingQuality = "high"; bx.drawImage(small, 0, 0, w, h);
      const bd = bx.getImageData(0, 0, w, h).data;
      for (let i = 0, j = 0; i < d.length; i += 4, j++) {
        const bg = Math.max(30, (bd[i] * 299 + bd[i + 1] * 587 + bd[i + 2] * 114) / 1000 + 6);
        let v = gray[j] / bg * 255;
        v = (v - 80) * 255 / (235 - 80);          // levels: safed ko safed, akshar ko gehra
        v = v < 0 ? 0 : v > 255 ? 255 : v;
        d[i] = d[i + 1] = d[i + 2] = v;
      }
    } else {
      for (let i = 0, j = 0; i < d.length; i += 4, j++) d[i] = d[i + 1] = d[i + 2] = gray[j];
    }
    ctx.putImageData(img, 0, 0);
    return c;
  }

  async function processPage(page) {
    const out = applyFilter(rotate(warp(page), page.rot), page.filter);
    page.w = out.width; page.h = out.height;
    page.blob = await new Promise(res => out.toBlob(res, "image/jpeg", page.filter === "orig" ? 0.85 : 0.8));
    if (page.url) URL.revokeObjectURL(page.url);
    page.url = URL.createObjectURL(page.blob);
  }

  // ---------------------------------------------------------------- editor
  let ed = null;  // { i, corners, rot, filter, box }
  function openEditor(i) {
    const p = pages[i];
    ed = { i, corners: p.corners.map(c => [...c]), rot: p.rot, filter: p.filter };
    $("edTitle").textContent = `Page ${i + 1} / ${pages.length}`;
    $("editor").classList.remove("hidden");
    requestAnimationFrame(drawEditor);
  }
  function drawEditor() {
    const p = pages[ed.i], st = $("edStage"), cv = $("edCanvas");
    const sw = st.clientWidth - 40, sh = st.clientHeight - 40;
    const s = Math.min(sw / p.src.width, sh / p.src.height);
    cv.width = Math.round(p.src.width * s); cv.height = Math.round(p.src.height * s);
    cv.style.left = Math.round((st.clientWidth - cv.width) / 2) + "px";
    cv.style.top = Math.round((st.clientHeight - cv.height) / 2) + "px";
    cv.getContext("2d").drawImage(p.src, 0, 0, cv.width, cv.height);
    ed.box = { x: cv.offsetLeft, y: cv.offsetTop, w: cv.width, h: cv.height };
    document.querySelectorAll("#edFilter button").forEach(b => b.classList.toggle("on", b.dataset.v === ed.filter));
    placeHandles();
  }
  function placeHandles() {
    const { x, y, w, h } = ed.box;
    document.querySelectorAll("#edStage .hd").forEach(el => {
      const [cx, cy] = ed.corners[+el.dataset.k];
      el.style.left = (x + cx * w) + "px"; el.style.top = (y + cy * h) + "px";
    });
    $("edPoly").setAttribute("points", ed.corners.map(([cx, cy]) => `${x + cx * w},${y + cy * h}`).join(" "));
  }
  let drag = null;
  $("edStage").addEventListener("pointerdown", e => {
    const el = e.target.closest(".hd"); if (!el) return;
    drag = +el.dataset.k; el.setPointerCapture(e.pointerId); e.preventDefault();
  });
  $("edStage").addEventListener("pointermove", e => {
    if (drag === null) return;
    const r = $("edStage").getBoundingClientRect(), { x, y, w, h } = ed.box;
    ed.corners[drag] = [Math.min(1, Math.max(0, (e.clientX - r.left - x) / w)), Math.min(1, Math.max(0, (e.clientY - r.top - y) / h))];
    placeHandles();
  });
  const endDrag = () => { drag = null; };
  $("edStage").addEventListener("pointerup", endDrag);
  $("edStage").addEventListener("pointercancel", endDrag);
  $("edFilter").onclick = e => { const b = e.target.closest("button"); if (b) { ed.filter = b.dataset.v; drawEditor(); } };
  $("edRotate").onclick = () => { ed.rot = (ed.rot + 1) % 4; $("edTitle").textContent = `Page ${ed.i + 1} • ${ed.rot * 90}° ghumaya`; };
  $("edFull").onclick = () => { ed.corners = [[0, 0], [1, 0], [1, 1], [0, 1]]; placeHandles(); };
  $("edDel").onclick = () => {
    if (!confirm("Ye page hata dein?")) return;
    const [p] = pages.splice(ed.i, 1); if (p.url) URL.revokeObjectURL(p.url);
    closeEditor(); renderGrid();
  };
  $("edClose").onclick = closeEditor;
  $("edDone").onclick = async () => {
    const p = pages[ed.i];
    Object.assign(p, { corners: ed.corners, rot: ed.rot, filter: ed.filter });
    $("edDone").textContent = "…";
    await new Promise(r => setTimeout(r, 30));
    await processPage(p);
    $("edDone").textContent = "✓ Theek hai";
    closeEditor(); renderGrid();
  };
  function closeEditor() { $("editor").classList.add("hidden"); ed = null; }
  window.addEventListener("resize", () => { if (ed) drawEditor(); });

  // ---------------------------------------------------------------- PDF banana (bina kisi library ke)
  async function makePdf() {
    const enc = new TextEncoder(), parts = [], offsets = [];
    let len = 0;
    const push = x => { const b = typeof x === "string" ? enc.encode(x) : x; parts.push(b); len += b.length; };
    const obj = (n, body) => { offsets[n] = len; push(`${n} 0 obj\n`); body(); push("\nendobj\n"); };
    push("%PDF-1.4\n%\xE2\xE3\xCF\xD3\n");
    const n = pages.length, kids = [];
    for (let i = 0; i < n; i++) kids.push(`${3 + i * 3} 0 R`);
    obj(1, () => push("<< /Type /Catalog /Pages 2 0 R >>"));
    obj(2, () => push(`<< /Type /Pages /Kids [${kids.join(" ")}] /Count ${n} >>`));
    for (let i = 0; i < n; i++) {
      const p = pages[i], pid = 3 + i * 3, cid = pid + 1, iid = pid + 2;
      const land = p.w > p.h;
      const PW = land ? 842 : 595, PH = land ? 595 : 842, m = 0;   // A4 points
      const s = Math.min((PW - 2 * m) / p.w, (PH - 2 * m) / p.h);
      const dw = p.w * s, dh = p.h * s, dx = (PW - dw) / 2, dy = (PH - dh) / 2;
      const jpg = new Uint8Array(await p.blob.arrayBuffer());
      const content = `q ${dw.toFixed(2)} 0 0 ${dh.toFixed(2)} ${dx.toFixed(2)} ${dy.toFixed(2)} cm /Im0 Do Q`;
      obj(pid, () => push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PW} ${PH}] /Resources << /XObject << /Im0 ${iid} 0 R >> >> /Contents ${cid} 0 R >>`));
      obj(cid, () => push(`<< /Length ${content.length} >>\nstream\n${content}\nendstream`));
      obj(iid, () => {
        push(`<< /Type /XObject /Subtype /Image /Width ${p.w} /Height ${p.h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpg.length} >>\nstream\n`);
        push(jpg); push("\nendstream");
      });
    }
    const total = 3 + n * 3, xref = len;
    let x = `xref\n0 ${total}\n0000000000 65535 f \n`;
    for (let k = 1; k < total; k++) x += String(offsets[k]).padStart(10, "0") + " 00000 n \n";
    push(x + `trailer\n<< /Size ${total} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
    const name = ($("scanName").value.trim() || "Scan").replace(/[\\/:*?"<>|]+/g, "_") + ".pdf";
    return new File(parts, name, { type: "application/pdf" });
  }

  $("scanPrint").onclick = async () => {
    if (!pages.length) return;
    $("scanPrint").disabled = true;
    try { PB.printFile(await makePdf()); }
    catch (e) { smsg("err", "PDF nahi ban paayi"); }
    finally { $("scanPrint").disabled = false; }
  };
  $("scanShare").onclick = async () => {
    if (!pages.length) return;
    const file = await makePdf();
    try {
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], title: file.name });
        return;
      }
    } catch (e) { if (e.name === "AbortError") return; }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(file); a.download = file.name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    smsg("ok", `✅ ${file.name} download ho gayi`);
  };

  // test ke liye
  window.PBScan = { pages, addImages, makePdf, processPage, openEditor };
})();
