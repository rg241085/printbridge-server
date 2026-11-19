"""PrintBridge - laptop se print: isi laptop ke printer par, ya kisi DOOSRE laptop ke printer par.

Jaise: printer office wale laptop se juda hai, aur aap apne laptop se print dena chahte hain.
Doosre laptop ka ID + PIN ek baar daal kar jodiye, phir bas file chuniye aur Print dabaiye.
"""
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import sender
import ui_kit as ui

F = "Segoe UI"
C = {"blue": "#1a56db", "light": "#f5f7fb", "line": "#e5e7eb", "text": "#111827", "mut": "#6b7280",
     "green": "#059669", "red": "#dc2626", "amber": "#b45309"}
SELF = "__self__"
FILE_TYPES = [("Printable files", "*.pdf *.jpg *.jpeg *.png *.bmp *.gif *.webp *.txt *.doc *.docx *.xls *.xlsx "
                                        "*.ppt *.pptx *.rtf *.odt"), ("All files", "*.*")]
PAGES = {"All pages": "all", "Odd pages (1, 3, 5…)": "odd", "Even pages (2, 4, 6…)": "even"}
DUPLEX = {"Off (one-sided)": "off", "On - long edge": "long", "On - short edge": "short"}
PAPER = {"Printer default": "default", "A4": "a4", "Letter": "letter", "Legal": "legal", "A3": "a3", "A5": "a5"}

_open = {}


