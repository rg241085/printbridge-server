"""
PrintBridge - laptop ki extra windows
  open_shop(root, m)    : Print shop ki line (orders), settings, poster
  open_report(root, m)  : kisne kitna chhapa (Office mode), CSV export
  open_phones(root, m)  : jude hue phones, color band / roz ki limit / hatana
m = printbridge module (CFG, SHOP, us, pb, emit... wahin se)
"""
import os
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, simpledialog, ttk

F = "Segoe UI"
_open = {}


def _single(name, root, title, size):
    w = _open.get(name)
    if w is not None and w.winfo_exists():
        w.deiconify()
        w.lift()
        return None
    w = tk.Toplevel(root)
    w.title(title)
    w.geometry(size)
    w.configure(bg="white")
    _open[name] = w
    return w


def _open_file(path):
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa
        else:
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        messagebox.showerror("PrintBridge", f"File khul nahi paayi: {e}")


def _money(v):
    return f"₹{v:g}" if isinstance(v, (int, float)) else "?"


# =================================================================== PRINT SHOP
STATUS = {"receiving": "File aa rahi…", "waiting": "Line mein", "printing": "Print ho raha…",
          "ready": "Taiyaar (dena hai)", "done": "De diya", "rejected": "Hataya", "cancelled": "Cancel"}


