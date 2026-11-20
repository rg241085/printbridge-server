package com.printbridge.app;

import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.ParcelFileDescriptor;
import android.print.PrintAttributes;
import android.print.PrintJobId;
import android.print.PrintJobInfo;
import android.print.PrinterCapabilitiesInfo;
import android.print.PrinterId;
import android.print.PrinterInfo;
import android.printservice.PrintDocument;
import android.printservice.PrintJob;
import android.printservice.PrintService;
import android.printservice.PrinterDiscoverySession;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Android ki print screen ka "PrintBridge" printer.
 * Har jude hue computer ke har printer ko ek printer ki tarah dikhata hai. Print dabane par Android PDF banata hai,
 * ye service use server ke raaste computer ko bhejti hai, aur chhapne tak status dikhati hai.
 */
public class BridgePrintService extends PrintService {
    private static final String SEP = "\n";
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Map<PrintJobId, String> remoteIds = new HashMap<>();
    private final Map<PrintJobId, String> remoteTokens = new HashMap<>();

    @Override
    protected PrinterDiscoverySession onCreatePrinterDiscoverySession() {
        return new Session();
    }

    // ------------------------------------------------------------------ printers dhoondhna
    private final class Session extends PrinterDiscoverySession {
        private volatile boolean active;

        @Override
        public void onStartPrinterDiscovery(List<PrinterId> priorityList) {
            active = true;
            refresh();
        }

        @Override
        public void onStopPrinterDiscovery() { active = false; }

        @Override
        public void onValidatePrinters(List<PrinterId> printerIds) { }

        @Override
        public void onStartPrinterStateTracking(PrinterId printerId) { refresh(); }

        @Override
        public void onStopPrinterStateTracking(PrinterId printerId) { }

        @Override
        public void onDestroy() { active = false; }

        private void refresh() {
            final Store store = new Store(BridgePrintService.this);
            new Thread(() -> {
                List<PrinterInfo> found = new ArrayList<>();
                List<Store.Computer> comps = store.computers();
                if (comps.isEmpty()) {
                    PrinterId pid = generatePrinterId("setup");
                    found.add(new PrinterInfo.Builder(pid, "PrintBridge — open the app to connect a computer",
                            PrinterInfo.STATUS_UNAVAILABLE).build());
                }
                for (Store.Computer c : comps) {
                    try {
                        JSONObject r = Api.printers(store.server(), c);
                        JSONArray names = r.optJSONArray("printers");
                        String def = r.optString("default", "");
                        JSONObject status = r.optJSONObject("status");
                        JSONObject caps = r.optJSONObject("caps");
                        if (names == null || names.length() == 0) {
                            PrinterId pid = generatePrinterId(c.id + SEP);
                            found.add(new PrinterInfo.Builder(pid, c.name + " — no printer found",
                                    PrinterInfo.STATUS_UNAVAILABLE).build());
                            continue;
                        }
                        for (int i = 0; i < names.length(); i++) {
                            String n = names.getString(i);
                            PrinterId pid = generatePrinterId(c.id + SEP + n);
                            boolean offline = status != null && "offline".equals(status.optString(n, ""));
                            String label = n + (n.equals(def) ? " ★" : "");
                            PrinterInfo.Builder b = new PrinterInfo.Builder(pid, label,
                                    offline ? PrinterInfo.STATUS_UNAVAILABLE : PrinterInfo.STATUS_IDLE)
                                    .setDescription("PrintBridge • " + c.name);
                            b.setCapabilities(capabilities(pid, caps == null ? null : caps.optJSONObject(n)));
                            found.add(b.build());
                        }
                    } catch (Api.ApiException e) {
                        PrinterId pid = generatePrinterId(c.id + SEP);
                        String why = e.code == 401 ? "connect again in the app" : "computer offline";
                        found.add(new PrinterInfo.Builder(pid, c.name + " (" + why + ")",
                                PrinterInfo.STATUS_UNAVAILABLE).setDescription("PrintBridge").build());
                    } catch (Exception e) {
                        // ek computer ki gadbad se baaki na rukein
                    }
                }
                main.post(() -> {
                    if (!active || isDestroyed()) return;
                    List<PrinterId> stale = new ArrayList<>();
                    for (PrinterInfo old : getPrinters()) {
                        boolean keep = false;
                        for (PrinterInfo f : found) if (f.getId().equals(old.getId())) keep = true;
                        if (!keep) stale.add(old.getId());
                    }
                    if (!stale.isEmpty()) removePrinters(stale);
                    addPrinters(found);
                });
            }).start();
        }
    }

