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
        messagebox.showerror("PrintBridge", f"Could not open the file: {e}")


def _money(v):
    return f"₹{v:g}" if isinstance(v, (int, float)) else "?"


# =================================================================== PRINT SHOP (dashboard)
STATUS = {"receiving": "Receiving…", "waiting": "In queue", "printing": "Printing…",
          "ready": "Ready for pickup", "done": "Collected", "rejected": "Declined", "cancelled": "Cancelled"}
C = {"purple": "#6d28d9", "purple2": "#7c3aed", "light": "#f5f3ff", "line": "#ddd6fe", "text": "#1f2937",
     "mut": "#6b7280", "green": "#059669", "green_bg": "#dcfce7", "yellow_bg": "#fef9c3", "red": "#dc2626",
     "white": "#ffffff", "blue_bg": "#eff6ff"}


def _btn(parent, text, cmd, bg, fg="#ffffff", big=False, **kw):
    b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
                  relief="flat", bd=0, cursor="hand2", font=(F, 11 if big else 10, "bold" if big else "normal"),
                  padx=16 if big else 11, pady=8 if big else 5, **kw)
    return b


def _check_images(win):
    """Checkbox ki chhoti tasveerein (font par bharosa nahi, har computer par dikhein)."""
    from PIL import Image, ImageDraw, ImageTk
    out = {}
    for on in (False, True):
        im = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([4, 4, 35, 35], radius=7, fill=(124, 58, 237) if on else (255, 255, 255),
                            outline=(124, 58, 237) if on else (156, 163, 175), width=3)
        if on:
            d.line([(11, 20), (17, 27), (29, 12)], fill="white", width=5)
        out[on] = ImageTk.PhotoImage(im.resize((20, 20), Image.LANCZOS), master=win)
    return out


