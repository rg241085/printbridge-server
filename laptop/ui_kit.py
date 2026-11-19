"""PrintBridge - desktop UI kit (Tkinter).

Phone app jaisa saaf, modern look: same rang, gol kono wale buttons (hover ke saath), safed cards,
aur ttk widgets (dropdown, table, scrollbar) ki ek jaisi styling.
"""
import tkinter as tk
from tkinter import ttk

FONT = "Segoe UI"
C = {
    "blue": "#1a56db", "blue_dark": "#1e40af", "blue_soft": "#eef2ff", "blue_line": "#c7d2fe",
    "bg": "#f3f4f6", "card": "#ffffff", "line": "#e5e7eb", "text": "#111827", "mut": "#6b7280",
    "green": "#059669", "green_soft": "#dcfce7", "red": "#dc2626", "red_soft": "#fee2e2",
    "amber": "#b45309", "amber_soft": "#fef3c7", "purple": "#6d28d9", "purple2": "#7c3aed",
    "purple_soft": "#ede9fe", "yellow_soft": "#fef9c3",
}
KINDS = {  # bg, hover bg, text
    "primary": (C["blue"], C["blue_dark"], "#ffffff"),
    "secondary": ("#ffffff", C["blue_soft"], C["text"]),
    "soft": (C["blue_soft"], "#e0e7ff", C["blue"]),
    "ghost": (None, C["blue_soft"], C["blue"]),
    "danger": (C["red_soft"], "#fecaca", C["red"]),
    "success": (C["green"], "#047857", "#ffffff"),
    "purple": (C["purple2"], C["purple"], "#ffffff"),
    "purple_soft": (C["purple_soft"], "#ddd6fe", C["purple2"]),
    "on_dark": ("#8b5cf6", "#a78bfa", "#ffffff"),
    "open": (C["green_soft"], "#bbf7d0", "#065f46"),
    "closed": (C["red_soft"], "#fecaca", "#991b1b"),
    "amber": (C["amber_soft"], "#fde68a", C["amber"]),
}


def font(size=10, bold=False):
    return (FONT, size, "bold") if bold else (FONT, size)


def _parent_bg(w):
    try:
        return w.cget("bg")
    except tk.TclError:
        return C["bg"]


class Button(tk.Canvas):
    """Gol kono wala flat button. configure(text=, state=, command=, kind=) chalta hai."""

    def __init__(self, parent, text="", command=None, kind="primary", size=10, bold=True, padx=16, pady=8,
                 width=None, radius=8, border=None, **kw):
        self._bgparent = kw.pop("bg", None) or _parent_bg(parent)
        super().__init__(parent, highlightthickness=0, bd=0, bg=self._bgparent, cursor="hand2", **kw)
        self._text, self._cmd, self._kind = text, command, kind
        self._font = font(size, bold)
        self._padx, self._pady, self._radius, self._width = padx, pady, radius, width
        self._state, self._hover = "normal", False
        self._border = border if border is not None else (kind == "secondary")
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Configure>", lambda e: self._draw())
        self._size()

    def _size(self):
        tmp = self.create_text(0, 0, text=self._text, font=self._font)
        x1, y1, x2, y2 = self.bbox(tmp) or (0, 0, 0, 0)
        self.delete(tmp)
        w = self._width if self._width else (x2 - x1) + 2 * self._padx
        super().configure(width=w, height=(y2 - y1) + 2 * self._pady)
        self._draw()

    def _round(self, x1, y1, x2, y2, r, **kw):
        r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
               x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.create_polygon(pts, smooth=True, **kw)

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:
            w, h = int(super().cget("width")), int(super().cget("height"))
        bg, hov, fg = KINDS.get(self._kind, KINDS["primary"])
        fill = hov if (self._hover and self._state == "normal") else (bg or self._bgparent)
        if self._state == "disabled":
            fill, fg = ("#e5e7eb" if bg else self._bgparent), "#9ca3af"
        outline = C["line"] if self._border else fill
        self._round(1, 1, w - 1, h - 1, self._radius, fill=fill, outline=outline)
        self.create_text(w / 2, h / 2, text=self._text, font=self._font, fill=fg)

    def _set_hover(self, on):
        self._hover = on
        self._draw()

    def _click(self, e):
        if self._state != "normal" or not self._cmd:
            return
        if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self._cmd()

    def invoke(self):
        if self._state == "normal" and self._cmd:
            self._cmd()

    def configure(self, cnf=None, **kw):
        changed = False
        for k in ("text", "command", "state", "kind"):
            if k in kw:
                v = kw.pop(k)
                if k == "text":
                    self._text = v
                elif k == "command":
                    self._cmd = v
                elif k == "state":
                    self._state = "disabled" if v == "disabled" else "normal"
                    super().configure(cursor="arrow" if self._state == "disabled" else "hand2")
                else:
                    self._kind = v
                changed = True
        if cnf or kw:
            super().configure(cnf, **kw)
        if changed:
            self._size()
    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        return super().cget(key)

    def __getitem__(self, key):
        return self.cget(key)


