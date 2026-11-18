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
  const FULL = () => [[0, 0], [1, 0], [1, 1], [0, 1]];
  const autoCorners = src => (window.PBDetect && window.PBDetect.detect(src)) || FULL();  // kone apne aap

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
      `<div class="sp" data-i="${i}"><img src="${p.url || ""}" alt=""><span class="no">${i + 1}</span><span class="fl">${FNAMES[p.filter] || ""}</span></div>`).join("")
      + `<div class="sp add" id="spAdd"><b>＋</b><span>Aur page</span></div>`
      : `<p class="hint" style="grid-column:1/-1">Abhi koi page nahi. Neeche "Scan karein" dabaiye.</p>`;
    $("scanPrint").disabled = $("scanShare").disabled = !pages.length;
    $("scanPrint").textContent = pages.length > 1 ? `🖨 ${pages.length} page ki PDF banakar print karein` : "🖨 PDF banakar print karein";
  }
  $("scanGrid").onclick = e => { const el = e.target.closest(".sp"); if (!el) return; if (el.id === "spAdd") return openLive(); openEditor(+el.dataset.i); };

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
  async function addImages(list, filter = null, openFirst = true, auto = true) {
    filter = filter || defFilter();
    const start = pages.length;
    for (const f of list) {
      try {
        const src = await fileToCanvas(f);
        const page = { src, corners: auto ? autoCorners(src) : FULL(), rot: 0, filter };
        await processPage(page);
        pages.push(page);
      } catch (e) { smsg("err", "Photo khul nahi paayi: " + (f.name || "")); }
    }
    renderGrid();
    if (openFirst && pages.length > start) openEditor(start, pages, renderGrid, auto ? "crop" : "filter");
  }
  $("camIn").onchange = e => { const f = [...e.target.files]; e.target.value = ""; if (f.length) addImages(f); };
  $("galIn").onchange = e => { const f = [...e.target.files]; e.target.value = ""; if (f.length) addImages(f, null, f.length === 1); };

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
          await addImages([blob], "orig", true, false);  // scanner ka poora page, kaatna nahi
          return smsg("ok", "✅ Scan aa gaya. Aur page ke liye dobara dabaiye.");
        }
      }
      throw new Error("Scanner se jawab nahi aaya");
    } catch (e) { smsg("err", e.message); }
    finally { $("lapScanBtn").disabled = false; }
  }
  $("lapScanBtn").onclick = laptopScan;

  // ---------------------------------------------------------------- LIVE SCANNER (CamScanner jaisa)
  // Kone alag thread (worker) mein dhoondhte hain, border 60fps par smooth chalti hai,
  // phone sthir ho to shutter ka gol ghera bharta hai aur photo apne aap khinchti hai.
  const HOLD = 1100;  // itni der sthir rahe to photo
  const lv = { stream: null, track: null, raf: 0, auto: true, batch: true, busy: false, needChange: false, lastQ: null,
               torch: false, added: 0, target: null, disp: null, alpha: 0, stableSince: 0, prevQ: null, seen: 0,
               worker: null, wBusy: false, wId: 0, lastSend: 0, ms: 0, lastShot: 0 };
  try { lv.batch = localStorage.getItem("pb.scanBatch") !== "0"; } catch (e) {}
  const qDiff = (a, b) => (a && b) ? Math.max(...a.map((p, i) => Math.abs(p[0] - b[i][0]) + Math.abs(p[1] - b[i][1]))) : 9;
  const qArea = q => { let s = 0; for (let i = 0; i < 4; i++) { const a = q[i], b = q[(i + 1) % 4]; s += a[0] * b[1] - b[0] * a[1]; } return Math.abs(s) / 2; };
  const frameCv = document.createElement("canvas");

  function startWorker() {
    if (lv.worker || !window.Worker) return;
    try {
      lv.worker = new Worker("/detworker.js");
      lv.worker.onmessage = e => { lv.wBusy = false; lv.ms = e.data.ms; onDetect(e.data.q); };
      lv.worker.onerror = () => { lv.worker = null; lv.wBusy = false; };
    } catch (e) { lv.worker = null; }
  }
  function sendFrame() {
    const v = $("lvVideo");
    if (lv.wBusy || lv.busy || v.readyState < 2 || !v.videoWidth) return;
    const now = performance.now(); if (now - lv.lastSend < 90) return;
    lv.lastSend = now;
    if (!lv.worker) { onDetect(window.PBDetect ? window.PBDetect.detect(v, { fast: true }) : null); return; }  // purana browser
    const s = 320 / Math.max(v.videoWidth, v.videoHeight);
    frameCv.width = Math.round(v.videoWidth * s); frameCv.height = Math.round(v.videoHeight * s);
    const x = frameCv.getContext("2d", { willReadFrequently: true });
    x.drawImage(v, 0, 0, frameCv.width, frameCv.height);
    const img = x.getImageData(0, 0, frameCv.width, frameCv.height);
    lv.wBusy = true;
    lv.worker.postMessage({ id: ++lv.wId, img }, [img.data.buffer]);
  }
  function onDetect(q) {
    if (!lv.stream) return;
    const now = performance.now();
    if (q && qArea(q) > 0.1) {
      lv.seen = now;
      if (!lv.prevQ || qDiff(q, lv.prevQ) > 0.035) lv.stableSince = now;   // hil gaya -> ghadi dobara
      lv.prevQ = q; lv.target = q;
      if (lv.needChange && qDiff(q, lv.lastQ) > 0.12) lv.needChange = false;  // naya page aaya
    } else if (now - lv.seen > 450) {   // thodi der na dikhe tabhi border hatao (jhilmil nahi)
      lv.target = null; lv.prevQ = null;
      if (lv.needChange && !q) lv.needChange = false;  // page hata diya
    }
  }
  function videoBox() {  // video screen par kahan dikh raha hai (object-fit: contain)
    const v = $("lvVideo"), W = v.clientWidth, H = v.clientHeight, s = Math.min(W / v.videoWidth, H / v.videoHeight);
    return { x: (W - v.videoWidth * s) / 2, y: (H - v.videoHeight * s) / 2, w: v.videoWidth * s, h: v.videoHeight * s };
  }
  const RING = 2 * Math.PI * 34;
  function frame() {
    if (!lv.stream) return;
    lv.raf = requestAnimationFrame(frame);
    sendFrame();
    const now = performance.now(), v = $("lvVideo");
    // border ko dheere-dheere naye kono ki taraf le jao (smooth)
    if (lv.target) {
      lv.disp = lv.disp ? lv.disp.map((p, i) => [p[0] + (lv.target[i][0] - p[0]) * 0.35, p[1] + (lv.target[i][1] - p[1]) * 0.35]) : lv.target.map(p => [...p]);
      lv.alpha = Math.min(1, lv.alpha + 0.15);
    } else lv.alpha = Math.max(0, lv.alpha - 0.08);
    const prog = (lv.target && !lv.needChange && !lv.busy) ? Math.min(1, (now - lv.stableSince) / HOLD) : 0;
    const ready = prog > 0.35;
    if (lv.disp && v.videoWidth) {
      const b = videoBox(), pts = lv.disp.map(([x, y]) => [b.x + x * b.w, b.y + y * b.h]);
      $("lvPoly").setAttribute("points", pts.map(p => p.join(",")).join(" "));
      document.querySelectorAll("#lvSvg .cn").forEach((c, i) => { c.setAttribute("cx", pts[i][0]); c.setAttribute("cy", pts[i][1]); });
    }
    $("lvSvg").style.opacity = lv.alpha;
    $("lvSvg").classList.toggle("ready", ready || lv.needChange);
    $("lvRing").style.strokeDashoffset = lv.auto ? RING * (1 - prog) : RING;
    let hint;
    if (lv.busy) hint = "📸 Photo le rahe hain…";
    else if (lv.needChange) hint = "✅ Ho gaya! Agla page rakhiye";
    else if (!lv.target) hint = "Kaagaz ko frame mein rakhiye";
    else if (qArea(lv.target) < 0.22) hint = "Thoda paas laaiye";
    else if (!ready) hint = "Phone sthir rakhiye…";
    else hint = lv.auto ? "Bas aise hi rakhiye…" : "Taiyaar! Button dabaiye";
    if ($("lvHint").textContent !== hint) $("lvHint").textContent = hint;
    if (lv.auto && prog >= 1 && now - lv.lastShot > 1200) captureLive(lv.target);
  }

  async function openLive() {
    if (!navigator.mediaDevices?.getUserMedia) return $("camIn").click();  // purana phone: camera app
    try {
      lv.stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: {
        facingMode: { ideal: "environment" }, width: { ideal: 3840 }, height: { ideal: 2160 } } });
    } catch (e) {
      smsg("err", "Camera nahi khula (permission?). 'Camera app' button se photo lijiye.");
      return;
    }
    lv.track = lv.stream.getVideoTracks()[0];
    try { await lv.track.applyConstraints({ advanced: [{ focusMode: "continuous" }] }); } catch (e) {}  // lagatar auto focus
    const caps = lv.track.getCapabilities ? lv.track.getCapabilities() : {};
    $("lvTorch").classList.toggle("hidden", !caps.torch);
    Object.assign(lv, { needChange: false, added: 0, lastQ: null, torch: false, target: null, disp: null, alpha: 0, prevQ: null, busy: false });
    $("lvCount").textContent = "0"; $("lvThumb").style.backgroundImage = ""; $("lvThumb").classList.add("empty");
    $("lvTorch").classList.remove("on");
    setMode();
    $("live").classList.remove("hidden");
    const v = $("lvVideo"); v.srcObject = lv.stream; await v.play().catch(() => {});
    startWorker();
    cancelAnimationFrame(lv.raf); lv.raf = requestAnimationFrame(frame);
  }
  function stopLive() {
    cancelAnimationFrame(lv.raf);
    if (lv.stream) lv.stream.getTracks().forEach(t => t.stop());
    lv.stream = null; $("lvVideo").srcObject = null;
    $("live").classList.add("hidden");
  }
  function setMode() {
    document.querySelectorAll("#lvMode button").forEach(b => b.classList.toggle("on", (b.dataset.v === "batch") === lv.batch));
    $("lvDone").classList.toggle("hidden", !lv.batch);
  }
  // photo ki chhoti tasveer udkar neeche kone mein jaati hai
  function flyThumb(url, q) {
    const st = $("lvStage").getBoundingClientRect(), th = $("lvThumb").getBoundingClientRect();
    const b = videoBox(), qq = q || FULL();
    const xs = qq.map(p => b.x + p[0] * b.w), ys = qq.map(p => b.y + p[1] * b.h);
    const x0 = Math.min(...xs), y0 = Math.min(...ys), w0 = Math.max(...xs) - x0, h0 = Math.max(...ys) - y0;
    const im = document.createElement("img"); im.className = "lvFly"; im.src = url;
    Object.assign(im.style, { left: st.left + x0 + "px", top: st.top + y0 + "px", width: w0 + "px", height: h0 + "px" });
    document.body.appendChild(im);
    requestAnimationFrame(() => requestAnimationFrame(() => Object.assign(im.style, {
      left: th.left + "px", top: th.top + "px", width: th.width + "px", height: th.height + "px", opacity: ".6" })));
    setTimeout(() => im.remove(), 520);
  }
  async function captureLive(liveQ) {
    if (lv.busy || !lv.stream) return;
    lv.busy = true; lv.lastShot = performance.now();
    $("lvFlash").classList.add("on"); setTimeout(() => $("lvFlash").classList.remove("on"), 90);
    if (navigator.vibrate) navigator.vibrate(35);
    try {
      let blob = null;
      if (window.ImageCapture) {  // poore camera ki quality wali photo
        try { blob = await Promise.race([new ImageCapture(lv.track).takePhoto(), new Promise((_, r) => setTimeout(() => r(), 4000))]); } catch (e) { blob = null; }
      }
      if (!blob) {
        const v = $("lvVideo"), c = document.createElement("canvas"); c.width = v.videoWidth; c.height = v.videoHeight;
        c.getContext("2d").drawImage(v, 0, 0);
        blob = await new Promise(r => c.toBlob(r, "image/jpeg", 0.95));
      }
      const src = await fileToCanvas(blob);
      const corners = (window.PBDetect && window.PBDetect.detect(src)) || liveQ || FULL();
      const page = { src, corners, rot: 0, filter: defFilter() };
      await processPage(page);
      pages.push(page); lv.added++;
      if (!lv.batch) {  // ek page: seedha kone/rang wali screen
        stopLive(); renderGrid(); openEditor(pages.length - 1, pages, renderGrid, "crop");
        return;
      }
      flyThumb(page.url, liveQ || corners);
      setTimeout(() => {
        $("lvCount").textContent = String(lv.added); $("lvThumb").classList.remove("empty");
        $("lvThumb").style.backgroundImage = `url(${page.url})`;
      }, 430);
      lv.needChange = true; lv.lastQ = liveQ || corners;
    } catch (e) {
      $("lvHint").textContent = "Photo nahi li ja saki, dobara try kijiye";
    } finally { lv.busy = false; lv.stableSince = performance.now(); }
  }
  function liveDone() {
    stopLive(); renderGrid();
    if (lv.added) smsg("ok", `✅ ${lv.added} page scan hue. Kisi page par tap karke kone ya rang badal sakte hain.`);
  }
  $("liveBtn").onclick = openLive;
  $("lvClose").onclick = liveDone;
  $("lvDone").onclick = liveDone;
  $("lvThumb").onclick = () => { if (lv.added) liveDone(); };
  $("lvShot").onclick = () => captureLive(lv.target);
  $("lvMode").onclick = e => {
    const b = e.target.closest("button"); if (!b) return;
    lv.batch = b.dataset.v === "batch"; try { localStorage.setItem("pb.scanBatch", lv.batch ? "1" : "0"); } catch (x) {}
    setMode();
  };
  $("lvAuto").onclick = () => { lv.auto = !lv.auto; $("lvAuto").textContent = "Auto " + (lv.auto ? "ON" : "OFF"); $("lvAuto").classList.toggle("on", lv.auto); lv.stableSince = performance.now(); };
  $("lvTorch").onclick = async () => {
    lv.torch = !lv.torch;
    try { await lv.track.applyConstraints({ advanced: [{ torch: lv.torch }] }); } catch (e) { lv.torch = false; }
    $("lvTorch").classList.toggle("on", lv.torch);
  };
  // video par tap = wahan focus (jin phones mein ho)
  $("lvStage").addEventListener("click", async e => {
    if (!lv.track || e.target.closest("button")) return;
    const r = $("lvStage").getBoundingClientRect(), b = videoBox();
    const x = (e.clientX - r.left - b.x) / b.w, y = (e.clientY - r.top - b.y) / b.h;
    const ring = $("lvFocus"); ring.style.left = (e.clientX - r.left) + "px"; ring.style.top = (e.clientY - r.top) + "px";
    ring.classList.remove("on"); void ring.offsetWidth; ring.classList.add("on");
    try { await lv.track.applyConstraints({ advanced: [{ pointsOfInterest: [{ x, y }], focusMode: "single-shot" }] });
          setTimeout(() => lv.track && lv.track.applyConstraints({ advanced: [{ focusMode: "continuous" }] }).catch(() => {}), 1500); } catch (err) {}
  });

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
  const isFull = cs => cs.every(([x, y], i) => x === [0, 1, 1, 0][i] && y === [0, 0, 1, 1][i]);

  function warp(page, maxOut = MAX_OUT) {
    const W = page.src.width, H = page.src.height;
    const q = page.corners.map(([x, y]) => [x * W, y * H]);
    let ow = Math.max(dist(q[0], q[1]), dist(q[3], q[2])), oh = Math.max(dist(q[0], q[3]), dist(q[1], q[2]));
    const s = Math.min(1, maxOut / Math.max(ow, oh));
    ow = Math.max(1, Math.round(ow * s)); oh = Math.max(1, Math.round(oh * s));
    const out = document.createElement("canvas"); out.width = ow; out.height = oh;
    const octx = out.getContext("2d");
    if (isFull(page.corners)) { octx.imageSmoothingQuality = "high"; octx.drawImage(page.src, 0, 0, ow, oh); return out; }
    if (!page._sd || page._sdSrc !== page.src) { page._sd = page.src.getContext("2d").getImageData(0, 0, W, H).data; page._sdSrc = page.src; }
    const sd = page._sd;
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
  // kone wahi hon to dobara seedha na karo (filter badalna turant)
  function warpCached(page) {
    const key = JSON.stringify(page.corners);
    if (page._wk !== key || !page._w) { page._w = warp(page); page._wk = key; }
    return page._w;
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
  function copyCanvas(c, max = 1e9) {
    const s = Math.min(1, max / Math.max(c.width, c.height));
    const o = document.createElement("canvas"); o.width = Math.max(1, Math.round(c.width * s)); o.height = Math.max(1, Math.round(c.height * s));
    const x = o.getContext("2d"); x.imageSmoothingQuality = "high"; x.drawImage(c, 0, 0, o.width, o.height);
    return o;
  }

  // ---------------------------------------------------------------- FILTERS (rang)
  const FILTERS = [
    { v: "magic", name: "✨ Magic" },   // rang saaf + kaagaz safed + chhaaya gayab
    { v: "orig", name: "Asli" },
    { v: "light", name: "☀ Roshan" },
    { v: "doc", name: "📄 Document" },
    { v: "gray", name: "Grey" },
    { v: "bw", name: "⚫ B&W" },
  ];
  const FNAMES = Object.fromEntries(FILTERS.map(f => [f.v, f.name]));
  function defFilter() { try { const f = localStorage.getItem("pb.scanFilter"); if (FNAMES[f]) return f; } catch (e) {} return "magic"; }

  // kaagaz ki roshni ka naksha: chhote khaano mein sabse ujla hissa (akshar/chhaaya chhodkar), phir smooth
  function paperMap(d, w, h) {
    const cs = Math.max(8, Math.round(Math.max(w, h) / 36)), gw = Math.ceil(w / cs), gh = Math.ceil(h / cs), n = gw * gh;
    const cr = new Float32Array(n), cg = new Float32Array(n), cb = new Float32Array(n), cl = new Float32Array(n);
    const hist = new Uint32Array(64), st = Math.max(1, cs >> 3);
    for (let gy = 0; gy < gh; gy++) for (let gx = 0; gx < gw; gx++) {
      hist.fill(0); let cnt = 0;
      const x1 = Math.min(w, (gx + 1) * cs), y1 = Math.min(h, (gy + 1) * cs);
      for (let y = gy * cs; y < y1; y += st) for (let x = gx * cs; x < x1; x += st) {
        const i = (y * w + x) * 4; hist[(d[i] * 77 + d[i + 1] * 150 + d[i + 2] * 29) >> 10]++; cnt++;
      }
      let k = 63, acc = 0; const want = cnt * 0.12;   // sabse ujle 12%
      while (k > 0 && acc + hist[k] < want) acc += hist[k--];
      let r = 0, g = 0, b = 0, m = 0;
      for (let y = gy * cs; y < y1; y += st) for (let x = gx * cs; x < x1; x += st) {
        const i = (y * w + x) * 4;
        if (((d[i] * 77 + d[i + 1] * 150 + d[i + 2] * 29) >> 10) >= k) { r += d[i]; g += d[i + 1]; b += d[i + 2]; m++; }
      }
      const j = gy * gw + gx; m = m || 1;
      cr[j] = r / m; cg[j] = g / m; cb[j] = b / m; cl[j] = (cr[j] * 299 + cg[j] * 587 + cb[j] * 114) / 1000;
    }
    // jo khaane kaagaz nahi (photo / rangeen patti / gehra hissa) unhe aas-paas ke kaagaz se bharo.
    // Chhaaya dheere-dheere badalti hai, photo ka kinaara achanak: is farq se pehchaante hain.
    const sorted = Array.from(cl).sort((a, b) => a - b), paper = sorted[Math.floor(n * 0.75)] || 200;
    const chroma = j => { const t = cr[j] + cg[j] + cb[j] + 1; return [cr[j] / t, cg[j] / t, cb[j] / t]; };
    const refC = [0, 1, 2].map(k => { const v = []; for (let j = 0; j < n; j++) if (cl[j] > paper * 0.8) v.push(chroma(j)[k]); v.sort((a, b) => a - b); return v[v.length >> 1] || 1 / 3; });
    let ok = new Uint8Array(n); const ref = new Float32Array(n);
    for (let j = 0; j < n; j++) {
      const ch = chroma(j), cd = Math.abs(ch[0] - refC[0]) + Math.abs(ch[1] - refC[1]) + Math.abs(ch[2] - refC[2]);
      ok[j] = cl[j] > paper * 0.55 && cd < 0.12 ? 1 : 0; ref[j] = ok[j] ? cl[j] : paper;
    }
    for (let pass = 0, changed = 1; pass < 50 && changed; pass++) {
      changed = 0;
      for (let gy = 0; gy < gh; gy++) for (let gx = 0; gx < gw; gx++) {
        const j = gy * gw + gx; if (!ok[j]) continue;
        let mx = 0;
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
          const xx = gx + dx, yy = gy + dy; if ((!dx && !dy) || xx < 0 || yy < 0 || xx >= gw || yy >= gh) continue;
          const k = yy * gw + xx, v = ok[k] ? cl[k] : ref[k]; if (v > mx) mx = v;
        }
        if (cl[j] < mx * 0.8) { ok[j] = 0; ref[j] = mx; changed = 1; }
      }
    }
    if (!ok.some(Boolean)) { ok.fill(1); }
    for (let pass = 0; pass < 60; pass++) {
      let left = 0; const nok = ok.slice();
      for (let gy = 0; gy < gh; gy++) for (let gx = 0; gx < gw; gx++) {
        const j = gy * gw + gx; if (ok[j]) continue;
        let r = 0, g = 0, b = 0, m = 0;
        for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
          const xx = gx + dx, yy = gy + dy; if (xx < 0 || yy < 0 || xx >= gw || yy >= gh) continue;
          const k = yy * gw + xx; if (ok[k]) { r += cr[k]; g += cg[k]; b += cb[k]; m++; }
        }
        if (m) { cr[j] = r / m; cg[j] = g / m; cb[j] = b / m; nok[j] = 1; } else left++;
      }
      ok = nok; if (!left) break;
    }
    // smooth + bada karo
    const small = document.createElement("canvas"); small.width = gw; small.height = gh;
    const sx = small.getContext("2d"), sid = sx.createImageData(gw, gh);
    for (let rep = 0; rep < 2; rep++) for (const a of [cr, cg, cb]) {
      const t = a.slice();
      for (let gy = 0; gy < gh; gy++) for (let gx = 0; gx < gw; gx++) {
        let s = 0, m = 0;
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
          const xx = gx + dx, yy = gy + dy; if (xx < 0 || yy < 0 || xx >= gw || yy >= gh) continue; s += t[yy * gw + xx]; m++; }
        a[gy * gw + gx] = s / m;
      }
    }
    for (let j = 0; j < n; j++) { sid.data[j * 4] = cr[j]; sid.data[j * 4 + 1] = cg[j]; sid.data[j * 4 + 2] = cb[j]; sid.data[j * 4 + 3] = 255; }
    sx.putImageData(sid, 0, 0);
    const big = document.createElement("canvas"); big.width = w; big.height = h;
    const bx = big.getContext("2d", { willReadFrequently: true }); bx.imageSmoothingEnabled = true; bx.imageSmoothingQuality = "high";
    bx.drawImage(small, 0, 0, gw * cs, gh * cs);  // har khaane ki value uske beech mein
    return bx.getImageData(0, 0, w, h).data;
  }
  function sharpen(d, w, h, a) {  // akshar ki dhaar tez (unsharp)
    const src = new Uint8ClampedArray(d), row = w * 4;
    for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
      const i = y * row + x * 4;
      for (let c = 0; c < 3; c++) {
        const v = src[i + c];
        d[i + c] = v + a * (4 * v - src[i + c - 4] - src[i + c + 4] - src[i + c - row] - src[i + c + row]);
      }
    }
  }
  function levels(d, lo, hi) {  // sabse gehra -> kaala, sabse ujla -> safed
    const hist = new Uint32Array(256); let n = 0;
    for (let i = 0; i < d.length; i += 16) { hist[(d[i] * 299 + d[i + 1] * 587 + d[i + 2] * 114) / 1000 | 0]++; n++; }
    let a = 0, s = 0; while (a < 255 && s + hist[a] < n * lo) s += hist[a++];
    let b = 255; s = 0; while (b > 0 && s + hist[b] < n * hi) s += hist[b--];
    return [a, Math.max(a + 20, b)];
  }

  function applyFilter(c, filter) {
    if (filter === "orig" || !FNAMES[filter]) return c;
    const w = c.width, h = c.height, ctx = c.getContext("2d", { willReadFrequently: true });
    const img = ctx.getImageData(0, 0, w, h), d = img.data, n = w * h;
    if (filter === "light") {          // rang wahi, bas ujla aur saaf
      const [a, b] = levels(d, 0.01, 0.02), k = 255 / (b - a);
      for (let i = 0; i < d.length; i += 4) for (let c2 = 0; c2 < 3; c2++) {
        let v = (d[i + c2] - a) * k / 255; v = v < 0 ? 0 : v > 1 ? 1 : v;
        d[i + c2] = Math.pow(v, 0.8) * 255;
      }
      sharpen(d, w, h, 0.25);
    } else if (filter === "gray") {
      const [a, b] = levels(d, 0.01, 0.01), k = 255 / (b - a);
      for (let i = 0; i < d.length; i += 4) { const v = ((d[i] * 299 + d[i + 1] * 587 + d[i + 2] * 114) / 1000 - a) * k; d[i] = d[i + 1] = d[i + 2] = v; }
    } else {
      const bg = paperMap(d, w, h);
      if (filter === "magic") {        // har rang ko kaagaz ki roshni se baanto: chhaaya + peelapan gayab, rang chatak
        for (let i = 0; i < d.length; i += 4) {
          let r = d[i] / Math.max(25, bg[i]), g = d[i + 1] / Math.max(25, bg[i + 1]), b = d[i + 2] / Math.max(25, bg[i + 2]);
          r = (r - 0.1) / 0.8; g = (g - 0.1) / 0.8; b = (b - 0.1) / 0.8;
          const l = 0.299 * r + 0.587 * g + 0.114 * b, sat = 1.35;
          r = l + (r - l) * sat; g = l + (g - l) * sat; b = l + (b - l) * sat;
          d[i] = r * 255; d[i + 1] = g * 255; d[i + 2] = b * 255;
        }
        sharpen(d, w, h, 0.35);
      } else {                          // doc (grey saaf) / bw (kaala-safed)
        const bw = filter === "bw";
        for (let i = 0; i < d.length; i += 4) {
          const l = (d[i] * 299 + d[i + 1] * 587 + d[i + 2] * 114) / 1000, pl = Math.max(25, (bg[i] * 299 + bg[i + 1] * 587 + bg[i + 2] * 114) / 1000);
          const r = l / pl;
          const v = bw ? (r - 0.74) * 2200 + 128 : (r - 0.3) * 255 / 0.62;
          d[i] = d[i + 1] = d[i + 2] = v;
        }
        if (!bw) sharpen(d, w, h, 0.4);
      }
    }
    ctx.putImageData(img, 0, 0);
    return c;
  }

  async function processPage(page) {
    const out = applyFilter(rotate(copyCanvas(warpCached(page)), page.rot), page.filter);
    page.w = out.width; page.h = out.height;
    page.blob = await new Promise(res => out.toBlob(res, "image/jpeg", page.filter === "orig" || page.filter === "light" ? 0.85 : 0.8));
    if (page.url) URL.revokeObjectURL(page.url);
    page.url = URL.createObjectURL(page.blob);
  }

  // ---------------------------------------------------------------- EDITOR: 1) Kone  2) Rang
  let ed = null;  // { list, i, onDone, corners, rot, filter, step }
  const isPages = () => ed && ed.list === pages;
  function openEditor(i, list = pages, onDone = renderGrid, step) {
    const p = list[i];
    ed = { list, i, onDone, corners: p.corners.map(c => [...c]), rot: p.rot, filter: p.filter, step: step || "filter", prev: null };
    $("edStrip").innerHTML = ""; $("edStrip").dataset.key = "";
    $("editor").classList.remove("hidden");
    showStep();
  }
  function edTitle() {
    if (isPages()) return `Page ${ed.i + 1} / ${pages.length}`;
    return ed.i === 0 ? "Card: aage ka hissa" : "Card: peeche ka hissa";
  }
  function showStep() {
    const crop = ed.step === "crop";
    $("editor").classList.toggle("crop", crop);
    $("edTitle").textContent = crop ? "✂ Kone theek kijiye" : edTitle();
    $("edDone").textContent = crop ? "Aage →" : "✓ Theek hai";
    $("edPrev").classList.toggle("hidden", crop || !isPages() || ed.i === 0);
    $("edNext").classList.toggle("hidden", crop || !isPages() || ed.i >= pages.length - 1);
    $("edMoveL").classList.toggle("hidden", !isPages() || pages.length < 2);
    $("edMoveR").classList.toggle("hidden", !isPages() || pages.length < 2);
    $("edAll").classList.toggle("hidden", !isPages() || pages.length < 2);
    $("edDots").innerHTML = isPages() && pages.length > 1 && pages.length <= 20 ? pages.map((_, k) => `<i class="${k === ed.i ? "on" : ""}"></i>`).join("") : "";
    if (!crop) { $("edCanvas").style.opacity = ".15"; $("edBusy").classList.remove("hidden"); }
    requestAnimationFrame(() => crop ? drawCrop() : drawFilter());
  }
  function fitCanvas(cv, w, h, pad = 28) {
    const st = $("edStage"), s = Math.min((st.clientWidth - pad * 2) / w, (st.clientHeight - pad * 2) / h);
    cv.style.width = Math.round(w * s) + "px"; cv.style.height = Math.round(h * s) + "px";
    cv.style.left = Math.round((st.clientWidth - w * s) / 2) + "px"; cv.style.top = Math.round((st.clientHeight - h * s) / 2) + "px";
    return s;
  }
  // ---- kone wali screen
  function drawCrop() {
    const p = ed.list[ed.i], cv = $("edCanvas"); cv.style.opacity = "1"; $("edBusy").classList.add("hidden");
    const st = $("edStage"), s0 = Math.min((st.clientWidth - 56) / p.src.width, (st.clientHeight - 56) / p.src.height);
    cv.width = Math.round(p.src.width * Math.min(1, s0 * (window.devicePixelRatio || 1)));
    cv.height = Math.round(p.src.height * Math.min(1, s0 * (window.devicePixelRatio || 1)));
    cv.getContext("2d").drawImage(p.src, 0, 0, cv.width, cv.height);
    fitCanvas(cv, p.src.width, p.src.height);
    ed.box = { x: cv.offsetLeft, y: cv.offsetTop, w: parseFloat(cv.style.width), h: parseFloat(cv.style.height) };
    placeHandles();
  }
  const MID = [[0, 1], [1, 2], [2, 3], [3, 0]];  // kinaare ke beech wale handle
  function convexOk(c) {
    let sign = 0;
    for (let i = 0; i < 4; i++) {
      const a = c[i], b = c[(i + 1) % 4], d = c[(i + 2) % 4];
      const z = (b[0] - a[0]) * (d[1] - b[1]) - (b[1] - a[1]) * (d[0] - b[0]);
      if (Math.abs(z) < 1e-6) return false;
      if (!sign) sign = Math.sign(z); else if (Math.sign(z) !== sign) return false;
    }
    return true;
  }
  function placeHandles() {
    const { x, y, w, h } = ed.box, P = ed.corners.map(([cx, cy]) => [x + cx * w, y + cy * h]);
    document.querySelectorAll("#edStage .hd").forEach(el => {
      const k = +el.dataset.k;
      const pt = k < 4 ? P[k] : [(P[MID[k - 4][0]][0] + P[MID[k - 4][1]][0]) / 2, (P[MID[k - 4][0]][1] + P[MID[k - 4][1]][1]) / 2];
      el.style.left = pt[0] + "px"; el.style.top = pt[1] + "px";
      if (k >= 4) {  // kinaare ki disha mein ghuma do
        const a = P[MID[k - 4][0]], b = P[MID[k - 4][1]];
        el.style.transform = `rotate(${Math.atan2(b[1] - a[1], b[0] - a[0])}rad)`;
      }
    });
    $("edPoly").setAttribute("points", P.map(p => p.join(",")).join(" "));
    const st = $("edStage");
    $("edShade").setAttribute("d", `M0 0H${st.clientWidth}V${st.clientHeight}H0Z M${P.map(p => p.join(" ")).join(" L")}Z`);
    const bad = !convexOk(ed.corners);
    $("editor").classList.toggle("bad", bad);
    $("edDone").disabled = ed.step === "crop" && bad;
  }
  // magnifier: ungli ke neeche ka hissa bada karke dikhao
  function drawLoupe(nx, ny) {
    const p = ed.list[ed.i], L = $("edLoupe"), lc = L.getContext("2d"), S = L.width;
    const zoom = 3.2, r = S / 2 / zoom * (p.src.width / ed.box.w);  // src pixels
    lc.fillStyle = "#000"; lc.fillRect(0, 0, S, S);
    lc.drawImage(p.src, nx * p.src.width - r, ny * p.src.height - r, r * 2, r * 2, 0, 0, S, S);
    // kone ki lineein bhi zoom mein
    const k = ed.drag, sc = S / (2 * r);
    const toL = ([cx, cy]) => [(cx * p.src.width - (nx * p.src.width - r)) * sc, (cy * p.src.height - (ny * p.src.height - r)) * sc];
    lc.strokeStyle = "#3b82f6"; lc.lineWidth = 2; lc.beginPath();
    if (k < 4) { const a = toL(ed.corners[(k + 3) % 4]), b = toL(ed.corners[k]), c = toL(ed.corners[(k + 1) % 4]); lc.moveTo(...a); lc.lineTo(...b); lc.lineTo(...c); }
    else { const [i0, i1] = MID[k - 4], a = toL(ed.corners[i0]), b = toL(ed.corners[i1]); lc.moveTo(...a); lc.lineTo(...b); }
    lc.stroke();
    lc.strokeStyle = "#fff"; lc.lineWidth = 1.5; lc.beginPath();
    lc.moveTo(S / 2 - 10, S / 2); lc.lineTo(S / 2 + 10, S / 2); lc.moveTo(S / 2, S / 2 - 10); lc.lineTo(S / 2, S / 2 + 10); lc.stroke();
    const st = $("edStage"), px = ed.box.x + nx * ed.box.w, py = ed.box.y + ny * ed.box.h;
    // ungli ke upar; agar upar jagah nahi to doosre kone mein
    let lx = px - S / 2, ly = py - S - 50;
    if (ly < 4) { ly = 8; lx = px < st.clientWidth / 2 ? st.clientWidth - S - 8 : 8; }
    lx = Math.max(4, Math.min(st.clientWidth - S - 4, lx));
    L.style.left = lx + "px"; L.style.top = ly + "px"; L.classList.remove("hidden");
  }
  $("edStage").addEventListener("pointerdown", e => {
    if (!ed || ed.step !== "crop") return;
    const el = e.target.closest(".hd"); if (!el) return;
    const r = $("edStage").getBoundingClientRect();
    ed.drag = +el.dataset.k; ed.dragStart = [e.clientX - r.left, e.clientY - r.top]; ed.dragOrig = ed.corners.map(c => [...c]);
    el.setPointerCapture(e.pointerId); el.classList.add("on"); e.preventDefault();
    const pt = ed.drag < 4 ? ed.corners[ed.drag] : mid(ed.drag);
    drawLoupe(pt[0], pt[1]);
  });
  const clamp01 = v => Math.min(1, Math.max(0, v));
  const mid = k => { const [a, b] = MID[k - 4]; return [(ed.corners[a][0] + ed.corners[b][0]) / 2, (ed.corners[a][1] + ed.corners[b][1]) / 2]; };
  $("edStage").addEventListener("pointermove", e => {
    if (!ed || ed.drag == null) return;
    const r = $("edStage").getBoundingClientRect(), { w, h } = ed.box;
    const dx = (e.clientX - r.left - ed.dragStart[0]) / w, dy = (e.clientY - r.top - ed.dragStart[1]) / h;
    const k = ed.drag;
    if (k < 4) ed.corners[k] = [clamp01(ed.dragOrig[k][0] + dx), clamp01(ed.dragOrig[k][1] + dy)];
    else for (const j of MID[k - 4]) ed.corners[j] = [clamp01(ed.dragOrig[j][0] + dx), clamp01(ed.dragOrig[j][1] + dy)];  // poora kinaara khisko
    placeHandles();
    const pt = k < 4 ? ed.corners[k] : mid(k);
    drawLoupe(pt[0], pt[1]);
  });
  const endDrag = () => {
    if (!ed) return; ed.drag = null;
    $("edLoupe").classList.add("hidden");
    document.querySelectorAll("#edStage .hd.on").forEach(h => h.classList.remove("on"));
  };
  $("edStage").addEventListener("pointerup", endDrag);
  $("edStage").addEventListener("pointercancel", endDrag);
  $("edFull").onclick = () => { ed.corners = FULL(); placeHandles(); };
  $("edAuto").onclick = () => {
    const q = window.PBDetect && window.PBDetect.detect(ed.list[ed.i].src);
    if (q) { ed.corners = q; placeHandles(); }
    else $("edTitle").textContent = "Kone apne aap nahi mile, haath se lagaiye";
  };
  $("edCropBtn").onclick = () => { ed.back = ed.corners.map(c => [...c]); ed.step = "crop"; showStep(); };

  // ---- rang wali screen (bada preview + filter ki patti)
  let fTok = 0;
  function edPage() {  // editor ki abhi ki settings wala page (asli mein badlav nahi)
    const p = ed.list[ed.i];
    return { src: p.src, corners: ed.corners, rot: ed.rot, filter: ed.filter, _sd: p._sd, _sdSrc: p._sdSrc,
             _w: p._w, _wk: p._wk, real: p };
  }
  async function drawFilter() {
    const tok = ++fTok, cv = $("edCanvas"), tmp = edPage();
    $("edBusy").classList.remove("hidden");
    await new Promise(r => setTimeout(r, 20));
    const w = warpCached(tmp);
    Object.assign(tmp.real, { _w: tmp._w, _wk: tmp._wk, _sd: tmp._sd, _sdSrc: tmp._sdSrc });  // cache bacha lo
    if (tok !== fTok || !ed) return;
    const st = $("edStage"), dpr = window.devicePixelRatio || 1;
    const prevMax = Math.min(1400, Math.max(st.clientWidth, st.clientHeight) * dpr);
    const out = applyFilter(rotate(copyCanvas(w, prevMax), ed.rot), ed.filter);
    cv.width = out.width; cv.height = out.height; cv.getContext("2d").drawImage(out, 0, 0);
    fitCanvas(cv, out.width, out.height, 16);
    cv.style.opacity = "1"; $("edBusy").classList.add("hidden");
    if (ed.onDrawn) { const f = ed.onDrawn; ed.onDrawn = null; f(); }
    renderStrip(w, tok);
  }
  async function renderStrip(w, tok) {
    const strip = $("edStrip");
    if (strip.dataset.key !== ed.list[ed.i]._wk + "|" + ed.rot + "|" + ed.i) {
      strip.dataset.key = ed.list[ed.i]._wk + "|" + ed.rot + "|" + ed.i;
      strip.innerHTML = FILTERS.map(f => `<button data-v="${f.v}" class="${f.v === ed.filter ? "on" : ""}"><canvas></canvas><span>${f.name}</span></button>`).join("");
      const on = strip.querySelector("button.on"); if (on) strip.scrollLeft = Math.max(0, on.offsetLeft - (strip.clientWidth - on.offsetWidth) / 2);
      const small = rotate(copyCanvas(w, 180), ed.rot);
      for (const [k, f] of FILTERS.entries()) {
        await new Promise(r => setTimeout(r, 0));
        if (tok !== fTok || !ed) return;
        const t = applyFilter(copyCanvas(small), f.v), c = strip.children[k].querySelector("canvas");
        c.width = t.width; c.height = t.height; c.getContext("2d").drawImage(t, 0, 0);
      }
    }
    strip.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === ed.filter));
    const on = strip.querySelector("button.on");
    if (on) strip.scrollLeft = Math.max(0, on.offsetLeft - (strip.clientWidth - on.offsetWidth) / 2);
  }
  $("edStrip").onclick = e => {
    const b = e.target.closest("button"); if (!b || !ed) return;
    ed.filter = b.dataset.v;
    try { if (isPages()) localStorage.setItem("pb.scanFilter", ed.filter); } catch (x) {}
    $("edStrip").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
    drawFilter();
  };
  $("edRotate").onclick = () => { ed.rot = (ed.rot + 1) % 4; if (ed.step === "filter") drawFilter(); else $("edTitle").textContent = `⟳ ${ed.rot * 90}° ghumega`; };
  $("edAll").onclick = async () => {
    const f = ed.filter, b = $("edAll"); b.disabled = true; b.textContent = "…";
    for (const p of pages) if (p !== ed.list[ed.i] && p.filter !== f) { p.filter = f; await processPage(p); }
    b.disabled = false; b.textContent = "✔ Sab par laga";
    setTimeout(() => { if (b.textContent.startsWith("✔")) b.textContent = "⧉ Sab pages par"; }, 1500);
  };
  async function saveEd() {
    const p = ed.list[ed.i];
    Object.assign(p, { corners: ed.corners, rot: ed.rot, filter: ed.filter });
    await processPage(p);
  }
  async function goTo(j) {
    $("edBusy").classList.remove("hidden");
    await saveEd();
    const { list, onDone } = ed;
    openEditor(j, list, onDone, "filter");
  }
  // ungli se khiskao: baayein = agla page, daayein = pichhla (CamScanner jaisa)
  let sw = null;
  $("edStage").addEventListener("pointerdown", e => {
    if (!ed || ed.step !== "filter" || !isPages() || pages.length < 2) return;
    sw = { x: e.clientX, y: e.clientY, id: e.pointerId, dx: 0 };
  });
  $("edStage").addEventListener("pointermove", e => {
    if (!sw || e.pointerId !== sw.id) return;
    sw.dx = e.clientX - sw.x;
    if (Math.abs(e.clientY - sw.y) > Math.abs(sw.dx) * 1.5 && Math.abs(sw.dx) < 20) return;
    let dx = sw.dx;
    if ((dx > 0 && ed.i === 0) || (dx < 0 && ed.i >= pages.length - 1)) dx *= 0.3;  // aakhri page par rabar jaisa
    const cv = $("edCanvas"); cv.style.transition = "none"; cv.style.transform = `translateX(${dx}px)`;
  });
  const endSwipe = e => {
    if (!sw || (e && e.pointerId !== sw.id)) return;
    const dx = sw.dx, cv = $("edCanvas"); sw = null;
    const j = dx < -60 ? ed.i + 1 : dx > 60 ? ed.i - 1 : ed.i;
    cv.style.transition = "transform .22s ease-out, opacity .15s";
    if (j === ed.i || j < 0 || j >= pages.length) { cv.style.transform = ""; return; }
    cv.style.transform = `translateX(${dx < 0 ? -window.innerWidth : window.innerWidth}px)`;
    if (navigator.vibrate) navigator.vibrate(10);
    setTimeout(async () => {
      await goTo(j);
      if (!ed) return;
      ed.onDrawn = () => {  // naya page doosri taraf se andar aaye
        const c2 = $("edCanvas"); c2.style.transition = "none"; c2.style.transform = `translateX(${dx < 0 ? 90 : -90}px)`;
        requestAnimationFrame(() => requestAnimationFrame(() => { c2.style.transition = "transform .25s ease-out, opacity .15s"; c2.style.transform = ""; }));
      };
    }, 180);
  };
  $("edStage").addEventListener("pointerup", endSwipe);
  $("edStage").addEventListener("pointercancel", endSwipe);
  $("edPrev").onclick = () => goTo(ed.i - 1);
  $("edNext").onclick = () => goTo(ed.i + 1);
  function movePage(dir) {
    const j = ed.i + dir; if (j < 0 || j >= pages.length) return;
    [pages[ed.i], pages[j]] = [pages[j], pages[ed.i]]; ed.i = j;
    $("edTitle").textContent = edTitle() + (dir < 0 ? " ⬅" : " ➡");
    $("edPrev").classList.toggle("hidden", ed.step === "crop" || ed.i === 0);
    $("edNext").classList.toggle("hidden", ed.step === "crop" || ed.i >= pages.length - 1);
    renderGrid();
  }
  $("edMoveL").onclick = () => movePage(-1);
  $("edMoveR").onclick = () => movePage(1);
  $("edDel").onclick = () => {
    if (!confirm("Ye page hata dein?")) return;
    const { list, i, onDone } = ed;
    const p = list[i]; if (p && p.url) URL.revokeObjectURL(p.url);
    if (list === pages) list.splice(i, 1); else list[i] = null;
    closeEditor(); onDone();
  };
  $("edClose").onclick = () => {
    if (ed && ed.step === "crop" && ed.back) { ed.step = "filter"; ed.corners = ed.back; ed.back = null; return showStep(); }
    closeEditor();
  };
  $("edDone").onclick = async () => {
    if (ed.step === "crop") { ed.step = "filter"; ed.back = null; return showStep(); }
    const { onDone } = ed;
    $("edDone").textContent = "…"; $("edDone").disabled = true;
    await new Promise(r => setTimeout(r, 30));
    await saveEd();
    $("edDone").disabled = false;
    closeEditor(); onDone();
  };
  function closeEditor() { $("editor").classList.add("hidden"); $("edLoupe").classList.add("hidden"); ed = null; fTok++; }
  window.addEventListener("resize", () => { if (ed) showStep(); });

  // ---------------------------------------------------------------- PDF banana (bina kisi library ke)
  // layout: [{ W, H, imgs: [{ blob, w, h }], draws: [{ img, x, y, w, h }], lines: [{ x, y, w, h }] }]  (points, neeche-baayein se)
  async function writePdf(layout, fileName) {
    const enc = new TextEncoder(), parts = [], offsets = [];
    let len = 0, next = 3;
    const push = x => { const b = typeof x === "string" ? enc.encode(x) : x; parts.push(b); len += b.length; };
    const obj = (n, body) => { offsets[n] = len; push(`${n} 0 obj\n`); body(); push("\nendobj\n"); };
    const plan = layout.map(pg => { const pid = next, cid = next + 1, iids = pg.imgs.map((_, k) => next + 2 + k); next += 2 + pg.imgs.length; return { pg, pid, cid, iids }; });
    push("%PDF-1.4\n%\xE2\xE3\xCF\xD3\n");
    obj(1, () => push("<< /Type /Catalog /Pages 2 0 R >>"));
    obj(2, () => push(`<< /Type /Pages /Kids [${plan.map(p => `${p.pid} 0 R`).join(" ")}] /Count ${plan.length} >>`));
    const f = v => v.toFixed(2);
    for (const { pg, pid, cid, iids } of plan) {
      let content = pg.draws.map(d => `q ${f(d.w)} 0 0 ${f(d.h)} ${f(d.x)} ${f(d.y)} cm /Im${d.img} Do Q`).join("\n");
      if (pg.lines && pg.lines.length)
        content += "\nq 0.6 G 0.6 w [4 3] 0 d " + pg.lines.map(l => `${f(l.x)} ${f(l.y)} ${f(l.w)} ${f(l.h)} re S`).join(" ") + " Q";
      const xo = iids.map((id, k) => `/Im${k} ${id} 0 R`).join(" ");
      obj(pid, () => push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pg.W} ${pg.H}] /Resources << /XObject << ${xo} >> >> /Contents ${cid} 0 R >>`));
      obj(cid, () => push(`<< /Length ${content.length} >>\nstream\n${content}\nendstream`));
      for (let k = 0; k < pg.imgs.length; k++) {
        const im = pg.imgs[k], jpg = new Uint8Array(await im.blob.arrayBuffer());
        obj(iids[k], () => {
          push(`<< /Type /XObject /Subtype /Image /Width ${im.w} /Height ${im.h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpg.length} >>\nstream\n`);
          push(jpg); push("\nendstream");
        });
      }
    }
    const xref = len;
    let x = `xref\n0 ${next}\n0000000000 65535 f \n`;
    for (let k = 1; k < next; k++) x += String(offsets[k]).padStart(10, "0") + " 00000 n \n";
    push(x + `trailer\n<< /Size ${next} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
    return new File(parts, fileName.replace(/[\\/:*?"<>|]+/g, "_"), { type: "application/pdf" });
  }

  function makePdf() {
    const layout = pages.map(p => {
      const land = p.w > p.h, W = land ? 842 : 595, H = land ? 595 : 842;  // A4 (points)
      const s = Math.min(W / p.w, H / p.h), w = p.w * s, h = p.h * s;
      return { W, H, imgs: [{ blob: p.blob, w: p.w, h: p.h }], draws: [{ img: 0, x: (W - w) / 2, y: (H - h) / 2, w, h }] };
    });
    return writePdf(layout, ($("scanName").value.trim() || "Scan") + ".pdf");
  }

  async function sharePdf(file, msgFn) {
    try {
      if (navigator.canShare && navigator.canShare({ files: [file] })) { await navigator.share({ files: [file], title: file.name }); return; }
    } catch (e) { if (e.name === "AbortError") return; }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(file); a.download = file.name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    msgFn("ok", `✅ ${file.name} download ho gayi`);
  }

  $("scanPrint").onclick = async () => {
    if (!pages.length) return;
    $("scanPrint").disabled = true;
    try { PB.printFile(await makePdf()); }
    catch (e) { smsg("err", "PDF nahi ban paayi"); }
    finally { $("scanPrint").disabled = false; }
  };
  $("scanShare").onclick = async () => { if (pages.length) sharePdf(await makePdf(), smsg); };

  // ---------------------------------------------------------------- ID card mode
  const CARD_MM = [85.6, 54], MM = 72 / 25.4;          // ID-1 card size (Aadhaar, PAN, DL, ATM card)
  const CARD_PX = [1011, 638];                           // 300 DPI par card
  const idPages = [null, null];                          // [aage, peeche]
  let idSets = 1;
  function idMsg(type, text) { PB.msg($("idMsg"), type, text); }
  $("openId").onclick = () => { PB.show("id"); renderId(); };
  $("idBack").onclick = () => PB.show("main");

  async function cardJpeg(page) {
    // Kone theek kiya hua page -> bilkul card ke naap (85.6 x 54) mein, aada (landscape)
    const bmp = await createImageBitmap(page.blob);
    const c = document.createElement("canvas"); c.width = CARD_PX[0]; c.height = CARD_PX[1];
    const x = c.getContext("2d"); x.imageSmoothingQuality = "high";
    if (bmp.height > bmp.width) {  // khadi photo -> ghuma do
      x.translate(c.width / 2, c.height / 2); x.rotate(-Math.PI / 2);
      x.drawImage(bmp, -c.height / 2, -c.width / 2, c.height, c.width);
    } else x.drawImage(bmp, 0, 0, c.width, c.height);
    bmp.close && bmp.close();
    return new Promise(res => c.toBlob(res, "image/jpeg", 0.9));
  }
  async function renderId() {
    for (const side of [0, 1]) {
      const p = idPages[side], el = $("idPrev" + side);
      if (p) {
        p.card = await cardJpeg(p);
        if (p.cardUrl) URL.revokeObjectURL(p.cardUrl);
        p.cardUrl = URL.createObjectURL(p.card);
        el.innerHTML = `<img src="${p.cardUrl}" alt="">`; el.classList.add("has");
      } else {
        el.innerHTML = `<span>${side ? "Peeche ka hissa <small>(zaroori nahi)</small>" : "Aage ka hissa"}</span>`; el.classList.remove("has");
      }
    }
    const any = idPages.some(Boolean);
    $("idPrint").disabled = $("idShare").disabled = !any;
    document.querySelectorAll("#idSets button").forEach(b => b.classList.toggle("on", +b.dataset.v === idSets));
  }
  document.querySelectorAll('#idScreen input[type=file]').forEach(inp => inp.onchange = async e => {
    const f = e.target.files[0], side = +e.target.dataset.side; e.target.value = "";
    if (!f) return;
    try {
      const src = await fileToCanvas(f);
      const page = { src, corners: autoCorners(src), rot: 0, filter: "orig" };
      await processPage(page);
      idPages[side] = page;
      await renderId();
      openEditor(side, idPages, renderId, "crop");
    } catch (err) { idMsg("err", "Photo khul nahi paayi"); }
  });
  ["idPrev0", "idPrev1"].forEach((id, side) => $(id).onclick = () => { if (idPages[side]) openEditor(side, idPages, renderId); });
  $("idSets").onclick = e => { const b = e.target.closest("button"); if (b) { idSets = +b.dataset.v; renderId(); } };

  function idLayout() {
    // A4 par card(s) asli size mein. Har set = aage + uske neeche peeche. 2/4 set: 2 column.
    const W = 595, H = 842, cw = CARD_MM[0] * MM, ch = CARD_MM[1] * MM, gap = 8 * MM;
    const sides = idPages.filter(Boolean);
    const imgs = sides.map(p => ({ blob: p.card, w: CARD_PX[0], h: CARD_PX[1] }));
    const setH = sides.length * ch + (sides.length - 1) * 4 * MM;
    const cols = idSets === 1 ? 1 : 2, rows = idSets === 4 ? 2 : 1;
    const totalW = cols * cw + (cols - 1) * gap, totalH = rows * setH + (rows - 1) * gap;
    const x0 = (W - totalW) / 2, top = H - 15 * MM;  // upar 15 mm chhod kar
    const draws = [], lines = [];
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
      sides.forEach((_, k) => {
        const x = x0 + c * (cw + gap), y = top - r * (setH + gap) - (k + 1) * ch - k * 4 * MM;
        draws.push({ img: k, x, y, w: cw, h: ch });
        lines.push({ x, y, w: cw, h: ch });
      });
    }
    return [{ W, H, imgs, draws, lines }];
  }
  function idName() {
    const d = new Date(), z = n => String(n).padStart(2, "0");
    return `IDcard_${d.getFullYear()}${z(d.getMonth() + 1)}${z(d.getDate())}_${z(d.getHours())}${z(d.getMinutes())}.pdf`;
  }
  $("idPrint").onclick = async () => {
    if (!idPages.some(Boolean)) return;
    $("idPrint").disabled = true;
    try {
      PB.printFile(await writePdf(idLayout(), idName()), { fit: "actual", nup: "1" },
        "🪪 ID card taiyaar hai. Asli size ke liye \"Asli size\" chun liya gaya hai. \"Print karein\" dabaiye.");
    } catch (e) { idMsg("err", "PDF nahi ban paayi"); }
    finally { $("idPrint").disabled = false; }
  };
  $("idShare").onclick = async () => { if (idPages.some(Boolean)) sharePdf(await writePdf(idLayout(), idName()), idMsg); };

  // test ke liye
  window.PBScan = { pages, idPages, lv, openLive, addImages, makePdf, processPage, openEditor, idLayout, writePdf, applyFilter, get ed() { return ed; } };
})();