def open_shop(root, m):
    win = _single("shop", root, "PrintBridge - Print shop dashboard", "1060x680")
    if win is None:
        return
    win.configure(bg=C["light"])
    win.minsize(900, 560)
    S = m.CFG["shop"]
    checked = set()
    state = {"filter": "all", "last_count": 0}

    # ---------------------------------------------------- header
    head = tk.Frame(win, bg=C["purple"], padx=18, pady=12)
    head.pack(fill="x")
    left = tk.Frame(head, bg=C["purple"])
    left.pack(side="left", fill="x", expand=True)
    title = tk.Label(left, font=(F, 18, "bold"), fg="white", bg=C["purple"], anchor="w")
    title.pack(anchor="w")
    sub = tk.Label(left, font=(F, 9), fg="#ddd6fe", bg=C["purple"], anchor="w")
    sub.pack(anchor="w")
    right = tk.Frame(head, bg=C["purple"])
    right.pack(side="right")
    on_var = tk.BooleanVar(value=S.get("enabled"))
    auto_var = tk.BooleanVar(value=S.get("auto_print"))
    state_btn = tk.Button(right, relief="flat", bd=0, cursor="hand2", font=(F, 10, "bold"), padx=14, pady=6)
    state_btn.grid(row=0, column=0, columnspan=3, sticky="e")
    hb = tk.Frame(right, bg=C["purple"])
    hb.grid(row=1, column=0, columnspan=3, sticky="e", pady=(8, 0))

    def save_shop():
        with m.CFG_LOCK:
            S["enabled"], S["auto_print"] = on_var.get(), auto_var.get()
            m.save_config()
        refresh_head()

    def toggle_shop():
        on_var.set(not on_var.get())
        save_shop()

    state_btn.configure(command=toggle_shop)

    def refresh_head():
        title.configure(text=m.shop_name())
        sub.configure(text=f"B&W ₹{S['bw_rate']:g} / sheet   •   Color ₹{S['color_rate']:g} / sheet   •   "
                           f"Customer link: {m.shop_link() or '(server not set)'}")
        if S.get("enabled"):
            state_btn.configure(text="●  Shop OPEN — accepting orders", bg="#dcfce7", fg="#065f46",
                                activebackground="#dcfce7")
        else:
            state_btn.configure(text="●  Shop CLOSED — click to open", bg="#fee2e2", fg="#991b1b",
                                activebackground="#fee2e2")

    # ---------------------------------------------------- stat cards
    cards = tk.Frame(win, bg=C["light"], padx=14, pady=10)
    cards.pack(fill="x")
    stat = {}
    for i, (k, label, color) in enumerate([("wait", "In queue", C["purple2"]), ("ready", "Ready for pickup", C["green"]),
                                           ("pages", "Sheets today", "#2563eb"), ("money", "Revenue today", "#b45309")]):
        f = tk.Frame(cards, bg="white", padx=16, pady=8, highlightbackground=C["line"], highlightthickness=1)
        f.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
        cards.columnconfigure(i, weight=1)
        stat[k] = tk.Label(f, text="0", font=(F, 20, "bold"), fg=color, bg="white")
        stat[k].pack(anchor="w")
        tk.Label(f, text=label, font=(F, 9), fg=C["mut"], bg="white").pack(anchor="w")

    # ---------------------------------------------------- filter + search
    bar = tk.Frame(win, bg=C["light"], padx=14)
    bar.pack(fill="x")
    fbtn = {}

    def set_filter(k):
        state["filter"] = k
        for kk, b in fbtn.items():
            on = kk == k
            b.configure(bg=C["purple2"] if on else "white", fg="white" if on else C["text"],
                        activebackground=C["purple2"] if on else "white")
        refresh()

    for k, t in [("all", "All"), ("waiting", "In queue"), ("ready", "Ready"), ("private", "Private")]:
        fbtn[k] = tk.Button(bar, text=t, command=lambda k=k: set_filter(k), relief="flat", bd=0, cursor="hand2",
                            font=(F, 10), padx=14, pady=4, highlightthickness=1, highlightbackground=C["line"])
        fbtn[k].pack(side="left", padx=(0, 6))
    search = tk.StringVar()
    tk.Label(bar, text="Search:", font=(F, 9), fg=C["mut"], bg=C["light"]).pack(side="left", padx=(18, 4))
    se = ttk.Entry(bar, textvariable=search, width=22)
    se.pack(side="left")
    search.trace_add("write", lambda *a: refresh())
    auto_cb = tk.Checkbutton(bar, text="Print new orders automatically", variable=auto_var, command=save_shop,
                             bg=C["light"], activebackground=C["light"], font=(F, 9), fg=C["mut"])
    auto_cb.pack(side="right")

    # ---------------------------------------------------- table
    style = ttk.Style(win)
    style.configure("Shop.Treeview", rowheight=32, font=(F, 10), background="white", fieldbackground="white",
                    borderwidth=0)
    style.configure("Shop.Treeview.Heading", font=(F, 10, "bold"), background="#ede9fe", foreground=C["text"],
                    relief="flat", padding=(6, 6))
    style.map("Shop.Treeview", background=[("selected", "#c4b5fd")], foreground=[("selected", C["text"])])
    frame = tk.Frame(win, bg="white", highlightbackground=C["line"], highlightthickness=1)
    frame.pack(fill="both", expand=True, padx=14, pady=8)
    cols = ("token", "naam", "files", "kaagaz", "rang", "rs", "status", "note", "act")
    heads = ("Token", "Customer", "Files", "Sheets", "Color", "Amount", "Status", "Note", "Actions")
    widths = (60, 140, 180, 62, 58, 76, 130, 120, 196)
    box = _check_images(win)
    win._box = box  # tasveerein yaad rakho (warna Tk bhool jaata hai)
    tv = ttk.Treeview(frame, columns=cols, show="tree headings", selectmode="browse", style="Shop.Treeview")
    tv.column("#0", width=44, minwidth=44, stretch=False, anchor="center")
    tv.heading("#0", image=box[False])
    for c, h, w in zip(cols, heads, widths):
        tv.heading(c, text=h)
        tv.column(c, width=w, minwidth=196 if c == "act" else 30, stretch=c in ("files", "note"),
                  anchor="w" if c in ("naam", "files", "status", "note") else "center")
    sb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
    tv.configure(yscrollcommand=lambda a, b: (sb.set(a, b), win.after_idle(place_row_buttons)))
    tv.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    tv.tag_configure("ready", background=C["green_bg"])
    tv.tag_configure("printing", background=C["yellow_bg"])
    tv.tag_configure("receiving", foreground=C["mut"])
    tv.tag_configure("checked", background="#ede9fe")
    tv.tag_configure("private", foreground="#5b21b6")
    empty = tk.Label(frame, text="No orders yet.\nOrders appear here when customers scan your counter QR code and send files.",
                     font=(F, 11), fg=C["mut"], bg="white", justify="center")

    def row_vals(o):
        known = [f["sheets"] * f["copies"] for f in o["files"] if f.get("sheets") is not None]
        unknown = any(f.get("sheets") is None and f["status"] != "error" for f in o["files"])
        colors = {f["color"] for f in o["files"]}
        names = (f"[Private] {len(o['files'])} {'file' if len(o['files']) == 1 else 'files'}" if o.get("private")
                 else ", ".join(f["name"] for f in o["files"]))
        cust = o["customer"] + (f" ({o['mobile']})" if o.get("mobile") else "")
        return (f"#{o['token']}", cust,
                names[:60] + ("…" if len(names) > 60 else ""),
                f"{sum(known)}{'+?' if unknown else ''}", "Mix" if len(colors) > 1 else ("Color" if "color" in colors else "B&W"),
                _money(o["total"]) + ("+?" if not o.get("total_known") else "") + (" *" if o.get("amount_override") is not None else ""),
                STATUS.get(o["status"], o["status"]) + (" ⚠" if any(f["status"] == "error" for f in o["files"]) else ""),
                o.get("note", ""), "")

    def visible_orders():
        q = search.get().strip().lower().lstrip("#")
        out = []
        for o in m.SHOP.list():
            f = state["filter"]
            if f == "waiting" and o["status"] not in ("waiting", "receiving"):
                continue
            if f == "ready" and o["status"] != "ready":
                continue
            if f == "private" and not o.get("private"):
                continue
            if q and q not in o["customer"].lower() and q != str(o["token"]) and q not in (o.get("mobile") or ""):
                continue
            out.append(o)
        return out

    # ---------------------------------------------------- har line ke aage Print / Cancel button
    # (Treeview ke andar button nahi lagte, isliye asli button "Kaam" column ke upar chipka dete hain)
    row_btns = {}   # order id -> (frame, kind)
    ROW_BG = {"ready": C["green_bg"], "printing": C["yellow_bg"]}

    def mini(parent, text, cmd, bg, fg):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
                         relief="flat", bd=0, cursor="hand2", font=(F, 9, "bold"), padx=8, pady=0)

    def row_kind(o):
        if o["status"] in ("receiving", "printing"):
            return o["status"]
        return "ready" if o["status"] == "ready" else "waiting"

    def make_row_buttons(o):
        kind = row_kind(o)
        fr = tk.Frame(tv, bg=ROW_BG.get(o["status"], "white"))
        oid = o["id"]
        if kind == "waiting":
            mini(fr, "Print", lambda: print_one(oid), C["purple2"], "white").pack(side="left", fill="y", padx=(0, 4))
            mini(fr, "Cancel", lambda: cancel_one(oid), "#fee2e2", C["red"]).pack(side="left", fill="y")
        elif kind == "ready":
            mini(fr, "Reprint", lambda: print_one(oid), "#ede9fe", C["purple2"]).pack(side="left", fill="y", padx=(0, 4))
            mini(fr, "Collected", lambda: done_one(oid), C["green"], "white").pack(side="left", fill="y")
        else:
            tk.Label(fr, text="receiving…" if kind == "receiving" else "printing…", font=(F, 9),
                     fg=C["mut"], bg=fr["bg"]).pack(side="left")
        return fr, kind

    def place_row_buttons():
        if not win.winfo_exists():
            return
        for iid in tv.get_children():
            ent = row_btns.get(iid)
            if not ent:
                continue
            bb = tv.bbox(iid, "act")
            if not bb:
                ent[0].place_forget()
                continue
            x, y, w, h = bb
            ent[0].place(x=x + 6, y=y + 4, width=max(10, w - 12), height=max(10, h - 8))

    def sync_row_buttons(orders):
        want = {o["id"]: o for o in orders}
        for oid in list(row_btns):
            o = want.get(oid)
            if not o or row_btns[oid][1] != row_kind(o) or row_btns[oid][0]["bg"] != ROW_BG.get(o["status"], "white"):
                row_btns.pop(oid)[0].destroy()
        for oid, o in want.items():
            if oid not in row_btns:
                row_btns[oid] = make_row_buttons(o)
        win.after_idle(place_row_buttons)

    def print_one(oid):
        o = m.SHOP.get(oid)
        if not o:
            return
        if o["status"] in ("receiving", "printing"):
            return
        if o["status"] == "ready" and not messagebox.askyesno(
                "Print shop", f"Order #{o['token']} ({o['customer']}) has already been printed. Print it again?", parent=win):
            return
        checked.discard(oid)

        def run():
            ok, err = m.shop_print(oid)
            if not ok:
                m.UI_EVENTS.put(("error", f"#{o['token']} {o['customer']} could not be printed:\n{err}"))
        threading.Thread(target=run, daemon=True).start()

    def cancel_one(oid):
        o = m.SHOP.get(oid)
        if o and messagebox.askyesno("Cancel order", f"Cancel order #{o['token']} ({o['customer']})?\n\n"
                                     "The customer will see that the shop declined the order.", parent=win):
            m.SHOP.set_status(oid, "rejected", "The shop cancelled this order. Please ask at the counter.")
            checked.discard(oid)

    def done_one(oid):
        m.shop_done(oid)
        checked.discard(oid)

    tv.bind("<Configure>", lambda e: win.after_idle(place_row_buttons))
    tv.bind("<ButtonRelease-1>", lambda e: win.after_idle(place_row_buttons), add="+")

    def refresh():
        sel = tv.selection()
        tv.delete(*tv.get_children())
        allo = m.SHOP.list()
        live = {o["id"] for o in allo}
        checked.intersection_update(live)
        orders = visible_orders()
        for o in orders:
            tags = [o["status"]] + (["private"] if o.get("private") else []) + (["checked"] if o["id"] in checked else [])
            tv.insert("", "end", iid=o["id"], text="", image=box[o["id"] in checked], values=row_vals(o), tags=tags)
        if sel and tv.exists(sel[0]):
            tv.selection_set(sel[0])
        sync_row_buttons(orders)
        tv.heading("#0", image=box[bool(orders) and all(o["id"] in checked for o in orders)])
        if orders:
            empty.place_forget()
        else:
            empty.place(relx=0.5, rely=0.5, anchor="center")
        stat["wait"].configure(text=str(sum(o["status"] in ("waiting", "receiving", "printing") for o in allo)))
        stat["ready"].configure(text=str(sum(o["status"] == "ready" for o in allo)))
        today = [d for d in m.us.summary("today") if d["source"] == "shop"]
        stat["pages"].configure(text=str(sum(d["pages"] for d in today)))
        stat["money"].configure(text=f"₹{sum(d['amount'] for d in today):g}")
        n = len(checked)
        sel_lbl.configure(text=f"{n} {'order' if n == 1 else 'orders'} selected — the buttons below apply to all selected orders" if n else
                          "Single order: use Print / Cancel in its row.   Multiple orders: tick the boxes, "
                          "then click Print below.")
        print_btn.configure(text=f"Print ({n})" if n > 1 else "Print")
        if len(allo) > state["last_count"]:  # naya order: header thoda chamke
            title.configure(fg="#fde68a")
            win.after(1500, lambda: title.configure(fg="white"))
        state["last_count"] = len(allo)

    def on_click(e):
        region, col, row = tv.identify_region(e.x, e.y), tv.identify_column(e.x), tv.identify_row(e.y)
        if region == "heading" and col == "#0":  # sab chuniye / sab hatao
            vis = [o["id"] for o in visible_orders()]
            if vis and all(i in checked for i in vis):
                checked.difference_update(vis)
            else:
                checked.update(vis)
            refresh()
            return "break"
        if row and col == "#0":
            checked.symmetric_difference_update({row})
            refresh()
            tv.selection_set(row)
            return "break"
    tv.bind("<Button-1>", on_click)

    def targets():
        """Chune hue (☑) orders, warna jo line highlight hai."""
        ids = [i for i in tv.get_children() if i in checked] or list(tv.selection())
        orders = [m.SHOP.get(i) for i in ids if m.SHOP.get(i)]
        if not orders:
            messagebox.showinfo("Print shop", "Select an order first by ticking the box next to it "
                                              "(or tick the box in the header to select all).", parent=win)
        return orders

    # ---------------------------------------------------- actions
    def do_print():
        orders = targets()
        if not orders:
            return
        busy = [o for o in orders if o["status"] in ("receiving", "printing")]
        again = [o for o in orders if o["status"] == "ready"]
        todo = [o for o in orders if o["status"] in ("waiting", "ready")]
        if busy and not todo:
            messagebox.showinfo("Print shop", "The selected orders cannot be printed right now (files are still arriving or printing is in progress).", parent=win)
            return
        if again and not messagebox.askyesno("Print shop", f"{len(again)} of the selected orders have already been printed. Print them again?", parent=win):
            todo = [o for o in todo if o["status"] != "ready"]
        if not todo:
            return
        if len(todo) > 1 and not messagebox.askyesno(
                "Print shop", f"{len(todo)} orders will be printed one after another:\n" +
                ", ".join(f"#{o['token']} {o['customer']}" for o in todo) + "\n\nStart printing?", parent=win):
            return
        ids = [o["id"] for o in todo]
        checked.difference_update(ids)

        def run():
            fails = []
            for oid in ids:
                o = m.SHOP.get(oid)
                if not o:
                    continue
                ok, err = m.shop_print(oid)
                if not ok:
                    fails.append(f"#{o['token']} {o['customer']}: {err}")
            if fails:
                m.UI_EVENTS.put(("error", "These orders could not be printed:\n" + "\n".join(fails)))
        threading.Thread(target=run, daemon=True).start()
        refresh()

    def do_open():
        orders = targets()
        if not orders:
            return
        if any(o.get("private") for o in orders):
            messagebox.showinfo("Print shop", "Files in private orders cannot be opened because the customer did not allow viewing.\n"
                                              "You can only print them.", parent=win)
        for o in [o for o in orders if not o.get("private")][:3]:
            for f in o["files"][:5]:
                if os.path.exists(f["path"]):
                    _open_file(f["path"])

    def do_done():
        orders = targets()
        if not orders:
            return
        if len(orders) > 1 and not messagebox.askyesno("Print shop", f"Mark {len(orders)} orders as collected?", parent=win):
            return
        for o in orders:
            m.shop_done(o["id"])
            checked.discard(o["id"])

    def do_remove():
        orders = targets()
        if orders and messagebox.askyesno(
                "Print shop", "Decline these orders?\n" + ", ".join(f"#{o['token']} {o['customer']}" for o in orders) +
                "\n\nThe customer will see that the shop declined the order.", parent=win):
            for o in orders:
                m.SHOP.set_status(o["id"], "rejected", "The shop declined this order. Please ask at the counter.")
                checked.discard(o["id"])

    def do_move(how):
        s_ = tv.selection()
        if not s_:
            messagebox.showinfo("Print shop", "Click the order you want to move first.", parent=win)
            return
        o = m.SHOP.get(s_[0])
        if o and not m.SHOP.move(o["id"], how) and o["status"] not in ("waiting", "receiving"):
            messagebox.showinfo("Print shop", "Only orders that are still in the queue (not yet printed) can be moved.", parent=win)

    def do_amount():
        s_ = tv.selection()
        o = m.SHOP.get(s_[0]) if s_ else None
        if not o:
            messagebox.showinfo("Print shop", "Click the order whose amount you want to change first.", parent=win)
            return
        v = simpledialog.askstring("Change amount", f"Order #{o['token']} ({o['customer']})\n"
                                   f"Calculated amount: ₹{o.get('calc_total', o['total']):g}\n\n"
                                   "Enter the amount to charge (leave blank to use the calculated amount):",
                                   parent=win, initialvalue=f"{o['total']:g}")
        if v is None:
            return
        v = v.strip().replace("₹", "")
        if not v:
            m.SHOP.set_amount(o["id"], None)
            return
        try:
            amt = float(v)
            if amt < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Change amount", "Please enter a valid number (e.g. 50)", parent=win)
            return
        m.SHOP.set_amount(o["id"], amt)

    bb = tk.Frame(win, bg=C["light"], padx=14)
    bb.pack(fill="x", pady=(0, 6))
    lft = tk.Frame(bb, bg=C["light"])
    lft.pack(side="left")
    tk.Label(lft, text="Order:", font=(F, 9), fg=C["mut"], bg=C["light"]).pack(side="left", padx=(0, 4))
    for t, how in [("⤒ Move to top", "top"), ("▲ Up", "up"), ("▼ Down", "down")]:
        _btn(lft, t, lambda h=how: do_move(h), "white", C["text"], highlightthickness=1,
             highlightbackground=C["line"]).pack(side="left", padx=2)
    rgt = tk.Frame(bb, bg=C["light"])
    rgt.pack(side="right")
    _btn(rgt, "Decline", do_remove, "#fee2e2", C["red"]).pack(side="right", padx=(6, 0))
    _btn(rgt, "Change amount", do_amount, "white", C["text"], highlightthickness=1,
         highlightbackground=C["line"]).pack(side="right", padx=3)
    _btn(rgt, "Open files", do_open, "white", C["text"], highlightthickness=1,
         highlightbackground=C["line"]).pack(side="right", padx=3)
    _btn(rgt, "✓ Collected", do_done, C["green"], big=True).pack(side="right", padx=3)
    print_btn = _btn(rgt, "Print", do_print, C["purple2"], big=True)
    print_btn.pack(side="right", padx=3)

    foot = tk.Frame(win, bg=C["light"], padx=14)
    foot.pack(fill="x", pady=(0, 10))
    sel_lbl = tk.Label(foot, font=(F, 9), fg=C["mut"], bg=C["light"])
    sel_lbl.pack(side="left")

    def settings():
        d = tk.Toplevel(win)
        d.title("Shop settings")
        d.configure(bg="white", padx=18, pady=14)
        d.grab_set()
        vals = {}
        for i, (k, label) in enumerate([("name", "Shop name"), ("bw_rate", "Black & white price (₹ / sheet)"),
                                        ("color_rate", "Color price (₹ / sheet)"),
                                        ("message", "Message for customers\n(e.g. Closed 2-3 pm for lunch)")]):
            tk.Label(d, text=label, bg="white", font=(F, 10), justify="left").grid(row=i, column=0, sticky="w", pady=5)
            e = ttk.Entry(d, width=34)
            e.insert(0, str(S.get(k, "") if k != "name" else (S.get("name") or m.CFG["name"])))
            e.grid(row=i, column=1, pady=5, padx=(10, 0))
            vals[k] = e
        priv_var = tk.BooleanVar(value=S.get("allow_private", True))
        tk.Checkbutton(d, text="Accept private orders (customers can choose to hide their files; you can only print them)",
                       variable=priv_var, bg="white", font=(F, 9)).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

        def ok():
            try:
                bw, col = float(vals["bw_rate"].get()), float(vals["color_rate"].get())
                if bw < 0 or col < 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Shop settings", "Please enter valid prices (e.g. 2 or 1.5)", parent=d)
                return
            with m.CFG_LOCK:
                S.update(name=vals["name"].get().strip()[:40], bw_rate=bw, color_rate=col,
                         message=vals["message"].get().strip()[:150], allow_private=priv_var.get())
                m.save_config()
            refresh_head()
            refresh()
            d.destroy()
        _btn(d, "Save", ok, C["purple2"]).grid(row=5, column=1, sticky="e", pady=(12, 0))

    def copy_link():
        win.clipboard_clear()
        win.clipboard_append(m.shop_link())
        m.emit("Shop link copied")

    for t, cmd in [("Settings", settings), ("Poster (QR)", lambda: make_poster(m, win)), ("Copy link", copy_link),
                   ("Report", lambda: open_report(root, m))]:
        tk.Button(hb, text=t, command=cmd, bg="#8b5cf6", fg="white", activebackground="#a78bfa", activeforeground="white",
                  relief="flat", bd=0, cursor="hand2", font=(F, 9), padx=10, pady=3).pack(side="left", padx=(6, 0))

    tv.bind("<Double-1>", lambda e: (tv.identify_column(e.x) not in ("#0", f"#{len(cols)}")) and do_print())

    def tick():
        if not win.winfo_exists():
            return
        if m.SHOP_DIRTY.is_set():
            m.SHOP_DIRTY.clear()
            refresh()
        win.after(800, tick)

    refresh_head()
    set_filter("all")
    tick()

    def ask_enable():  # window dikhne ke baad hi poochho (warna sawaal chhupa reh jaata hai)
        win.lift()
        if not S.get("enabled") and messagebox.askyesno(
                "Print shop", "The shop is currently CLOSED. Open it now?\n\nWhen open, any customer can scan your shop QR code "
                              "and send files. You stay in control of what gets printed.", parent=win):
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
        messagebox.showerror("Poster", "Please set the server address first", parent=parent)
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
    center(320, "Print straight from your phone", _font(90, False), (237, 233, 254))
    q = qrcode.QRCode(border=2, box_size=10, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(link)
    q.make(fit=True)
    qi = q.make_image(fill_color="black", back_color="white").get_image().convert("RGB").resize((1400, 1400), Image.NEAREST)
    img.paste(qi, ((W - 1400) // 2, 650))
    d.rounded_rectangle([(W - 1400) // 2 - 20, 630, (W + 1400) // 2 + 20, 2070], radius=40, outline=purple, width=12)
    center(2110, "Scan with your phone camera", _font(95), (31, 41, 55))
    steps = ["1.  Scan the QR code and enter your name", "2.  Choose your files and tap 'Send to shop'",
             "3.  Get a token and collect your prints at the counter"]
    for i, s in enumerate(steps):
        d.text((260, 2330 + i * 150), s, font=_font(78, False), fill=(55, 65, 81))
    S = m.CFG["shop"]
    d.rounded_rectangle([260, 2860, W - 260, 3180], radius=40, fill=(245, 243, 255))
    center(2920, f"Black & white: Rs {S['bw_rate']:g} / page", _font(95), purple)
    center(3050, f"Color: Rs {S['color_rate']:g} / page", _font(95), purple)
    center(3300, "No app installation required  •  PrintBridge", _font(60, False), (120, 120, 120))
    path = os.path.join(m.DATA_DIR, "Shop_poster.pdf")
    img.save(path, "PDF", resolution=300)
    img.save(os.path.join(m.DATA_DIR, "Shop_poster.png"))
    m.emit(f"Poster created: {path}")
    if open_after:
        _open_file(path)
    return path


# =================================================================== REPORT (Office mode)
SRC = {"phone": "Phone", "shop": "Print shop", "local": "Offline", "self": "This computer"}


def open_report(root, m):
    win = _single("report", root, "PrintBridge - Usage report", "720x500")
    if win is None:
        return
    top = tk.Frame(win, bg="white", padx=12, pady=10)
    top.pack(fill="x")
    tk.Label(top, text="Report", font=(F, 15, "bold"), fg="#1a56db", bg="white").pack(side="left")
    period = tk.StringVar(value="today")
    for v, t in [("today", "Today"), ("week", "This week"), ("month", "This month"), ("all", "All time")]:
        ttk.Radiobutton(top, text=t, value=v, variable=period, command=lambda: refresh()).pack(side="left", padx=(16, 0))

    cols = ("naam", "src", "prints", "pages", "color", "bw", "rs")
    heads = ("Name", "Source", "Prints", "Total sheets", "Color", "B&W", "Revenue (shop)")
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
        total.configure(text=f"Total: {sum(d['prints'] for d in data)} prints  •  {sum(d['pages'] for d in data)} sheets  "
                             f"(Color {sum(d['color'] for d in data)}, B&W {sum(d['bw'] for d in data)})"
                             + (f"  •  Shop ₹{sum(d['amount'] for d in data):g}" if any(d['amount'] for d in data) else ""))

    def export():
        name = {"today": "today", "week": "week", "month": "month", "all": "all"}[period.get()]
        path = filedialog.asksaveasfilename(parent=win, defaultextension=".csv", filetypes=[("Excel / CSV", "*.csv")],
                                            initialfile=f"PrintBridge_report_{name}_{datetime.now():%Y%m%d}.csv")
        if path:
            n = m.us.export(path, period.get())
            messagebox.showinfo("Report", f"Saved {n} print records to:\n{path}\n\nYou can open this file in Excel.", parent=win)

    bb = tk.Frame(win, bg="white")
    bb.pack(fill="x", padx=12, pady=(0, 12))
    ttk.Button(bb, text="Export to Excel (CSV)", command=export).pack(side="left")
    ttk.Button(bb, text="Refresh", command=refresh).pack(side="left", padx=6)
    refresh()


# =================================================================== PHONES (niyam)
def open_phones(root, m, after_change=None):
    win = _single("phones", root, "PrintBridge - Connected devices", "700x440")
    if win is None:
        return
    tk.Label(win, text="Connected devices", font=(F, 15, "bold"), fg="#1a56db", bg="white").pack(anchor="w", padx=12, pady=(10, 0))
    tk.Label(win, text="Usage rules: disable color printing or set a daily sheet limit for individual devices.",
             font=(F, 9), fg="#555", bg="white").pack(anchor="w", padx=12)
    cols = ("naam", "juda", "aaj", "color", "limit")
    frame = tk.Frame(win, bg="white")
    frame.pack(fill="both", expand=True, padx=12, pady=8)
    tv = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
    for c, h, w in zip(cols, ("Name", "Connected on", "Sheets today", "Color", "Daily limit"), (200, 140, 100, 90, 100)):
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
                                                "Disabled" if r.get("no_color") else "Allowed",
                                                r.get("daily_limit") or "None"))
        if keep and tv.exists(keep[0]):
            tv.selection_set(keep[0])
        elif len(m.CFG["phones"]) == 1:  # ek hi phone ho to wahi chuna hua
            tv.selection_set(next(iter(m.CFG["phones"])))

    def sel():
        s = tv.selection()
        if not s:
            messagebox.showinfo("Devices", "Please select a device first", parent=win)
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
        v = simpledialog.askinteger("Daily limit", "Maximum sheets this device can print per day:\n(0 = no limit)",
                                    parent=win, minvalue=0, maxvalue=100000,
                                    initialvalue=m.CFG["rules"].get(h, {}).get("daily_limit") or 0)
        if v is not None:
            set_rule(h, daily_limit=v)

    def remove():
        h = sel()
        if h and messagebox.askyesno("Devices", f"Remove '{m.CFG['phones'][h].get('name')}'?\n"
                                               "It will need the Computer ID and PIN to connect again.", parent=win):
            with m.CFG_LOCK:
                m.CFG["phones"].pop(h, None)
                m.CFG["rules"].pop(h, None)
                m.save_config()
            m.emit("Device removed")
            refresh()
            after_change and after_change()

    bb = tk.Frame(win, bg="white")
    bb.pack(fill="x", padx=12, pady=(0, 12))
    ttk.Button(bb, text="Toggle color printing", command=toggle_color).pack(side="left")
    ttk.Button(bb, text="Daily limit…", command=limit).pack(side="left", padx=6)
    ttk.Button(bb, text="Remove device", command=remove).pack(side="left")
    refresh()
