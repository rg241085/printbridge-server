// PrintBridge service worker
// 1) App ko install hone laayak banata hai (offline me bhi khul jaaye)
// 2) WhatsApp/Gallery ke "Share" se aayi files ko pakad kar app ko deta hai
const APP_CACHE = "pb-app-v13";
const SHARE_CACHE = "pb-share";
const SHELL = ["/", "/jsQR.min.js", "/docdetect.js", "/detworker.js", "/scan.js", "/tools.js", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", e => {
  self.skipWaiting();
  e.waitUntil(caches.open(APP_CACHE).then(c => c.addAll(SHELL)));
});

self.addEventListener("activate", e => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== APP_CACHE && k !== SHARE_CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return;

  // Share target: files ko cache me rakho, phir app kholo
  if (e.request.method === "POST" && url.pathname === "/share") {
    e.respondWith((async () => {
      try {
        const form = await e.request.formData();
        const files = form.getAll("files").filter(f => f && typeof f === "object" && f.size > 0);
        const cache = await caches.open(SHARE_CACHE);
        for (const k of await cache.keys()) await cache.delete(k);
        const keys = [];
        files.forEach((f, i) => keys.push(`/__shared/${Date.now()}_${i}`));
        await Promise.all(files.map((f, i) => cache.put(keys[i], new Response(f, {
          headers: { "Content-Type": f.type || "application/octet-stream", "X-Name": encodeURIComponent(f.name || `file${i}`) }
        }))));
        await cache.put("/__shared/meta", new Response(JSON.stringify(keys)));
        return Response.redirect("/?shared=1", 303);
      } catch (err) {
        return Response.redirect("/?shared=error", 303);
      }
    })());
    return;
  }

  if (e.request.method !== "GET" || url.pathname.startsWith("/api/") || url.pathname.startsWith("/ws/")) return;

  // App ki files: pehle cache se turant dikhao, peeche se naya version le aao
  const isPage = e.request.mode === "navigate";
  const key = isPage ? "/" : e.request;
  e.respondWith((async () => {
    const cache = await caches.open(APP_CACHE);
    const cached = await cache.match(key);
    const fresh = fetch(e.request).then(r => {
      if (r.ok && r.type === "basic") cache.put(key, r.clone());
      return r;
    }).catch(() => null);
    if (cached) { e.waitUntil(fresh); return cached; }
    return (await fresh) || new Response("Offline", { status: 503 });
  })());
});
