// PrintBridge - live camera ke kone alag thread mein dhoondhna (camera atke nahi)
importScripts("/docdetect.js");
onmessage = e => {
  const { id, img } = e.data, t = Date.now();
  let q = null;
  try { q = self.PBDetect.detect(img, { size: Math.max(img.width, img.height) }); } catch (err) {}
  postMessage({ id, q, ms: Date.now() - t });
};