    /** Printer kya-kya kar sakta hai: kaagaz ke size, color/B&W, dono taraf. */
    private PrinterCapabilitiesInfo capabilities(PrinterId pid, JSONObject caps) {
        PrinterCapabilitiesInfo.Builder b = new PrinterCapabilitiesInfo.Builder(pid);
        b.addMediaSize(PrintAttributes.MediaSize.ISO_A4, true);
        b.addMediaSize(PrintAttributes.MediaSize.NA_LETTER, false);
        b.addMediaSize(PrintAttributes.MediaSize.NA_LEGAL, false);
        b.addMediaSize(PrintAttributes.MediaSize.ISO_A5, false);
        b.addMediaSize(PrintAttributes.MediaSize.ISO_A3, false);
        b.addResolution(new PrintAttributes.Resolution("300dpi", "300 dpi", 300, 300), true);
        b.setColorModes(PrintAttributes.COLOR_MODE_COLOR | PrintAttributes.COLOR_MODE_MONOCHROME,
                PrintAttributes.COLOR_MODE_COLOR);
        b.setMinMargins(PrintAttributes.Margins.NO_MARGINS);
        if (Build.VERSION.SDK_INT >= 23) {
            boolean duplex = caps != null && caps.optBoolean("duplex", false);
            if (duplex) {
                b.setDuplexModes(PrintAttributes.DUPLEX_MODE_NONE | PrintAttributes.DUPLEX_MODE_LONG_EDGE
                        | PrintAttributes.DUPLEX_MODE_SHORT_EDGE, PrintAttributes.DUPLEX_MODE_NONE);
            } else {
                b.setDuplexModes(PrintAttributes.DUPLEX_MODE_NONE, PrintAttributes.DUPLEX_MODE_NONE);
            }
        }
        return b.build();
    }

    // ------------------------------------------------------------------ print job
    @Override
    protected void onPrintJobQueued(final PrintJob job) {
        if (!job.isQueued()) return;
        job.start();
        final PrintJobInfo info = job.getInfo();
        final PrinterId pid = info.getPrinterId();
        final String local = pid == null ? "" : pid.getLocalId();
        int sep = local.indexOf(SEP);
        final String compId = sep >= 0 ? local.substring(0, sep) : local;
        final String printerName = sep >= 0 ? local.substring(sep + 1) : "";
        final Store store = new Store(this);
        final Store.Computer comp = store.find(compId);
        if (comp == null) {
            job.fail("This computer is no longer connected. Open PrintBridge Printer and connect it again.");
            return;
        }
        final JSONObject opts = options(info);
        final int copies = Math.max(1, info.getCopies());
        PrintDocument doc = job.getDocument();
        final String fileName = fileName(doc);
        final ParcelFileDescriptor data = doc.getData();
        if (data == null) {
            job.fail("The document could not be read");
            return;
        }
        final PrintJobId jid = job.getId();
        new Thread(() -> {
            File tmp = new File(getCacheDir(), "job_" + System.currentTimeMillis() + ".pdf");
            try {
                try (InputStream in = new FileInputStream(data.getFileDescriptor());
                     OutputStream out = new FileOutputStream(tmp)) {
                    byte[] buf = new byte[65536];
                    int n;
                    while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                } finally {
                    try { data.close(); } catch (Exception ignored) { }
                }
                String remote = Api.upload(store.server(), comp, tmp, fileName, printerName, copies, opts,
                        store.deviceName());
                main.post(() -> { remoteIds.put(jid, remote); remoteTokens.put(jid, comp.token); });
                // chhapne tak status dekhte raho
                long end = System.currentTimeMillis() + 10 * 60 * 1000;
                String last = "";
                while (System.currentTimeMillis() < end) {
                    Thread.sleep(1500);
                    JSONObject st;
                    try {
                        st = Api.status(store.server(), remote);
                    } catch (Api.ApiException e) {
                        if (e.code == 404) { finish(job, null); return; }   // server ne saaf kar diya = ho chuka
                        continue;
                    }
                    String s = st.optString("status", "");
                    String msg = st.optString("message", "");
                    if (!msg.equals(last)) {
                        last = msg;
                        final String m = msg;
                        main.post(() -> { if (job.isStarted() && Build.VERSION.SDK_INT >= 24) job.setStatus(m); });
                    }
                    if (s.equals("done")) { finish(job, null); return; }
                    if (s.equals("error")) { finish(job, msg.isEmpty() ? "Printing failed" : msg); return; }
                    if (s.equals("cancelled")) { main.post(() -> { if (!job.isCompleted() && !job.isCancelled()) job.cancel(); }); return; }
                }
                finish(job, "No confirmation from the computer. Please check the printer.");
            } catch (Api.ApiException e) {
                finish(job, e.getMessage());
            } catch (Exception e) {
                finish(job, "Printing failed: " + e.getMessage());
            } finally {
                //noinspection ResultOfMethodCallIgnored
                tmp.delete();
            }
        }).start();
    }