def card(parent, padx=16, pady=14, **kw):
    """Safed card (patli border ke saath)."""
    return tk.Frame(parent, bg=C["card"], padx=padx, pady=pady, highlightbackground=C["line"],
                    highlightthickness=1, **kw)


def header(parent, title, subtitle="", color=None, right=None):
    """Upar rangeen patti (phone app jaisi). Lautata hai (frame, right_side_frame)."""
    color = color or C["blue"]
    h = tk.Frame(parent, bg=color, padx=20, pady=14)
    h.pack(fill="x")
    left = tk.Frame(h, bg=color)
    left.pack(side="left", fill="x", expand=True)
    t = tk.Label(left, text=title, font=font(17, True), fg="white", bg=color, anchor="w")
    t.pack(anchor="w")
    s = None
    if subtitle:
        s = tk.Label(left, text=subtitle, font=font(9), fg="#dbeafe" if color == C["blue"] else "#ede9fe",
                     bg=color, anchor="w", justify="left")
        s.pack(anchor="w", pady=(2, 0))
    r = tk.Frame(h, bg=color)
    r.pack(side="right")
    h.title_lbl, h.sub_lbl = t, s
    return h, r


def section(parent, text, bg=None):
    lbl = tk.Label(parent, text=text.upper(), font=font(8, True), fg=C["mut"], bg=bg or _parent_bg(parent))
    return lbl


def label(parent, text="", size=10, bold=False, color=None, **kw):
    return tk.Label(parent, text=text, font=font(size, bold), fg=color or C["text"], bg=kw.pop("bg", None) or
                    _parent_bg(parent), **kw)


def pill(parent, text="", fg=None, bg=None):
    return tk.Label(parent, text=text, font=font(9, True), fg=fg or C["green"], bg=bg or C["green_soft"],
                    padx=10, pady=3)


def listbox(parent, height=6, **kw):
    return tk.Listbox(parent, height=height, font=font(9), bd=0, relief="flat", highlightthickness=1,
                      highlightbackground=C["line"], highlightcolor=C["blue_line"], selectbackground=C["blue_soft"],
                      selectforeground=C["text"], activestyle="none", fg=C["text"], bg="#ffffff", **kw)


def _indicators(root):
    """Checkbox / radio ke saaf nishaan (tasveer), har computer par ek jaise."""
    from PIL import Image, ImageDraw, ImageTk
    blue, grey, k, n = (26, 86, 219), (156, 163, 175), 4, 18

    def make(on, radio):
        s = n * k
        im = Image.new("RGBA", (s + 6 * k, s), (0, 0, 0, 0))   # daayein thodi khaali jagah (label se doori)
        d = ImageDraw.Draw(im)
        if radio:
            d.ellipse([k, k, s - k, s - k], fill=(255, 255, 255), outline=blue if on else grey, width=2 * k)
            if on:
                d.ellipse([5 * k, 5 * k, s - 5 * k, s - 5 * k], fill=blue)
        else:
            d.rounded_rectangle([k, k, s - k, s - k], radius=4 * k, fill=blue if on else (255, 255, 255),
                                outline=blue if on else grey, width=2 * k)
            if on:
                d.line([(5 * k, 9.5 * k), (8 * k, 12.5 * k), (13.5 * k, 6 * k)], fill="white", width=int(2.4 * k), joint="curve")
        return ImageTk.PhotoImage(im.resize(((n + 6), n), Image.LANCZOS), master=root)
    imgs = {(on, r): make(on, r) for on in (False, True) for r in (False, True)}
    root._pb_indicator_imgs = imgs   # yaad rakho (warna Tk bhool jaata hai)
    return imgs