def open_send(root, m, files=None):
    """Window kholo (pehle se khuli ho to wahi saamne laao aur files jod do)."""
    w = _open.get("win")
    if w is not None and w.winfo_exists():
        w.deiconify()
        w.lift()
        w.focus_force()
        if files:
            w._add_files(files)
        return w
    win = tk.Toplevel(root)
    _open["win"] = win
    win.title("PrintBridge - Print")
    win.configure(bg=ui.C["bg"])
    sh = win.winfo_screenheight()
    win.geometry(f"680x{min(760, sh - 80)}+60+20")
    win.minsize(540, 560)
    targets = m.CFG.setdefault("targets", [])
    files_list = []
    q = queue.Queue()
    state = {"busy": False, "printers_for": None}

    # ------------------------------------------------ header
    ui.header(win, "Print files", "Print on this computer, or on a printer connected to another computer over the internet")

    # neeche ka Print button pehle pack (taaki chhoti screen par bhi hamesha dikhe)
    foot = tk.Frame(win, bg=ui.C["bg"], padx=16, pady=12)
    foot.pack(side="bottom", fill="x")
    go = ui.Button(foot, "Print", None, size=12, pady=11)
    go.pack(fill="x")
    log = ui.listbox(foot, height=4)
    log.pack(fill="x", pady=(10, 0))

    body = tk.Frame(win, bg=ui.C["bg"], padx=16, pady=12)
    body.pack(fill="both", expand=True)

    def section(text):
        cd = ui.card(body, pady=12)
        cd.pack(fill="x", pady=(0, 10))
        tk.Label(cd, text=text.upper(), font=(F, 8, "bold"), fg=C["mut"], bg="white").pack(anchor="w", pady=(0, 6))
        return cd

    # ------------------------------------------------ 1. files
    c_files = section("1. Files")
    fr = tk.Frame(c_files, bg="white")
    fr.pack(fill="x")
    lb = ui.listbox(fr, height=4, selectmode="extended")
    lb.configure(font=(F, 10))
    lb.pack(side="left", fill="x", expand=True)
    fb = tk.Frame(fr, bg="white")
    fb.pack(side="left", padx=(8, 0), anchor="n")

    def show_files():
        lb.delete(0, "end")
        for p in files_list:
            try:
                kb = os.path.getsize(p) / 1024
                size = f"{kb / 1024:.1f} MB" if kb > 1024 else f"{max(1, round(kb))} KB"
            except OSError:
                size = "?"
            lb.insert("end", f"  {os.path.basename(p)}    ({size})")
        if not files_list:
            lb.insert("end", "  (no files yet — click 'Add files')")
            lb.itemconfig(0, fg=C["mut"])
        go.configure(text=f"Print {len(files_list)} files" if len(files_list) > 1 else "Print")

    def add_files(paths):
        bad = []
        for p in paths:
            p = os.path.abspath(str(p).strip('"'))
            if not os.path.isfile(p):
                continue
            if os.path.splitext(p)[1].lower() not in m.pb.ALLOWED_EXT:
                bad.append(os.path.basename(p))
            elif p not in files_list:
                files_list.append(p)
        show_files()
        if bad:
            messagebox.showwarning("Print", "These files cannot be printed:\n" + "\n".join(bad[:8]), parent=win)
    win._add_files = add_files

    def pick():
        paths = filedialog.askopenfilenames(parent=win, title="Choose files to print", filetypes=FILE_TYPES)
        if paths:
            add_files(paths)

    def remove_sel():
        for i in reversed(lb.curselection()):
            if i < len(files_list):
                files_list.pop(i)
        show_files()

    for t, cmd in [("+ Add files", pick), ("Remove", remove_sel), ("Clear all", lambda: (files_list.clear(), show_files()))]:
        ui.Button(fb, t, cmd, kind="primary" if t.startswith("+") else "secondary", size=9, bold=t.startswith("+"),
                  width=120, pady=6).pack(pady=(0, 6))

    # ------------------------------------------------ 2. kahan
    c_prn = section("2. Choose a printer")
    tr = tk.Frame(c_prn, bg="white")
    tr.pack(fill="x")
    tbox = ttk.Combobox(tr, state="readonly", width=32)
    tbox.pack(side="left")
    ui.Button(tr, "+ Add computer", lambda: pair_dialog(), kind="soft", size=9, pady=6).pack(side="left", padx=(8, 0))
    ui.Button(tr, "Remove", lambda: remove_target(), kind="secondary", size=9, bold=False, pady=6).pack(side="left", padx=(6, 0))
    tstat = tk.Label(c_prn, font=(F, 9), bg="white", fg=C["mut"], anchor="w")
    tstat.pack(fill="x", pady=(4, 0))
    pr = tk.Frame(c_prn, bg="white")
    pr.pack(fill="x", pady=(6, 0))
    tk.Label(pr, text="Printer", font=(F, 9), fg=C["mut"], bg="white").pack(side="left")
    pvar = tk.StringVar(win)
    win._vars = [pvar]   # Tk variable ko zinda rakho (warna dabba khaali dikhta hai)
    pbox = ttk.Combobox(pr, textvariable=pvar, state="readonly", width=46)
    pbox.pack(side="left", padx=(6, 0))

    def choices():
        return [("This computer (local printers)", SELF)] + [
            (f"{t.get('name') or t['id']}  ({t['id']})", t["id"]) for t in targets]

    def fill_targets(select=None):
        ch = choices()
        tbox["values"] = [c[0] for c in ch]
        want = select or m.CFG.get("send_last") or SELF
        idx = next((i for i, c in enumerate(ch) if c[1] == want), 0)
        tbox.current(idx)
        on_target()

    def cur_target():
        ch = choices()
        i = tbox.current()
        tid = ch[i][1] if 0 <= i < len(ch) else SELF
        return None if tid == SELF else next((t for t in targets if t["id"] == tid), None)

    def on_target(_e=None):
        t = cur_target()
        with m.CFG_LOCK:
            m.CFG["send_last"] = t["id"] if t else SELF
            m.save_config()
        pbox["values"] = []
        pvar.set("…looking for printers")
        tstat.configure(text="", fg=C["mut"])
        state["printers_for"] = t["id"] if t else SELF
        threading.Thread(target=load_printers, args=(t,), daemon=True).start()
    tbox.bind("<<ComboboxSelected>>", on_target)

    def load_printers(t):
        key = t["id"] if t else SELF
        try:
            if t is None:
                names, default, status, _caps = m.pb.list_printers()
                default = m.CFG.get("printer") if m.CFG.get("printer") in names else default
                q.put(("printers", key, names, default, "Local printers", status))
                return
            d = sender.device(m.server_base(), t["id"])
            if not d.get("online"):
                q.put(("printers", key, [], None, "offline", {}))
                return
            names, default, status = sender.printers(m.server_base(), t)
            q.put(("printers", key, names, default, f"● Online: {d.get('name') or t['id']}", status))
        except sender.SendError as e:
            if getattr(e, "code", 0) == 401:
                q.put(("printers", key, [], None, "unpaired", {}))
            else:
                q.put(("printers", key, [], None, "err:" + str(e), {}))
        except Exception as e:
            q.put(("printers", key, [], None, "err:" + str(e), {}))

    def remove_target():
        t = cur_target()
        if not t:
            return
        if messagebox.askyesno("Remove computer", f"Remove '{t.get('name')}' ({t['id']}) from the list?", parent=win):
            targets[:] = [x for x in targets if x["id"] != t["id"]]
            with m.CFG_LOCK:
                m.save_config()
            fill_targets(SELF)

    def pair_dialog(prefill=""):
        d = tk.Toplevel(win)
        d.title("Add computer")
        d.configure(bg="white", padx=22, pady=16)
        d.transient(win)
        d.grab_set()
        tk.Label(d, text="Open PrintBridge on the computer that the printer is connected to.\n"
                         "Enter the Computer ID and PIN shown there.",
                 font=(F, 10), bg="white", justify="left").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        tk.Label(d, text="Computer ID", font=(F, 10), bg="white").grid(row=1, column=0, sticky="w")
        e_id = ttk.Entry(d, width=22, font=("Consolas", 12))
        e_id.insert(0, prefill or "PB-")
        e_id.grid(row=1, column=1, pady=4, sticky="w")
        tk.Label(d, text="PIN", font=(F, 10), bg="white").grid(row=2, column=0, sticky="w")
        e_pin = ttk.Entry(d, width=22, font=("Consolas", 12))
        e_pin.grid(row=2, column=1, pady=4, sticky="w")
        info = tk.Label(d, text="", font=(F, 9), bg="white", fg=C["mut"], justify="left", wraplength=340)
        info.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        btn = ui.Button(d, "Connect", None, pady=7)
        btn.grid(row=4, column=1, sticky="e", pady=(10, 0))
        e_id.focus_set()
        e_id.icursor("end")

        def done(res):
            if not d.winfo_exists():
                return
            btn.configure(state="normal")
            if isinstance(res, Exception):
                info.configure(text=str(res), fg=C["red"])
                return
            targets[:] = [x for x in targets if x["id"] != res["id"]] + [res]
            with m.CFG_LOCK:
                m.save_config()
            d.destroy()
            fill_targets(res["id"])
            add_log(f"Connected: {res['name']} ({res['id']})")

        def go_pair(_e=None):
            tid = sender.norm_id(e_id.get())
            pin = "".join(ch for ch in e_pin.get() if ch.isdigit())
            if not tid:
                info.configure(text="Enter a valid Computer ID (e.g. PB-7K3M-9QX2)", fg=C["red"])
                return
            if tid == m.CFG["device_id"]:
                info.configure(text="This is the ID of this computer. To use local printers, "
                                    "choose 'This computer' above.", fg=C["red"])
                return
            if len(pin) != 6:
                info.configure(text="The PIN must be 6 digits", fg=C["red"])
                return
            btn.configure(state="disabled")
            info.configure(text="Connecting… you may need to click 'Allow' on the other computer.", fg=C["mut"])

            def run():
                try:
                    res = sender.pair(m.server_base(), tid, pin, "PC: " + (m.CFG.get("name") or "Computer"))
                except Exception as e:
                    res = e
                q.put(("paired", done, res))   # window wale thread mein hi jawab dikhao
            threading.Thread(target=run, daemon=True).start()
        btn.configure(command=go_pair)
        d.bind("<Return>", go_pair)
        return d
    win._pair_dialog = pair_dialog

    # ------------------------------------------------ 3. settings
    c_set = section("3. Settings")
    sg = tk.Frame(c_set, bg="white")
    sg.pack(fill="x")
    for i in range(4):
        sg.columnconfigure(i, weight=1 if i % 2 else 0)

    def lbl(r, c, t):
        tk.Label(sg, text=t, font=(F, 9), fg=C["mut"], bg="white").grid(row=r, column=c, sticky="w", padx=(0 if c == 0 else 14, 6), pady=3)

    lbl(0, 0, "Copies")
    copies = ttk.Spinbox(sg, from_=1, to=50, width=6, justify="center")
    copies.set(1)
    copies.grid(row=0, column=1, sticky="w")
    lbl(0, 2, "Color")
    color = tk.StringVar(win, value="color")
    win._vars.append(color)
    cf = tk.Frame(sg, bg="white")
    cf.grid(row=0, column=3, sticky="w")
    for t, v in [("Color", "color"), ("Black & white", "bw")]:
        ttk.Radiobutton(cf, text=t, variable=color, value=v).pack(side="left", padx=(0, 10))
    lbl(1, 0, "Pages")
    pages = ttk.Combobox(sg, values=list(PAGES), width=18)
    pages.set("All pages")
    pages.grid(row=1, column=1, sticky="w")
    lbl(1, 2, "Pages per sheet")
    nup = ttk.Combobox(sg, values=["1", "2", "4"], state="readonly", width=10)
    nup.set("1")
    nup.grid(row=1, column=3, sticky="w")
    lbl(2, 0, "Two-sided")
    duplex = ttk.Combobox(sg, values=list(DUPLEX), state="readonly", width=18)
    duplex.set("Off (one-sided)")
    duplex.grid(row=2, column=1, sticky="w")
    lbl(2, 2, "Paper size")
    paper = ttk.Combobox(sg, values=list(PAPER), state="readonly", width=12)
    paper.set("Printer default")
    paper.grid(row=2, column=3, sticky="w")
    lbl(3, 0, "PDF password")
    pw = ttk.Entry(sg, width=20, show="•")
    pw.grid(row=3, column=1, sticky="w")
    tk.Label(sg, text="Custom pages: e.g. '1-3, 5'", font=(F, 8), fg=C["mut"], bg="white").grid(
        row=3, column=2, columnspan=2, sticky="w", padx=(14, 0))

    def opts():
        pg = pages.get().strip()
        return {"pages": PAGES.get(pg, pg or "all"), "color": color.get(), "nup": int(nup.get().split()[0]),
                "duplex": DUPLEX.get(duplex.get(), "off"), "paper": PAPER.get(paper.get(), "default"),
                "password": pw.get()}

    # ------------------------------------------------ print
    def add_log(text):
        log.insert(0, text)

    def do_print():
        if state["busy"]:
            return
        if not files_list:
            pick()
            if not files_list:
                return
        t = cur_target()
        printer = pvar.get()
        vals = pbox["values"]
        vals = (vals,) if isinstance(vals, str) else tuple(vals or ())
        if printer not in vals:
            messagebox.showinfo("Print", "Please select a printer (or wait for the computer to come online).", parent=win)
            return
        try:
            n = max(1, min(int(copies.get()), 50))
        except ValueError:
            n = 1
        o, todo = opts(), list(files_list)
        state["busy"] = True
        go.configure(state="disabled", text="Sending…")

        def run():
            ok = 0
            for path in todo:
                name = os.path.basename(path)
                try:
                    if t is None:
                        q.put(("log", f"Printing: {name}"))
                        m.self_print(path, printer, n, o)
                        q.put(("log", f"Printed: {name}  ->  {printer}"))
                        ok += 1
                    else:
                        q.put(("log", f"Sending: {name}  ->  {t.get('name')}"))
                        jid = sender.send_file(m.server_base(), t, path, n, printer, o, "PC: " + (m.CFG.get("name") or "Computer"))
                        st, msg = sender.wait_job(m.server_base(), jid, lambda s, mm: q.put(("log", f"   {name}: {mm}")))
                        if st == "done":
                            ok += 1
                            q.put(("log", f"Printed: {name}  ->  {t.get('name')} / {printer}"))
                        else:
                            q.put(("log", f"FAILED: {name}: {msg}"))
                except Exception as e:
                    q.put(("log", f"FAILED: {name}: {e}"))
            q.put(("done", ok, len(todo)))
        threading.Thread(target=run, daemon=True).start()
    go.configure(command=do_print)

    def poll():
        if not win.winfo_exists():
            return
        while not q.empty():
            ev = q.get()
            if ev[0] == "log":
                add_log(ev[1])
            elif ev[0] == "done":
                state["busy"] = False
                show_files()
                go.configure(state="normal")
                ok, total = ev[1], ev[2]
                if ok == total:
                    files_list.clear()
                    show_files()
                    add_log(f"✓ {ok} {'file' if ok == 1 else 'files'} printed")
                else:
                    messagebox.showwarning("Print", f"{total - ok} of {total} files could not be printed. See the log below for details.", parent=win)
            elif ev[0] == "paired":
                ev[1](ev[2])
            elif ev[0] == "printers":
                _, key, names, default, note, status = ev
                if key != state["printers_for"]:
                    continue
                pbox["values"] = names
                if names:
                    pvar.set(default if default in names else names[0])
                else:
                    pvar.set("")
                if note == "offline":
                    tstat.configure(text="● This computer is OFFLINE. Start PrintBridge on it, then select it "
                                         "here again.", fg=C["red"])
                elif note == "unpaired":
                    tstat.configure(text="● That computer has removed this one. Use '+ Add computer' to "
                                         "connect again.", fg=C["red"])
                elif note.startswith("err:"):
                    tstat.configure(text="● " + note[4:], fg=C["red"])
                elif not names:
                    tstat.configure(text="● No printers found on this computer", fg=C["amber"])
                else:
                    tstat.configure(text=note if note.startswith("●") else "● " + note, fg=C["green"])
        win.after(250, poll)

    show_files()
    fill_targets()
    if files:
        add_files(files)
    poll()
    return win
