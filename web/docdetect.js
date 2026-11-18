// PrintBridge - document ke 4 kone apne aap dhoondhna (auto crop)
// Bina kisi badi library ke: photo ko chhota karke, kaagaz ko background se alag karte hain,
// sabse bada hissa chunte hain, uska bahari gherav (convex hull) nikal kar sabse bada chaukor (4 kone) lete hain.
//   PBDetect.detect(canvas | ImageBitmap | video)  ->  [[x,y] x4] (0..1, TL TR BR BL)  ya  null
(() => {
  const SIZE = 360;  // itne pixel par kaam (tez + kaafi sahi)

  function toImageData(src, size = SIZE) {
    const sw = src.videoWidth || src.width, sh = src.videoHeight || src.height;
    const s = size / Math.max(sw, sh);
    const w = Math.max(8, Math.round(sw * s)), h = Math.max(8, Math.round(sh * s));
    const c = document.createElement("canvas"); c.width = w; c.height = h;
    const x = c.getContext("2d", { willReadFrequently: true });
    x.drawImage(src, 0, 0, w, h);
    return x.getImageData(0, 0, w, h);
  }

  function otsu(vals, n) {
    const hist = new Float64Array(256);
    for (let i = 0; i < n; i++) hist[vals[i]]++;
    let sum = 0; for (let i = 0; i < 256; i++) sum += i * hist[i];
    let sumB = 0, wB = 0, best = 0, t = 0;
    for (let i = 0; i < 256; i++) {
      wB += hist[i]; if (!wB) continue;
      const wF = n - wB; if (!wF) break;
      sumB += i * hist[i];
      const mB = sumB / wB, mF = (sum - sumB) / wF, between = wB * wF * (mB - mF) * (mB - mF);
      if (between > best) { best = between; t = i; }
    }
    return t;
  }

  function blur(a, w, h) {  // 3x3 box blur
    const o = new Uint8Array(a.length);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      let s = 0, c = 0;
      for (let dy = -1; dy <= 1; dy++) { const yy = y + dy; if (yy < 0 || yy >= h) continue;
        for (let dx = -1; dx <= 1; dx++) { const xx = x + dx; if (xx < 0 || xx >= w) continue; s += a[yy * w + xx]; c++; } }
      o[y * w + x] = s / c;
    }
    return o;
  }

  function morph(m, w, h, dilate) {
    const o = new Uint8Array(m.length);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      let v = dilate ? 0 : 1;
      for (let dy = -1; dy <= 1 && (dilate ? !v : v); dy++) { const yy = y + dy;
        for (let dx = -1; dx <= 1; dx++) { const xx = x + dx;
          const p = (yy < 0 || yy >= h || xx < 0 || xx >= w) ? 0 : m[yy * w + xx];
          if (dilate && p) { v = 1; break; } if (!dilate && !p) { v = 0; break; } } }
      o[y * w + x] = v;
    }
    return o;
  }

  function largestComponent(m, w, h) {
    const lab = new Int32Array(m.length), stack = new Int32Array(m.length);
    let best = -1, bestSize = 0, id = 0;
    for (let i = 0; i < m.length; i++) {
      if (!m[i] || lab[i]) continue;
      id++; let sp = 0, size = 0; stack[sp++] = i; lab[i] = id;
      while (sp) {
        const p = stack[--sp]; size++;
        const x = p % w, y = (p - x) / w;
        if (x > 0 && m[p - 1] && !lab[p - 1]) { lab[p - 1] = id; stack[sp++] = p - 1; }
        if (x < w - 1 && m[p + 1] && !lab[p + 1]) { lab[p + 1] = id; stack[sp++] = p + 1; }
        if (y > 0 && m[p - w] && !lab[p - w]) { lab[p - w] = id; stack[sp++] = p - w; }
        if (y < h - 1 && m[p + w] && !lab[p + w]) { lab[p + w] = id; stack[sp++] = p + w; }
      }
      if (size > bestSize) { bestSize = size; best = id; }
    }
    return { lab, id: best, size: bestSize };
  }

  function hull(pts) {  // monotone chain
    pts.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const lo = [], up = [];
    for (const p of pts) { while (lo.length >= 2 && cross(lo[lo.length - 2], lo[lo.length - 1], p) <= 0) lo.pop(); lo.push(p); }
    for (let i = pts.length - 1; i >= 0; i--) { const p = pts[i]; while (up.length >= 2 && cross(up[up.length - 2], up[up.length - 1], p) <= 0) up.pop(); up.push(p); }
    up.pop(); lo.pop(); return lo.concat(up);
  }

  const area = q => { let s = 0; for (let i = 0; i < q.length; i++) { const a = q[i], b = q[(i + 1) % q.length]; s += a[0] * b[1] - b[0] * a[1]; } return Math.abs(s) / 2; };

  function bestQuad(H) {
    // shuruaat: TL=min(x+y), TR=max(x-y), BR=max(x+y), BL=min(x-y); phir har kone ko behtar hull point se badlo
    const pick = f => H.reduce((b, p) => (f(p) > f(b) ? p : b), H[0]);
    let q = [pick(p => -(p[0] + p[1])), pick(p => p[0] - p[1]), pick(p => p[0] + p[1]), pick(p => p[1] - p[0])];
    for (let pass = 0; pass < 3; pass++) {
      for (let k = 0; k < 4; k++) {
        let bestA = area(q), bestP = q[k];
        for (const p of H) { const t = q.slice(); t[k] = p; const a = area(t); if (a > bestA) { bestA = a; bestP = p; } }
        q[k] = bestP;
      }
    }
    return q;
  }

  function valid(q, w, h) {
    const a = area(q) / (w * h);
    if (a < 0.08 || a > 0.995) return false;
    for (let i = 0; i < 4; i++) {  // har kona 25-155 degree
      const p = q[(i + 3) % 4], c = q[i], n = q[(i + 1) % 4];
      const v1 = [p[0] - c[0], p[1] - c[1]], v2 = [n[0] - c[0], n[1] - c[1]];
      const cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (Math.hypot(...v1) * Math.hypot(...v2) || 1);
      const ang = Math.acos(Math.max(-1, Math.min(1, cos))) * 180 / Math.PI;
      if (ang < 25 || ang > 155) return false;
    }
    return true;
  }

  function fromMask(mask, w, h) {
    let m = mask;
    m = morph(m, w, h, true); m = morph(m, w, h, true); m = morph(m, w, h, false); m = morph(m, w, h, false);  // close: akshar ke chhed bharo
    m = morph(m, w, h, false); m = morph(m, w, h, true);                                                   // open: chhote dhabbe hatao
    const { lab, id, size } = largestComponent(m, w, h);
    if (id < 0 || size < w * h * 0.08) return null;
    const pts = [];
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const i = y * w + x; if (lab[i] !== id) continue;
      if (x === 0 || y === 0 || x === w - 1 || y === h - 1 || lab[i - 1] !== id || lab[i + 1] !== id || lab[i - w] !== id || lab[i + w] !== id)
        pts.push([x + 0.5, y + 0.5]);
    }
    if (pts.length < 8) return null;
    const H = hull(pts);
    if (H.length < 4) return null;
    const q = bestQuad(H);
    return valid(q, w, h) ? { q, score: size / (w * h) } : null;
  }

  // Grayscale closing: pehle max (safed phailao) phir min. Kaagaz par likhe gehre akshar mit jaate hain,
  // kaagaz ka kinara bacha rehta hai. (document scanners wali chaal)
  function grayMorph(a, w, h, isMax) {
    const o = new Uint8Array(a.length);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      let v = isMax ? 0 : 255;
      for (let dy = -1; dy <= 1; dy++) { const yy = y + dy; if (yy < 0 || yy >= h) continue;
        for (let dx = -1; dx <= 1; dx++) { const xx = x + dx; if (xx < 0 || xx >= w) continue;
          const p = a[yy * w + xx]; if (isMax ? p > v : p < v) v = p; } }
      o[y * w + x] = v;
    }
    return o;
  }
  function closeText(gray, w, h, r = 3) {
    let g = gray;
    for (let i = 0; i < r; i++) g = grayMorph(g, w, h, true);
    for (let i = 0; i < r; i++) g = grayMorph(g, w, h, false);
    return g;
  }

  // ---------------------------------------------------------------- kinaaron ki lineein (Sobel + Hough)
  function edgeMap(chans, w, h) {
    // chans: ek ya zyada channel (gray, ya R G B). Har pixel par sabse tez kinara.
    const mag = new Float32Array(w * h);
    for (const ch of chans) {
      const g = blur(blur(ch, w, h), w, h);
      for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
        const i = y * w + x;
        const gx = -g[i - w - 1] - 2 * g[i - 1] - g[i + w - 1] + g[i - w + 1] + 2 * g[i + 1] + g[i + w + 1];
        const gy = -g[i - w - 1] - 2 * g[i - w] - g[i - w + 1] + g[i + w - 1] + 2 * g[i + w] + g[i + w + 1];
        const m = Math.hypot(gx, gy); if (m > mag[i]) mag[i] = m;
      }
    }
    // sirf sabse tez 7% kinaare
    const sorted = Float32Array.from(mag).sort(), thr = Math.max(25, sorted[Math.floor(sorted.length * 0.93)]);
    const e = new Uint8Array(w * h);
    for (let i = 0; i < e.length; i++) e[i] = mag[i] >= thr ? 1 : 0;
    return e;
  }

  function houghLines(e, w, h) {
    const diag = Math.ceil(Math.hypot(w, h)), NT = 180, NR = 2 * diag + 1, acc = new Uint32Array(NT * NR);
    const cos = new Float32Array(NT), sin = new Float32Array(NT);
    for (let t = 0; t < NT; t++) { cos[t] = Math.cos(t * Math.PI / NT); sin[t] = Math.sin(t * Math.PI / NT); }
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      if (!e[y * w + x]) continue;
      for (let t = 0; t < NT; t++) acc[t * NR + Math.round(x * cos[t] + y * sin[t]) + diag]++;
    }
    const lines = [], minVotes = Math.min(w, h) * 0.18;
    for (let k = 0; k < 24; k++) {
      let best = 0, bi = -1;
      for (let i = 0; i < acc.length; i++) if (acc[i] > best) { best = acc[i]; bi = i; }
      if (best < minVotes) break;
      const t = Math.floor(bi / NR), r = bi % NR - diag;
      lines.push({ t, theta: t * Math.PI / NT, rho: r, votes: best });
      for (let dt = -6; dt <= 6; dt++) for (let dr = -10; dr <= 10; dr++) {  // aas-paas ki milti-julti lineein hatao
        let tt = t + dt, rr = r + dr;
        if (tt < 0) { tt += NT; rr = -rr; } else if (tt >= NT) { tt -= NT; rr = -rr; }
        const ri = rr + diag; if (ri >= 0 && ri < NR) acc[tt * NR + ri] = 0;
      }
    }
    return lines;
  }

  function intersect(a, b) {
    const d = Math.cos(a.theta) * Math.sin(b.theta) - Math.sin(a.theta) * Math.cos(b.theta);
    if (Math.abs(d) < 1e-6) return null;
    return [(a.rho * Math.sin(b.theta) - b.rho * Math.sin(a.theta)) / d, (b.rho * Math.cos(a.theta) - a.rho * Math.cos(b.theta)) / d];
  }

  function order(q) {  // TL TR BR BL
    const c = [q.reduce((s, p) => s + p[0], 0) / 4, q.reduce((s, p) => s + p[1], 0) / 4];
    const srt = q.slice().sort((a, b) => Math.atan2(a[1] - c[1], a[0] - c[0]) - Math.atan2(b[1] - c[1], b[0] - c[0]));
    let k = 0; for (let i = 1; i < 4; i++) if (srt[i][0] + srt[i][1] < srt[k][0] + srt[k][1]) k = i;
    return [0, 1, 2, 3].map(i => srt[(k + i) % 4]);
  }

  function support(q, e, w, h) {
    // quad ke kinaaron par kitne % jagah sach mein photo ka kinara hai (1 pixel ki chhoot)
    let hit = 0, tot = 0;
    for (let i = 0; i < 4; i++) {
      const a = q[i], b = q[(i + 1) % 4], len = Math.hypot(b[0] - a[0], b[1] - a[1]), n = Math.max(10, Math.round(len));
      for (let k = 2; k < n - 2; k++) {
        const x = Math.round(a[0] + (b[0] - a[0]) * k / n), y = Math.round(a[1] + (b[1] - a[1]) * k / n);
        tot++;
        let on = false;
        for (let dy = -1; dy <= 1 && !on; dy++) for (let dx = -1; dx <= 1; dx++) {
          const xx = x + dx, yy = y + dy;
          if (xx >= 0 && yy >= 0 && xx < w && yy < h && e[yy * w + xx]) { on = true; break; }
        }
        if (on) hit++;
      }
    }
    return tot ? hit / tot : 0;
  }

  function houghQuads(e, w, h) {
    const L = houghLines(e, w, h);
    const horiz = L.filter(l => Math.abs(l.t - 90) <= 40).slice(0, 8), vert = L.filter(l => l.t <= 40 || l.t >= 140).slice(0, 8);
    const out = [];
    for (let a = 0; a < horiz.length; a++) for (let b = a + 1; b < horiz.length; b++)
      for (let c = 0; c < vert.length; c++) for (let d = c + 1; d < vert.length; d++) {
        const pts = [intersect(horiz[a], vert[c]), intersect(horiz[a], vert[d]), intersect(horiz[b], vert[d]), intersect(horiz[b], vert[c])];
        if (pts.some(p => !p || p[0] < -w * 0.03 || p[1] < -h * 0.03 || p[0] > w * 1.03 || p[1] > h * 1.03)) continue;
        const q = order(pts);
        if (valid(q, w, h)) out.push(q);
      }
    return out;
  }

  function detect(src, opts = {}) {
    // opts.fast: live camera ke liye (chhota size, sirf gray). Photo ke baad: poora (rangeen kinaare bhi).
    try {
      const img = toImageData(src, opts.fast ? 240 : SIZE), { width: w, height: h, data: d } = img, n = w * h;
      // background ka rang: kinaaron ka median
      const bw = Math.max(2, Math.round(Math.min(w, h) * 0.04)), R = [], G = [], B = [];
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        if (x >= bw && x < w - bw && y >= bw && y < h - bw) continue;
        const i = (y * w + x) * 4; R.push(d[i]); G.push(d[i + 1]); B.push(d[i + 2]);
      }
      const med = a => { a.sort((x, y) => x - y); return a[a.length >> 1]; };
      const br = med(R), bg = med(G), bb = med(B);
      const dist = new Uint8Array(n), gray = new Uint8Array(n);
      const rC = opts.fast ? null : new Uint8Array(n), gC = opts.fast ? null : new Uint8Array(n), bC = opts.fast ? null : new Uint8Array(n);
      for (let i = 0, j = 0; j < n; i += 4, j++) {
        if (rC) { rC[j] = d[i]; gC[j] = d[i + 1]; bC[j] = d[i + 2]; }
        dist[j] = Math.min(255, (Math.abs(d[i] - br) + Math.abs(d[i + 1] - bg) + Math.abs(d[i + 2] - bb)) / 2);
        gray[j] = (d[i] * 299 + d[i + 1] * 587 + d[i + 2] * 114) / 1000;
      }
      const tries = [];
      // 1) background se farq (kisi bhi rang ki table par)
      const db = blur(blur(dist, w, h), w, h), t1 = otsu(db, n);
      if (t1 > 12) { const m = new Uint8Array(n); for (let i = 0; i < n; i++) m[i] = db[i] > t1 ? 1 : 0; const r = fromMask(m, w, h); if (r) tries.push(r); }
      const closed = closeText(gray, w, h);
      // 2) roshni (safed kaagaz gehri jagah par), akshar mita kar
      const gb = blur(blur(closed, w, h), w, h), t2 = otsu(gb, n);
      { const m = new Uint8Array(n); for (let i = 0; i < n; i++) m[i] = gb[i] > t2 ? 1 : 0; const r = fromMask(m, w, h); if (r) tries.push(r); }
      // 3) kinaaron ki seedhi lineein (halki table / chhaaya mein bhi)
      const e = edgeMap(opts.fast ? [closed] : [closeText(rC, w, h), closeText(gC, w, h), closeText(bC, w, h)], w, h);
      const cands = tries.map(t => order(t.q)).concat(houghQuads(e, w, h));
      if (!cands.length) return null;
      // sabse achha: jiske kinaare photo ki lineon par baithe (support) aur jo bada ho
      let best = null, bestScore = 0;
      for (const q of cands) {
        const sup = support(q, e, w, h), score = sup * Math.pow(area(q) / (w * h), 0.35);
        if (sup >= 0.45 && score > bestScore) { bestScore = score; best = q; }
      }
      if (!best) return null;
      return best.map(([x, y]) => [Math.min(1, Math.max(0, x / w)), Math.min(1, Math.max(0, y / h))]);
    } catch (e) { return null; }
  }

  function debug(src) {
    const img = toImageData(src), { width: w, height: h, data: d } = img, n = w * h, gray = new Uint8Array(n);
    for (let i = 0, j = 0; j < n; i += 4, j++) gray[j] = (d[i] * 299 + d[i + 1] * 587 + d[i + 2] * 114) / 1000;
    const e = edgeMap([closeText(gray, w, h)], w, h), L = houghLines(e, w, h), hq = houghQuads(e, w, h);
    const ecount = e.reduce((a, b) => a + b, 0);
    const sup = hq.map(q => support(q, e, w, h)).sort((a, b) => b - a).slice(0, 5);
    return { w, h, edges: ecount, lines: L.map(l => [l.t, l.rho, l.votes]).slice(0, 10), hq: hq.length, topSupport: sup,
             edgeImg: Array.from(e) };
  }
  window.PBDetect = { detect, debug };
})();
