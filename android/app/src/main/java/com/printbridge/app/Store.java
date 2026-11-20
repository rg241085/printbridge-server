package com.printbridge.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;

import java.util.ArrayList;
import java.util.List;

import org.json.JSONArray;
import org.json.JSONObject;

/** Jude hue computers, server address aur phone ka naam (phone mein hi save). */
final class Store {
    static final String DEFAULT_SERVER = "https://printbridge.onrender.com";

    static final class Computer {
        final String id, name, token;
        Computer(String id, String name, String token) { this.id = id; this.name = name; this.token = token; }
    }

    private final SharedPreferences p;

    Store(Context ctx) { p = ctx.getSharedPreferences("printbridge", Context.MODE_PRIVATE); }

    String server() { return Api.trim(p.getString("server", DEFAULT_SERVER)); }

    void setServer(String s) { p.edit().putString("server", Api.trim(s).isEmpty() ? DEFAULT_SERVER : Api.trim(s)).apply(); }

    String deviceName() {
        String n = p.getString("name", "");
        if (n.isEmpty()) n = (Build.MANUFACTURER + " " + Build.MODEL).trim();
        return n.length() > 40 ? n.substring(0, 40) : n;
    }

    void setDeviceName(String n) { p.edit().putString("name", n.trim()).apply(); }

    List<Computer> computers() {
        List<Computer> list = new ArrayList<>();
        try {
            JSONArray a = new JSONArray(p.getString("computers", "[]"));
            for (int i = 0; i < a.length(); i++) {
                JSONObject o = a.getJSONObject(i);
                list.add(new Computer(o.getString("id"), o.optString("name", o.getString("id")), o.getString("token")));
            }
        } catch (Exception ignored) { }
        return list;
    }

    Computer find(String id) {
        for (Computer c : computers()) if (c.id.equals(id)) return c;
        return null;
    }

    void put(Computer c) {
        JSONArray a = new JSONArray();
        try {
            for (Computer x : computers()) if (!x.id.equals(c.id)) a.put(json(x));
            a.put(json(c));
        } catch (Exception ignored) { }
        p.edit().putString("computers", a.toString()).apply();
    }

    void remove(String id) {
        JSONArray a = new JSONArray();
        try { for (Computer x : computers()) if (!x.id.equals(id)) a.put(json(x)); } catch (Exception ignored) { }
        p.edit().putString("computers", a.toString()).apply();
    }

    private static JSONObject json(Computer c) throws Exception {
        JSONObject o = new JSONObject();
        o.put("id", c.id);
        o.put("name", c.name);
        o.put("token", c.token);
        return o;
    }

    /** "pb 7k3m 9qx2" -> "PB-7K3M-9QX2" */
    static String normId(String s) {
        StringBuilder b = new StringBuilder();
        for (char ch : (s == null ? "" : s).toUpperCase().toCharArray()) if (Character.isLetterOrDigit(ch)) b.append(ch);
        String t = b.toString();
        if (t.startsWith("PB")) t = t.substring(2);
        return t.length() == 8 ? "PB-" + t.substring(0, 4) + "-" + t.substring(4) : "";
    }
}