    private void finish(final PrintJob job, final String error) {
        main.post(() -> {
            remoteIds.remove(job.getId());
            remoteTokens.remove(job.getId());
            if (job.isCompleted() || job.isCancelled() || job.isFailed()) return;
            if (error == null) job.complete(); else job.fail(error);
        });
    }

    @Override
    protected void onRequestCancelPrintJob(final PrintJob job) {
        final String remote = remoteIds.get(job.getId());
        final String token = remoteTokens.get(job.getId());
        final Store store = new Store(this);
        if (remote != null) new Thread(() -> Api.cancel(store.server(), remote, token)).start();
        job.cancel();
    }

    /** Android ki settings -> PrintBridge ki settings (copies, B&W, dono taraf, kaagaz). */
    private static JSONObject options(PrintJobInfo info) {
        JSONObject o = new JSONObject();
        try {
            PrintAttributes a = info.getAttributes();
            o.put("pages", "all");   // Android chune hue page hi PDF mein bhejta hai
            o.put("color", a.getColorMode() == PrintAttributes.COLOR_MODE_MONOCHROME ? "bw" : "color");
            String paper = "default";
            PrintAttributes.MediaSize ms = a.getMediaSize();
            if (ms != null) {
                String id = ms.getId();
                if (id.startsWith("ISO_A4")) paper = "a4";
                else if (id.startsWith("NA_LETTER")) paper = "letter";
                else if (id.startsWith("NA_LEGAL")) paper = "legal";
                else if (id.startsWith("ISO_A5")) paper = "a5";
                else if (id.startsWith("ISO_A3")) paper = "a3";
            }
            o.put("paper", paper);
            String duplex = "off";
            if (Build.VERSION.SDK_INT >= 23) {
                int d = a.getDuplexMode();
                if (d == PrintAttributes.DUPLEX_MODE_LONG_EDGE) duplex = "long";
                else if (d == PrintAttributes.DUPLEX_MODE_SHORT_EDGE) duplex = "short";
            }
            o.put("duplex", duplex);
            o.put("fit", "fit");
            o.put("nup", 1);
        } catch (Exception ignored) { }
        return o;
    }

    private static String fileName(PrintDocument doc) {
        String n = "Document";
        try {
            if (doc.getInfo() != null && doc.getInfo().getName() != null) n = doc.getInfo().getName();
        } catch (Exception ignored) { }
        n = n.replaceAll("[\\\\/:*?\"<>|]+", "_").trim();
        if (n.isEmpty()) n = "Document";
        if (n.length() > 80) n = n.substring(0, 80);
        return n.toLowerCase().endsWith(".pdf") ? n : n + ".pdf";
    }
}