def open_shop(root, m):
    win = _single("shop", root, "PrintBridge - Print shop", "820x600")
    if win is None:
        return
    S = m.CFG["shop"]

    head = tk.Frame(win, bg="#f5f3ff", padx=14, pady=10)
    head.pack(fill="x")
    tk.Label(head, text="Print shop", font=(F, 16, "bold"), fg="#7c3aed", bg="#f5f3ff").grid(row=0, column=0, sticky="w")
    name_lbl = tk.Label(head, font=(F, 10), fg="#444", bg="#f5f3ff", width=56, anchor="w")
    name_lbl.grid(row=1, column=0, sticky="w")
    link_lbl = tk.Label(head, font=(F, 9), fg="#555", bg="#f5f3ff", width=62, anchor="w")
    link_lbl.grid(row=2, column=0, sticky="w")
    on_var = tk.BooleanVar(value=S.get("enabled"))
    auto_var = tk.BooleanVar(value=S.get("auto_print"))

    def save_shop():
        with m.CFG_LOCK:
            S["enabled"], S["auto_print"] = on_var.get(), auto_var.get()
            m.save_config()
        refresh_head()

    ttk.Checkbutton(head, text="Shop mode chalu (customer order bhej sakte hain)", variable=on_var,
                    command=save_shop).grid(row=0, column=1, sticky="w", padx=(30, 0))
    ttk.Checkbutton(head, text="Order aate hi apne aap print karo", variable=auto_var,
                    command=save_shop).grid(row=1, column=1, sticky="w", padx=(30, 0))
    hb = tk.Frame(head, bg="#f5f3ff")
    hb.grid(row=2, column=1, sticky="w", padx=(30, 0), pady=(4, 0))

    def refresh_head():
        name_lbl.configure(text=f"{m.shop_name()}  •  B&W ₹{S['bw_rate']:g}  •  Color ₹{S['color_rate']:g}"
                                + ("" if S.get("enabled") else "  •  BAND hai"),
                           fg="#444" if S.get("enabled") else "#dc2626")
        link_lbl.configure(text="Customer link: " + (m.shop_link() or "(server set nahi)"))

    def settings():
        d = tk.Toplevel(win)
        d.title("Shop settings")
        d.configure(bg="white", padx=16, pady=12)
        d.grab_set()
        vals = {}
        for i, (k, label) in enumerate([("name", "Shop ka naam"), ("bw_rate", "Black & White ₹ / kaagaz"),
                                        ("color_rate", "Color ₹ / kaagaz")]):
            tk.Label(d, text=label, bg="white", font=(F, 10)).grid(row=i, column=0, sticky="w", pady=4)
            e = ttk.Entry(d, width=28)
            e.insert(0, str(S.get(k) if k != "name" else (S.get("name") or m.CFG["name"])))
            e.grid(row=i, column=1, pady=4, padx=(10, 0))
            vals[k] = e

        def ok():
            try:
                bw, col = float(vals["bw_rate"].get()), float(vals["color_rate"].get())
                if bw < 0 or col < 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Shop settings", "Rate sahi number mein likhiye (jaise 2 ya 1.5)", parent=d)
                return
            with m.CFG_LOCK:
                S.update(name=vals["name"].get().strip()[:40], bw_rate=bw, color_rate=col)
                m.save_config()
            refresh_head()
            d.destroy()
        ttk.Button(d, text="Save", command=ok).grid(row=3, column=1, sticky="e", pady=(10, 0))

    def copy_link():
        win.clipboard_clear()
        win.clipboard_append(m.shop_link())
        m.emit("Shop link copy hua")

    ttk.Button(hb, text="Settings (naam / rate)", command=settings).pack(side="left", padx=(0, 4))
    ttk.Button(hb, text="Poster banao (QR)", command=lambda: make_poster(m, win)).pack(side="left", padx=4)
    ttk.Button(hb, text="Link copy", command=copy_link).pack(side="left", padx=4)

    cols = ("token", "naam", "files", "kaagaz", "rang", "rs", "status")
    heads = ("Token", "Customer", "Files", "Kaagaz", "Rang", "Rupaye", "Status")
    widths = (60, 150, 230, 70, 60, 70, 120)
    frame = tk.Frame(win, bg="white")
    frame.pack(fill="both", expand=True, padx=12, pady=(10, 4))
    tv = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
    for c, h, w in zip(cols, heads, widths):
        tv.heading(c, text=h)
        tv.column(c, width=w, anchor="w" if c in ("naam", "files", "status") else "center")
    sb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
    tv.configure(yscrollcommand=sb.set)
    tv.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    tv.tag_configure("ready", background="#dcfce7")
    tv.tag_configure("printing", background="#fef9c3")
    empty = tk.Label(win, text="Abhi koi order nahi. Customer QR scan karke file bhejega to yahan dikhega.",
                     font=(F, 10), fg="#888", bg="white")

    def row_vals(o):
        known = [f["sheets"] * f["copies"] for f in o["files"] if f.get("sheets") is not None]
        unknown = any(f.get("sheets") is None and f["status"] != "error" for f in o["files"])
        colors = {f["color"] for f in o["files"]}
        names = ", ".join(f["name"] for f in o["files"])
        cust = o["customer"] + (f" ({o['mobile']})" if o.get("mobile") else "")
        return (f"#{o['token']}", cust, names[:60] + ("…" if len(names) > 60 else ""),
                f"{sum(known)}{'+?' if unknown else ''}", "Mix" if len(colors) > 1 else ("Color" if "color" in colors else "B&W"),
                _money(o["total"]) + ("+?" if not o.get("total_known") else ""),
                STATUS.get(o["status"], o["status"]) + (" ⚠" if any(f["status"] == "error" for f in o["files"]) else ""))

    def refresh():
        sel = tv.selection()
        tv.delete(*tv.get_children())
        orders = m.SHOP.list()
        for o in orders:
            tv.insert("", "end", iid=o["id"], values=row_vals(o), tags=(o["status"],))
        if sel and tv.exists(sel[0]):
            tv.selection_set(sel[0])
        elif orders:
            tv.selection_set(orders[0]["id"])
        if orders:
            empty.pack_forget()
        else:
            empty.pack(pady=4)
        today = [d for d in m.us.summary("today") if d["source"] == "shop"]
        earn_lbl.configure(text=f"Aaj shop se: {sum(d['pages'] for d in today)} kaagaz  •  "
                                f"₹{sum(d['amount'] for d in today):g}")

    def selected():
        s = tv.selection()
        if not s:
            messagebox.showinfo("Print shop", "Pehle list mein ek order chuniye", parent=win)
            return None
        return m.SHOP.get(s[0])

    def do_print():
        o = selected()
        if not o:
            return
        if o["status"] not in ("waiting", "ready"):
            messagebox.showinfo("Print shop", "Ye order abhi print nahi ho sakta (file aa rahi hai ya print chal raha hai)", parent=win)
            return
        if o["status"] == "ready" and not messagebox.askyesno("Print shop", "Ye order pehle chhap chuka hai. Dobara chhaapein?", parent=win):
            return

        def run():
            ok, err = m.shop_print(o["id"])
            if not ok:
                m.UI_EVENTS.put(("error", f"Order #{o['token']} print nahi hua:\n{err}"))
        threading.Thread(target=run, daemon=True).start()

    def do_open():
        o = selected()
        if o:
            for f in o["files"][:5]:
                if os.path.exists(f["path"]):
                    _open_file(f["path"])

    def do_done():
        o = selected()
        if o:
            m.SHOP.set_status(o["id"], "done", "Print le liya gaya")

    def do_remove():
        o = selected()
        if o and messagebox.askyesno("Print shop", f"Order #{o['token']} ({o['customer']}) hata dein?\n"
                                                    "Customer ko 'Shop ne order nahi liya' dikhega.", parent=win):
            m.SHOP.set_status(o["id"], "rejected", "Shop ne order hata diya. Counter par baat kijiye.")

    bb = tk.Frame(win, bg="white")
    bb.pack(fill="x", padx=12, pady=(4, 12))
    ttk.Button(bb, text="Print", command=do_print).pack(side="left", padx=(0, 4))
    ttk.Button(bb, text="File kholiye", command=do_open).pack(side="left", padx=4)
    ttk.Button(bb, text="De diya (khatam)", command=do_done).pack(side="left", padx=4)
    ttk.Button(bb, text="Hatao", command=do_remove).pack(side="left", padx=4)
    earn_lbl = tk.Label(bb, font=(F, 10, "bold"), fg="#059669", bg="white")
    earn_lbl.pack(side="right")
    tv.bind("<Double-1>", lambda e: do_print())

    def tick():
        if not win.winfo_exists():
            return
        if m.SHOP_DIRTY.is_set():
            m.SHOP_DIRTY.clear()
            refresh()
        win.after(800, tick)

    refresh_head()
    refresh()
    tick()

    def ask_enable():  # window dikhne ke baad hi poochho (warna sawaal chhupa reh jaata hai)
        win.lift()
        if not S.get("enabled") and messagebox.askyesno(
                "Print shop", "Shop mode abhi band hai. Chalu karein?\n\nChalu karne par koi bhi customer aapka shop "
                              "QR scan karke file bhej sakta hai. Files apne aap print nahi hongi, aap 'Print' dabayenge.",
                parent=win):
            on_var.set(True)
            save_shop()
    win.after(400, ask_enable)


