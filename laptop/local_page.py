"""Bina internet wala phone page (laptop ke local server se khulta hai)."""
PAGE = r'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0f766e">
<title>PrintBridge (offline)</title>
<style>
 :root{--c:#0f766e;--bg:#f3f5f9;--mut:#6b7280;--ok:#059669;--err:#dc2626}
 *{box-sizing:border-box} body{margin:0;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:#1f2937}
 header{background:var(--c);color:#fff;padding:16px 18px 40px} header h1{margin:0;font-size:20px} header p{margin:4px 0 0;opacity:.9;font-size:13px}
 main{max-width:520px;margin:-28px auto 0;padding:0 14px 30px}
 .card{background:#fff;border-radius:16px;padding:16px;margin-bottom:14px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
 .pick{display:block;border:2px dashed #99f6e4;border-radius:14px;padding:20px;text-align:center;background:#f0fdfa;cursor:pointer}
 .pick b{display:block;color:var(--c);font-size:17px;margin-top:4px} .pick input{display:none}
 ul{list-style:none;padding:0;margin:10px 0 0} li{display:flex;justify-content:space-between;gap:8px;background:var(--bg);border-radius:10px;padding:8px 10px;margin-bottom:6px;font-size:14px}
 li span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap} li span:last-child{flex-shrink:0;color:var(--mut)}
 label.r{display:block;font-size:14px;color:var(--mut);margin:12px 0 6px}
 select,input.t{width:100%;padding:12px;border-radius:10px;border:1px solid #d1d5db;font-size:16px;background:#fff}
 .seg{display:flex;border:1px solid #d1d5db;border-radius:10px;overflow:hidden} .seg button{flex:1;border:0;background:#fff;padding:11px 4px;font-size:14px;border-right:1px solid #e5e7eb}
 .seg button:last-child{border-right:0} .seg button.on{background:var(--c);color:#fff;font-weight:600}
 .row{display:flex;gap:10px} .row>div{flex:1}
 .go{width:100%;padding:16px;border:0;border-radius:14px;background:var(--c);color:#fff;font-size:18px;font-weight:600;margin-top:16px} .go:disabled{background:#9ca3af}
 .msg{margin-top:12px;padding:12px;border-radius:10px;font-size:15px;display:none;white-space:pre-line}
 .msg.ok{display:block;background:#ecfdf5;color:var(--ok)} .msg.err{display:block;background:#fef2f2;color:var(--err)} .msg.info{display:block;background:#f0fdfa;color:var(--c)}
 .tip{font-size:13px;color:var(--mut);line-height:1.5} .hidden{display:none!important}
</style></head><body>
<header><h1>📶 PrintBridge offline</h1><p id="sub">Print directly over your local Wi-Fi</p></header>
<main>
 <section class="card hidden" id="noKey"><p>On the computer, click <b>"Offline QR"</b> in the PrintBridge window and scan the QR code with your phone camera.</p></section>
 <section class="card hidden" id="app">
  <label class="pick">📄<b>Choose files</b><small class="tip">PDF, photos, Word, Excel</small>
   <input type="file" id="file" multiple accept=".pdf,image/*,.txt,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.rtf,.odt"></label>
  <ul id="list"></ul>
  <label class="r">Printer</label><select id="printer"></select>
  <div class="row">
   <div><label class="r">Copies</label><input class="t" id="copies" type="number" min="1" max="50" value="1"></div>
   <div><label class="r">Pages (blank = all)</label><input class="t" id="pages" placeholder="e.g. 1-3, 5"></div>
  </div>
  <label class="r">Color</label>
  <div class="seg" id="color"><button data-v="color" class="on">Color</button><button data-v="bw">Black &amp; white</button></div>
  <button class="go" id="go" disabled>Print</button>
  <div class="msg" id="msg"></div>
 </section>
 <p class="tip">This page works without an internet connection. When you are back online, use the full PrintBridge app for scanning, ID cards and other tools.</p>
</main>
<script>
(() => {
 const $ = id => document.getElementById(id);
 if (location.hash.includes("k=")) { localStorage.setItem("pbl.key", new URLSearchParams(location.hash.slice(1)).get("k")); history.replaceState(null, "", "/"); }
 const key = localStorage.getItem("pbl.key");
 if (!key) { $("noKey").classList.remove("hidden"); return; }
 $("app").classList.remove("hidden");
 let phone = localStorage.getItem("pbl.name");
 if (!phone) { phone = (prompt("Device name (shown on the computer):", "") || "Phone").slice(0, 30); localStorage.setItem("pbl.name", phone); }
 const msg = (t, m) => { $("msg").className = "msg " + t; $("msg").textContent = m; };
 const fmt = b => b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
 const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
 let files = [], color = "color";
 fetch("/api/info").then(r => r.json()).then(j => $("sub").textContent = "Computer: " + j.name).catch(() => {});
 fetch("/api/printers", { headers: { "X-PB-Key": key } }).then(r => r.json()).then(j => {
   if (!j.ok) return msg("err", j.error);
   $("printer").innerHTML = j.printers.map(n => `<option ${n === j.default ? "selected" : ""}>${esc(n)}</option>`).join("");
 }).catch(() => msg("err", "Cannot reach the computer. Are both devices on the same Wi-Fi network?"));
 $("file").onchange = e => { files = [...e.target.files]; e.target.value = "";
   $("list").innerHTML = files.map((f, i) => `<li><span>${esc(f.name)}</span><span id="s${i}">${fmt(f.size)}</span></li>`).join("");
   $("go").disabled = !files.length; };
 $("color").onclick = e => { const b = e.target.closest("button"); if (!b) return; color = b.dataset.v;
   $("color").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); };
 const send = f => new Promise(res => {
   const x = new XMLHttpRequest(); x.open("POST", "/api/print");
   x.setRequestHeader("X-PB-Key", key); x.setRequestHeader("X-Filename", encodeURIComponent(f.name));
   x.setRequestHeader("X-Printer", encodeURIComponent($("printer").value)); x.setRequestHeader("X-Copies", $("copies").value || "1");
   x.setRequestHeader("X-Phone", encodeURIComponent(phone));
   x.setRequestHeader("X-Options", encodeURIComponent(JSON.stringify({ pages: $("pages").value.trim() || "all", color })));
   x.onload = () => { let j = {}; try { j = JSON.parse(x.responseText); } catch (e) {} res(x.status === 200 && j.ok ? { ok: true } : { ok: false, error: j.error || "Error " + x.status }); };
   x.onerror = () => res({ ok: false, error: "Cannot reach the computer" });
   x.send(f);
 });
 $("go").onclick = async () => {
   $("go").disabled = true; const errs = []; let ok = 0;
   for (let i = 0; i < files.length; i++) {
     $("s" + i).textContent = "⏳"; msg("info", `${i + 1}/${files.length}: ${files[i].name}`);
     const r = await send(files[i]); $("s" + i).textContent = r.ok ? "✅" : "❌";
     r.ok ? ok++ : errs.push(files[i].name + ": " + r.error);
   }
   errs.length ? msg("err", errs.join("\n")) : msg("ok", `✅ ${ok} ${ok === 1 ? "file" : "files"} sent to the printer.`);
   $("go").disabled = false;
 };
})();
</script></body></html>
'''
