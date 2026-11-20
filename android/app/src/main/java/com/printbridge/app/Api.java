package com.printbridge.app;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.UUID;

import org.json.JSONObject;

/** PrintBridge server se baat (wahi API jo phone ki web app use karti hai). */
final class Api {
    static final class ApiException extends Exception {
        final int code;
        ApiException(int code, String msg) { super(msg); this.code = code; }
    }

    private Api() {}

    private static JSONObject call(String server, String path, String method, byte[] body, String contentType,
                                   String token, int timeoutMs) throws ApiException {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(trim(server) + path).openConnection();
            c.setRequestMethod(method);
            c.setConnectTimeout(20000);
            c.setReadTimeout(timeoutMs);
            c.setRequestProperty("User-Agent", "PrintBridge-Android");
            if (token != null) c.setRequestProperty("X-PB-Token", token);
            if (body != null) {
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", contentType);
                c.setFixedLengthStreamingMode(body.length);
                c.getOutputStream().write(body);
            }
            int code = c.getResponseCode();
            InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
            String text = in == null ? "" : read(in);
            JSONObject j;
            try { j = text.isEmpty() ? new JSONObject() : new JSONObject(text); } catch (Exception e) { j = new JSONObject(); }
            if (code >= 400) {
                String msg = j.optString("detail", j.optString("error", ""));
                throw new ApiException(code, msg.isEmpty() ? "Server error (" + code + ")" : msg);
            }
            return j;
        } catch (ApiException e) {
            throw e;
        } catch (Exception e) {
            throw new ApiException(0, "Cannot reach the server. Check your internet connection.");
        } finally {
            if (c != null) c.disconnect();
        }
    }

    static String trim(String s) {
        s = s == null ? "" : s.trim();
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        return s;
    }

    private static String read(InputStream in) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        return out.toString("UTF-8");
    }

    private static String enc(String s) {
        try { return URLEncoder.encode(s, "UTF-8").replace("+", "%20"); } catch (Exception e) { return s; }
    }

    /** Computer ID + PIN se judo. Computer par "Allow" ke liye 90 sec tak ruk sakta hai. */
    static JSONObject pair(String server, String id, String pin, String name) throws ApiException {
        try {
            JSONObject b = new JSONObject();
            b.put("id", id);
            b.put("pin", pin);
            b.put("phone", name);
            return call(server, "/api/pair", "POST", b.toString().getBytes(StandardCharsets.UTF_8),
                    "application/json", null, 95000);
        } catch (ApiException e) {
            throw e;
        } catch (Exception e) {
            throw new ApiException(0, "Could not connect");
        }
    }

    static JSONObject device(String server, String id) throws ApiException {
        return call(server, "/api/devices/" + enc(id), "GET", null, null, null, 20000);
    }

    static JSONObject printers(String server, Store.Computer c) throws ApiException {
        return call(server, "/api/devices/" + enc(c.id) + "/printers", "GET", null, null, c.token, 40000);
    }

    static JSONObject status(String server, String jobId) throws ApiException {
        return call(server, "/api/jobs/" + enc(jobId), "GET", null, null, null, 20000);
    }

    static void cancel(String server, String jobId, String token) {
        try { call(server, "/api/jobs/" + enc(jobId) + "/cancel", "POST", new byte[0], "text/plain", token, 20000); }
        catch (ApiException ignored) { }
    }

    /** PDF bhejo. Lautata hai job_id. */
    static String upload(String server, Store.Computer c, File pdf, String fileName, String printer, int copies,
                         JSONObject options, String phone) throws ApiException {
        String b = "----pb" + UUID.randomUUID().toString().replace("-", "");
        try {
            ByteArrayOutputStream bos = new ByteArrayOutputStream((int) pdf.length() + 4096);
            DataOutputStream out = new DataOutputStream(bos);
            field(out, b, "device_id", c.id);
            field(out, b, "copies", String.valueOf(Math.max(1, Math.min(copies, 50))));
            field(out, b, "printer", printer == null ? "" : printer);
            field(out, b, "phone", phone);
            field(out, b, "options", options.toString());
            out.write(("--" + b + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\""
                    + fileName.replace("\"", "'") + "\"\r\nContent-Type: application/pdf\r\n\r\n").getBytes(StandardCharsets.UTF_8));
            try (FileInputStream in = new FileInputStream(pdf)) {
                byte[] buf = new byte[65536];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            }
            out.write(("\r\n--" + b + "--\r\n").getBytes(StandardCharsets.UTF_8));
            out.flush();
            JSONObject r = call(server, "/api/jobs", "POST", bos.toByteArray(), "multipart/form-data; boundary=" + b,
                    c.token, 300000);
            String id = r.optString("job_id", "");
            if (id.isEmpty()) throw new ApiException(0, r.optString("error", "Upload failed"));
            return id;
        } catch (ApiException e) {
            throw e;
        } catch (Exception e) {
            throw new ApiException(0, "Could not read the document to print");
        }
    }

    private static void field(DataOutputStream out, String b, String name, String value) throws Exception {
        out.write(("--" + b + "\r\nContent-Disposition: form-data; name=\"" + name + "\"\r\n\r\n").getBytes(StandardCharsets.UTF_8));
        out.write(value.getBytes(StandardCharsets.UTF_8));
        out.write("\r\n".getBytes(StandardCharsets.UTF_8));
    }
}