def setup_styles(root):
    """ttk widgets ki ek jaisi styling (clam theme par)."""
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    try:
        im = _indicators(root)
        st.element_create("pb.check", "image", im[(False, False)], ("selected", im[(True, False)]))
        st.element_create("pb.radio", "image", im[(False, True)], ("selected", im[(True, True)]))
        for cls, el in (("TCheckbutton", "pb.check"), ("TRadiobutton", "pb.radio")):
            base = cls[1:]
            st.layout(cls, [(f"{base}.padding", {"sticky": "nswe", "children": [
                (el, {"side": "left", "sticky": ""}),
                (f"{base}.focus", {"side": "left", "sticky": "w", "children": [(f"{base}.label", {"sticky": "nswe"})]})]})])
    except Exception:
        pass
    root.option_add("*Font", font(10))
    root.option_add("*TCombobox*Listbox.font", font(10))
    root.option_add("*TCombobox*Listbox.selectBackground", C["blue_soft"])
    root.option_add("*TCombobox*Listbox.selectForeground", C["text"])
    st.configure(".", font=font(10), background=C["card"], foreground=C["text"])
    st.configure("TCombobox", padding=(8, 5), fieldbackground="#ffffff", background="#ffffff", bordercolor=C["line"],
                 lightcolor=C["line"], darkcolor=C["line"], arrowcolor=C["mut"], arrowsize=13)
    st.map("TCombobox", fieldbackground=[("readonly", "#ffffff")], selectbackground=[("readonly", "#ffffff")],
           selectforeground=[("readonly", C["text"])], bordercolor=[("focus", C["blue_line"])],
           lightcolor=[("focus", C["blue_line"])])
    st.configure("TEntry", padding=(8, 5), fieldbackground="#ffffff", bordercolor=C["line"], lightcolor=C["line"],
                 darkcolor=C["line"])
    st.map("TEntry", bordercolor=[("focus", C["blue_line"])], lightcolor=[("focus", C["blue_line"])])
    st.configure("TSpinbox", padding=(6, 4), fieldbackground="#ffffff", bordercolor=C["line"], lightcolor=C["line"],
                 darkcolor=C["line"], arrowcolor=C["mut"], background="#ffffff")
    for bg_name, bg in (("", C["card"]), ("Bg.", C["bg"])):
        st.configure(f"{bg_name}TCheckbutton", background=bg, foreground=C["text"], font=font(9), focuscolor=bg,
                     indicatorbackground="#ffffff", indicatorforeground=C["blue"], bordercolor=C["line"])
        st.map(f"{bg_name}TCheckbutton", background=[("active", bg)],
               indicatorbackground=[("selected", C["blue"]), ("active", C["blue_soft"])],
               indicatorforeground=[("selected", "#ffffff")])
        st.configure(f"{bg_name}TRadiobutton", background=bg, foreground=C["text"], font=font(10), focuscolor=bg,
                     indicatorbackground="#ffffff", bordercolor=C["line"])
        st.map(f"{bg_name}TRadiobutton", background=[("active", bg)],
               indicatorbackground=[("selected", C["blue"]), ("active", C["blue_soft"])])
    st.configure("Vertical.TScrollbar", background="#d1d5db", troughcolor=C["card"], bordercolor=C["card"],
                 lightcolor="#d1d5db", darkcolor="#d1d5db", arrowcolor=C["mut"], gripcount=0, arrowsize=11)
    st.configure("Treeview", rowheight=36, font=font(10), background="#ffffff", fieldbackground="#ffffff",
                 foreground=C["text"], bordercolor=C["line"], borderwidth=0)
    st.configure("Treeview.Heading", font=font(9, True), background="#f9fafb", foreground=C["mut"], relief="flat",
                 padding=(8, 8), bordercolor=C["line"], lightcolor="#f9fafb", darkcolor=C["line"])
    st.map("Treeview.Heading", background=[("active", "#f3f4f6")])
    st.map("Treeview", background=[("selected", C["blue_soft"])], foreground=[("selected", C["text"])])
    st.configure("TNotebook", background=C["card"], borderwidth=0, tabmargins=(0, 4, 0, 0))
    st.configure("TNotebook.Tab", padding=(16, 8), font=font(10), background=C["bg"], foreground=C["mut"],
                 bordercolor=C["line"], lightcolor=C["bg"])
    st.map("TNotebook.Tab", background=[("selected", C["card"])], foreground=[("selected", C["blue"])],
           lightcolor=[("selected", C["card"])])
    return st


def field(parent, text, widget_fn, row, col=0, hint=None, bg=None, span=1):
    """Form ki ek line: label (upar) + widget."""
    bg = bg or _parent_bg(parent)
    f = tk.Frame(parent, bg=bg)
    f.grid(row=row, column=col, columnspan=span, sticky="ew", padx=(0, 16), pady=(0, 10))
    tk.Label(f, text=text, font=font(9), fg=C["mut"], bg=bg).pack(anchor="w")
    w = widget_fn(f)
    w.pack(anchor="w", fill="x", pady=(3, 0))
    if hint:
        tk.Label(f, text=hint, font=font(8), fg=C["mut"], bg=bg, justify="left", wraplength=320).pack(anchor="w", pady=(2, 0))
    return w