def _font(size, bold=True):
    from PIL import ImageFont
    names = (["arialbd.ttf", "segoeuib.ttf"] if bold else ["arial.ttf", "segoeui.ttf"]) + \
            ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def make_poster(m, parent=None, open_after=True):
    """Counter par chipkane ke liye A4 poster (PDF): shop ka naam, bada QR, 3 steps, rate."""
    from PIL import Image, ImageDraw
    import qrcode
    link = m.shop_link()
    if not link:
        messagebox.showerror("Poster", "Pehle server set kijiye", parent=parent)
        return None
    W, H = 2480, 3508
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    purple = (124, 58, 237)
    d.rectangle([0, 0, W, 520], fill=purple)

    def center(y, text, font, fill):
        w = d.textlength(text, font=font)
        d.text(((W - w) / 2, y), text, font=font, fill=fill)

    center(110, m.shop_name()[:28], _font(150), "white")
    center(320, "Mobile se seedha print karwaiye", _font(90, False), (237, 233, 254))
    q = qrcode.QRCode(border=2, box_size=10, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(link)
    q.make(fit=True)
    qi = q.make_image(fill_color="black", back_color="white").get_image().convert("RGB").resize((1400, 1400), Image.NEAREST)
    img.paste(qi, ((W - 1400) // 2, 650))
    d.rounded_rectangle([(W - 1400) // 2 - 20, 630, (W + 1400) // 2 + 20, 2070], radius=40, outline=purple, width=12)
    center(2110, "Phone ke camera se scan kijiye", _font(95), (31, 41, 55))
    steps = ["1.  QR scan kijiye aur apna naam likhiye", "2.  File chuniye aur 'Shop ko bhejiye' dabaiye",
             "3.  Token number milega. Taiyaar hone par counter se lijiye"]
    for i, s in enumerate(steps):
        d.text((260, 2330 + i * 150), s, font=_font(78, False), fill=(55, 65, 81))
    S = m.CFG["shop"]
    d.rounded_rectangle([260, 2860, W - 260, 3180], radius=40, fill=(245, 243, 255))
    center(2920, f"Black & White: Rs {S['bw_rate']:g} / page", _font(95), purple)
    center(3050, f"Color: Rs {S['color_rate']:g} / page", _font(95), purple)
    center(3300, "Koi app install karne ki zaroorat nahi  •  PrintBridge", _font(60, False), (120, 120, 120))
    path = os.path.join(m.DATA_DIR, "Shop_poster.pdf")
    img.save(path, "PDF", resolution=300)
    img.save(os.path.join(m.DATA_DIR, "Shop_poster.png"))
    m.emit(f"Poster bana: {path}")
    if open_after:
        _open_file(path)
    return path


# =================================================================== REPORT (Office mode)
SRC = {"phone": "Phone", "shop": "Print shop", "local": "Bina internet"}


def open_report(root, m):
    win = _single("report", root, "PrintBridge - Report (kisne kitna chhapa)", "720x500")
    if win is None:
        return
    top = tk.Frame(win, bg="white", padx=12, pady=10)
    top.pack(fill="x")
    tk.Label(top, text="Report", font=(F, 15, "bold"), fg="#1a56db", bg="white").pack(side="left")
    period = tk.StringVar(value="today")
    for v, t in [("today", "Aaj"), ("week", "Is hafte"), ("month", "Is mahine"), ("all", "Sab")]:
        ttk.Radiobutton(top, text=t, value=v, variable=period, command=lambda: refresh()).pack(side="left", padx=(16, 0))

    cols = ("naam", "src", "prints", "pages", "color", "bw", "rs")
    heads = ("Naam", "Kahan se", "Prints", "Kul kaagaz", "Color", "B&W", "Rupaye (shop)")
    frame = tk.Frame(win, bg="white")
    frame.pack(fill="both", expand=True, padx=12)
    tv = ttk.Treeview(frame, columns=cols, show="headings")
    for c, h, w in zip(cols, heads, (180, 100, 60, 90, 70, 70, 110)):
        tv.heading(c, text=h)
        tv.column(c, width=w, anchor="w" if c in ("naam", "src") else "center")
    tv.pack(fill="both", expand=True)
    total = tk.Label(win, font=(F, 11, "bold"), bg="white", fg="#111")
    total.pack(anchor="w", padx=14, pady=6)

    def refresh():
        tv.delete(*tv.get_children())
        data = m.us.summary(period.get())
        for d in data:
            tv.insert("", "end", values=(d["name"], SRC.get(d["source"], d["source"]), d["prints"],
                                         f"{d['pages']}" + (f" +{d['unknown']}?" if d["unknown"] else ""),
                                         d["color"], d["bw"], f"₹{d['amount']:g}" if d["amount"] else "-"))
        total.configure(text=f"Kul: {sum(d['prints'] for d in data)} prints  •  {sum(d['pages'] for d in data)} kaagaz  "
                             f"(Color {sum(d['color'] for d in data)}, B&W {sum(d['bw'] for d in data)})"
                             + (f"  •  Shop ₹{sum(d['amount'] for d in data):g}" if any(d['amount'] for d in data) else ""))

    def export():
        name = {"today": "aaj", "week": "hafta", "month": "mahina", "all": "sab"}[period.get()]
        path = filedialog.asksaveasfilename(parent=win, defaultextension=".csv", filetypes=[("Excel / CSV", "*.csv")],
                                            initialfile=f"PrintBridge_report_{name}_{datetime.now():%Y%m%d}.csv")
        if path:
            n = m.us.export(path, period.get())
            messagebox.showinfo("Report", f"{n} prints ki list save ho gayi:\n{path}\n\nIse Excel mein khol sakte hain.", parent=win)

    bb = tk.Frame(win, bg="white")
    bb.pack(fill="x", padx=12, pady=(0, 12))
    ttk.Button(bb, text="Excel (CSV) mein save", command=export).pack(side="left")
    ttk.Button(bb, text="Refresh", command=refresh).pack(side="left", padx=6)
    refresh()


# =================================================================== PHONES (niyam)
def open_phones(root, m, after_change=None):
    win = _single("phones", root, "PrintBridge - Jude hue phones", "700x440")
    if win is None:
        return
    tk.Label(win, text="Jude hue phones", font=(F, 15, "bold"), fg="#1a56db", bg="white").pack(anchor="w", padx=12, pady=(10, 0))
    tk.Label(win, text="Office / ghar ke niyam: kisi phone ke liye color band kijiye ya roz ki page limit lagaiye.",
             font=(F, 9), fg="#555", bg="white").pack(anchor="w", padx=12)
    cols = ("naam", "juda", "aaj", "color", "limit")
    frame = tk.Frame(win, bg="white")
    frame.pack(fill="both", expand=True, padx=12, pady=8)
    tv = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
    for c, h, w in zip(cols, ("Naam", "Kab juda", "Aaj ke kaagaz", "Color", "Roz ki limit"), (200, 140, 100, 90, 100)):
        tv.heading(c, text=h)
        tv.column(c, width=w, anchor="w" if c == "naam" else "center")
    tv.pack(fill="both", expand=True)

    def refresh():
        keep = tv.selection()
        tv.delete(*tv.get_children())
        for h, ph in m.CFG["phones"].items():
            r = m.CFG["rules"].get(h, {})
            tv.insert("", "end", iid=h, values=(ph.get("name", "Phone"), ph.get("added", "")[:16].replace("T", " "),
                                                m.us.today_pages(h[:12]),
                                                "BAND" if r.get("no_color") else "Chalu",
                                                r.get("daily_limit") or "Koi nahi"))
        if keep and tv.exists(keep[0]):
            tv.selection_set(keep[0])
        elif len(m.CFG["phones"]) == 1:  # ek hi phone ho to wahi chuna hua
            tv.selection_set(next(iter(m.CFG["phones"])))

    def sel():
        s = tv.selection()
        if not s:
            messagebox.showinfo("Phones", "Pehle ek phone chuniye", parent=win)
        return s[0] if s else None

    def set_rule(h, **kw):
        with m.CFG_LOCK:
            m.CFG["rules"].setdefault(h, {}).update(kw)
            m.save_config()
        refresh()

    def toggle_color():
        h = sel()
        if h:
            set_rule(h, no_color=not m.CFG["rules"].get(h, {}).get("no_color"))

    def limit():
        h = sel()
        if not h:
            return
        v = simpledialog.askinteger("Roz ki limit", "Ye phone roz kitne kaagaz chhaap sakta hai?\n(0 = koi limit nahi)",
                                    parent=win, minvalue=0, maxvalue=100000,
                                    initialvalue=m.CFG["rules"].get(h, {}).get("daily_limit") or 0)
        if v is not None:
            set_rule(h, daily_limit=v)

    def remove():
        h = sel()
        if h and messagebox.askyesno("Phones", f"'{m.CFG['phones'][h].get('name')}' ko hata dein?\n"
                                               "Use dobara jodne ke liye ID + PIN chahiye hoga.", parent=win):
            with m.CFG_LOCK:
                m.CFG["phones"].pop(h, None)
                m.CFG["rules"].pop(h, None)
                m.save_config()
            m.emit("Ek phone hata diya")
            refresh()
            after_change and after_change()

    bb = tk.Frame(win, bg="white")
    bb.pack(fill="x", padx=12, pady=(0, 12))
    ttk.Button(bb, text="Color band / chalu", command=toggle_color).pack(side="left")
    ttk.Button(bb, text="Roz ki limit…", command=limit).pack(side="left", padx=6)
    ttk.Button(bb, text="Ye phone hatao", command=remove).pack(side="left")
    refresh()
