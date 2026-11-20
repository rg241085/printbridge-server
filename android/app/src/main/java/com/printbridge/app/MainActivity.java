package com.printbridge.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.InputFilter;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONObject;

/** Setup screen: printer service chalu karo, computer jodo, phir kisi bhi app se print. */
public class MainActivity extends Activity {
    private static final int BLUE = 0xFF1A56DB, BG = 0xFFF3F4F6, TEXT = 0xFF111827, MUT = 0xFF6B7280,
            GREEN = 0xFF059669, RED = 0xFFDC2626, LINE = 0xFFE5E7EB, SOFT = 0xFFEEF2FF;
    private final Handler main = new Handler(Looper.getMainLooper());
    private Store store;
    private LinearLayout list;
    private TextView serviceState, pairMsg;
    private EditText idIn, pinIn, nameIn;
    private Button pairBtn;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        store = new Store(this);
        Window w = getWindow();
        if (Build.VERSION.SDK_INT >= 21) w.setStatusBarColor(BLUE);

        ScrollView sv = new ScrollView(this);
        sv.setBackgroundColor(BG);
        sv.setFillViewport(true);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        sv.addView(root);

        // ---------------- header
        LinearLayout head = new LinearLayout(this);
        head.setOrientation(LinearLayout.VERTICAL);
        head.setBackgroundColor(BLUE);
        head.setPadding(dp(20), dp(22), dp(20), dp(26));
        head.addView(text("PrintBridge Printer", 22, Color.WHITE, true));
        TextView sub = text("Print from any app to the printer connected to your computer", 14, 0xFFDBEAFE, false);
        sub.setPadding(0, dp(4), 0, 0);
        head.addView(sub);
        root.addView(head);

        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(14), dp(14), dp(14), dp(24));
        root.addView(body);

        // ---------------- 1. service
        LinearLayout c1 = card(body, "1. Turn on the printer service");
        c1.addView(text("Android needs your permission once to show PrintBridge in the print menu.", 14, MUT, false));
        serviceState = text("", 14, TEXT, true);
        serviceState.setPadding(0, dp(10), 0, 0);
        c1.addView(serviceState);
        Button open = button("Open print settings", true);
        open.setOnClickListener(v -> openPrintSettings());
        c1.addView(open, lp(-1, -2, 12));
        c1.addView(text("Tap “PrintBridge Printer” and switch it on.", 13, MUT, false), lp(-1, -2, 6));

        // ---------------- 2. computers
        LinearLayout c2 = card(body, "2. Connect your computer");
        c2.addView(text("Open PrintBridge on your computer. It shows a Computer ID and a PIN.", 14, MUT, false));
        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        c2.addView(list, lp(-1, -2, 8));
        idIn = input("Computer ID (e.g. PB-7K3M-9QX2)", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS);
        idIn.setFilters(new InputFilter[]{new InputFilter.LengthFilter(14), new InputFilter.AllCaps()});
        c2.addView(idIn, lp(-1, -2, 10));
        pinIn = input("6-digit PIN", InputType.TYPE_CLASS_NUMBER);
        pinIn.setFilters(new InputFilter[]{new InputFilter.LengthFilter(6)});
        c2.addView(pinIn, lp(-1, -2, 8));
        nameIn = input("Device name (shown on the computer)", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_WORDS);
        nameIn.setText(store.deviceName());
        c2.addView(nameIn, lp(-1, -2, 8));
        pairBtn = button("Connect", true);
        pairBtn.setOnClickListener(v -> pair());
        c2.addView(pairBtn, lp(-1, -2, 12));
        pairMsg = text("", 14, MUT, false);
        c2.addView(pairMsg, lp(-1, -2, 8));

        // ---------------- 3. how to print
        LinearLayout c3 = card(body, "3. Print from any app");
        String[] steps = {
                "Open the document in any app — bank statement, bill, Chrome, Gmail, Gallery…",
                "Tap Print (or ⋮ → Print / Share → Print).",
                "Tap the printer name at the top → All printers → choose your computer's printer.",
                "Tap the print button. The document prints on your computer's printer."};
        for (int i = 0; i < steps.length; i++) {
            TextView t = text((i + 1) + ".  " + steps[i], 14, TEXT, false);
            c3.addView(t, lp(-1, -2, i == 0 ? 0 : 6));
        }

        // ---------------- advanced
        TextView adv = text("Server: " + store.server() + "   (change)", 12, MUT, false);
        adv.setGravity(Gravity.CENTER);
        adv.setPadding(0, dp(14), 0, 0);
        adv.setOnClickListener(v -> askServer(adv));
        body.addView(adv);

        setContentView(sv);
        renderComputers();
    }

    @Override
    protected void onResume() {
        super.onResume();
        updateServiceState();
    }

    // ------------------------------------------------------------------ actions
    private void openPrintSettings() {
        try {
            startActivity(new Intent(Settings.ACTION_PRINT_SETTINGS));
        } catch (Exception e) {
            startActivity(new Intent(Settings.ACTION_SETTINGS));
        }
    }

    private void updateServiceState() {
        Boolean on = null;
        try {
            String s = Settings.Secure.getString(getContentResolver(), "enabled_print_services");
            if (s != null) on = s.contains(getPackageName());
        } catch (Exception ignored) { }
        if (on == null) {
            serviceState.setText("Make sure “PrintBridge Printer” is switched on in print settings.");
            serviceState.setTextColor(MUT);
        } else if (on) {
            serviceState.setText("✓ Printer service is on");
            serviceState.setTextColor(GREEN);
        } else {
            serviceState.setText("● Printer service is off");
            serviceState.setTextColor(RED);
        }
    }

    private void pair() {
        final String id = Store.normId(idIn.getText().toString());
        final String pin = pinIn.getText().toString().replaceAll("\\D", "");
        final String name = nameIn.getText().toString().trim();
        if (id.isEmpty()) { msg("Enter the full Computer ID (e.g. PB-7K3M-9QX2)", RED); return; }
        if (pin.length() != 6) { msg("The PIN must be 6 digits", RED); return; }
        if (!name.isEmpty()) store.setDeviceName(name);
        pairBtn.setEnabled(false);
        msg("Connecting… you may need to click “Allow” on the computer.", MUT);
        new Thread(() -> {
            try {
                JSONObject r = Api.pair(store.server(), id, pin, "Printer: " + store.deviceName());
                String token = r.optString("token", "");
                if (token.isEmpty()) throw new Api.ApiException(0, r.optString("error", "Could not connect"));
                Store.Computer c = new Store.Computer(r.optString("id", id), r.optString("name", id), token);
                store.put(c);
                main.post(() -> {
                    pairBtn.setEnabled(true);
                    pinIn.setText("");
                    idIn.setText("");
                    msg("✓ Connected to " + c.name + ". You can now print from any app.", GREEN);
                    renderComputers();
                });
            } catch (Api.ApiException e) {
                main.post(() -> { pairBtn.setEnabled(true); msg(e.getMessage(), RED); });
            }
        }).start();
    }

    private void renderComputers() {
        list.removeAllViews();
        for (final Store.Computer c : store.computers()) {
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(dp(12), dp(10), dp(8), dp(10));
            row.setBackground(round(SOFT, 0, 10));
            LinearLayout col = new LinearLayout(this);
            col.setOrientation(LinearLayout.VERTICAL);
            col.addView(text("💻  " + c.name, 15, TEXT, true));
            final TextView st = text(c.id + "  •  checking…", 12, MUT, false);
            col.addView(st);
            row.addView(col, new LinearLayout.LayoutParams(0, -2, 1));
            Button rm = button("Remove", false);
            rm.setOnClickListener(v -> new AlertDialog.Builder(this)
                    .setMessage("Remove “" + c.name + "” from this device?")
                    .setPositiveButton("Remove", (d, x) -> { store.remove(c.id); renderComputers(); })
                    .setNegativeButton("Cancel", null).show());
            row.addView(rm);
            list.addView(row, lp(-1, -2, 8));
            new Thread(() -> {
                String s;
                int color;
                try {
                    boolean on = Api.device(store.server(), c.id).optBoolean("online", false);
                    s = on ? "● Online" : "● Offline — start PrintBridge on the computer";
                    color = on ? GREEN : RED;
                } catch (Api.ApiException e) {
                    s = "● Cannot reach the server";
                    color = RED;
                }
                final String fs = c.id + "  •  " + s;
                final int fc = color;
                main.post(() -> { st.setText(fs); st.setTextColor(fc); });
            }).start();
        }
    }

    private void askServer(final TextView label) {
        final EditText e = input("https://printbridge.onrender.com", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        e.setText(store.server());
        new AlertDialog.Builder(this).setTitle("Server address").setView(e)
                .setPositiveButton("Save", (d, x) -> {
                    store.setServer(e.getText().toString());
                    label.setText("Server: " + store.server() + "   (change)");
                    renderComputers();
                })
                .setNegativeButton("Cancel", null).show();
    }

    private void msg(String s, int color) {
        pairMsg.setText(s);
        pairMsg.setTextColor(color);
    }

    // ------------------------------------------------------------------ chhote UI helpers
    private int dp(float v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics());
    }

    private LinearLayout.LayoutParams lp(int w, int h, int topDp) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(w, h);
        p.topMargin = dp(topDp);
        return p;
    }

    private GradientDrawable round(int fill, int stroke, int radiusDp) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(fill);
        g.setCornerRadius(dp(radiusDp));
        if (stroke != 0) g.setStroke(dp(1), stroke);
        return g;
    }

    private TextView text(String s, int sp, int color, boolean bold) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        if (bold) t.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        t.setLineSpacing(dp(2), 1f);
        return t;
    }

    private LinearLayout card(ViewGroup parent, String title) {
        LinearLayout c = new LinearLayout(this);
        c.setOrientation(LinearLayout.VERTICAL);
        c.setPadding(dp(16), dp(16), dp(16), dp(16));
        c.setBackground(round(Color.WHITE, LINE, 16));
        TextView t = text(title, 17, TEXT, true);
        t.setPadding(0, 0, 0, dp(6));
        c.addView(t);
        parent.addView(c, lp(-1, -2, parent.getChildCount() == 0 ? 0 : 12));
        return c;
    }

    private Button button(String s, boolean primary) {
        Button b = new Button(this);
        b.setText(s);
        b.setAllCaps(false);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, primary ? 16 : 13);
        b.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        b.setTextColor(primary ? Color.WHITE : RED);
        b.setBackground(round(primary ? BLUE : 0xFFFEE2E2, 0, 12));
        b.setMinHeight(dp(primary ? 50 : 38));
        b.setPadding(dp(14), 0, dp(14), 0);
        if (Build.VERSION.SDK_INT >= 21) b.setStateListAnimator(null);
        return b;
    }

    private EditText input(String hint, int type) {
        EditText e = new EditText(this);
        e.setHint(hint);
        e.setInputType(type);
        e.setSingleLine(true);
        e.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        e.setTextColor(TEXT);
        e.setHintTextColor(0xFF9CA3AF);
        e.setPadding(dp(14), dp(12), dp(14), dp(12));
        e.setBackground(round(Color.WHITE, 0xFFD1D5DB, 10));
        return e;
    }
}
