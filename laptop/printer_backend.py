"""
PrintBridge - printing ka hissa (v0.6)

Har file ko "pages" (images) mein badal kar chhaapte hain, taaki saari settings har file par chalein:
  - Photo (jpg/png/...)      -> 1 page
  - PDF                      -> program ke andar hi har page image banta hai (pypdfium2)
  - Word/Excel/PowerPoint    -> pehle PDF banate hain (MS Office ya LibreOffice se), phir PDF jaisa
  - TXT ya conversion fail   -> purana tareeka: file ki default app se print (settings nahi chalti)

Print settings (opts):
  pages     : "all" | "odd" | "even" | "1-3, 5, 8-"
  color     : "color" | "bw"
  nup       : 1 | 2 | 4        (ek kaagaz par kitne page)
  fit       : "fit" | "actual" (page par fit, ya asli size)
  reverse   : True/False       (aakhri page pehle)

Linux/Mac par: CUPS ka `lp` (settings: pages, bw, nup, reverse ke lp options).
Test mode (PRINTBRIDGE_TEST=1): printer ki jagah "test_prints" folder mein har kaagaz ki PNG.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

IS_WIN = sys.platform == "win32"
TEST_MODE = os.environ.get("PRINTBRIDGE_TEST") == "1"
OUT_DIR = os.path.join(os.getcwd(), "test_prints")

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
PDF_EXT = {".pdf"}
OFFICE_EXT = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf", ".odt"}
TEXT_EXT = {".txt"}
ALLOWED_EXT = IMAGE_EXT | PDF_EXT | OFFICE_EXT | TEXT_EXT

DPI = 300
MAX_PAGES = 500
A4 = (2480, 3508)  # 300 DPI par A4 (pixels)
TEST_PRINTER = "Test Printer (sirf folder me save)"

DEFAULT_OPTS = {"pages": "all", "color": "color", "nup": 1, "fit": "fit", "reverse": False}


class PrintError(Exception):
    pass


# ---------------------------------------------------------------- settings
def clean_opts(opts):
    """Phone se aayi settings ko surakshit roop mein badlo."""
    o = dict(DEFAULT_OPTS)
    opts = opts if isinstance(opts, dict) else {}
    pages = str(opts.get("pages", "all")).strip().lower()[:100] or "all"
    o["pages"] = pages
    o["color"] = "bw" if opts.get("color") == "bw" else "color"
    try:
        o["nup"] = int(opts.get("nup", 1)) if int(opts.get("nup", 1)) in (1, 2, 4) else 1
    except (TypeError, ValueError):
        o["nup"] = 1
    o["fit"] = "actual" if opts.get("fit") == "actual" else "fit"
    o["reverse"] = bool(opts.get("reverse"))
    return o


def select_pages(spec, n):
    """'all' / 'odd' / 'even' / '1-3, 5, 8-'  ->  0 se shuru hone wale page numbers ki list."""
    spec = (spec or "all").strip().lower()
    if spec in ("all", "saare", ""):
        return list(range(n))
    if spec == "odd":
        return list(range(0, n, 2))
    if spec == "even":
        sel = list(range(1, n, 2))
        if not sel:
            raise PrintError("Is file mein sirf 1 page hai, even page nahi hai")
        return sel
    out = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                a = int(a) if a else 1
                b = int(b) if b else n
            else:
                a = b = int(part)
        except ValueError:
            raise PrintError(f"Page number samajh nahi aaya: '{part}' (jaise 1-3, 5 likhiye)")
        if a < 1 or b < a:
            raise PrintError(f"Galat page range: '{part}'")
        if a > n:
            raise PrintError(f"File mein sirf {n} page hain, page {a} nahi hai")
        out.extend(range(a - 1, min(b, n)))
    if not out:
        raise PrintError("Koi page nahi chuna gaya")
    seen = set()
    return [i for i in out if not (i in seen or seen.add(i))]


# ---------------------------------------------------------------- documents
class Doc:
    """Kisi bhi file ke pages: count aur render(i) -> PIL image."""

    def __init__(self, path, ext):
        self.ext = ext
        self.pdf = None
        self.img = None
        if ext in IMAGE_EXT:
            from PIL import Image, ImageOps
            try:
                self.img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
            except Exception as e:
                raise PrintError("Photo khul nahi paayi") from e
            self.count = 1
        else:
            import pypdfium2 as pdfium
            try:
                self.pdf = pdfium.PdfDocument(path)
            except Exception as e:
                raise PrintError("PDF khul nahi paayi (shayad kharab ya password wali hai)") from e
            self.count = len(self.pdf)
            if self.count == 0:
                raise PrintError("PDF mein koi page nahi hai")
            if self.count > MAX_PAGES:
                raise PrintError(f"File mein {self.count} page hain (max {MAX_PAGES})")

    def render(self, i, dpi=DPI):
        """Page i ka image, aur uska asli size inch mein (fit='actual' ke liye)."""
        if self.img is not None:
            return self.img.copy(), None
        page = self.pdf[i]
        try:
            w, h = page.get_size()
            scale = dpi / 72
            if w * h * scale * scale > 40_000_000:
                scale = (40_000_000 / (w * h)) ** 0.5
            img = page.render(scale=scale).to_pil().convert("RGB")
            return img, (w / 72, h / 72)
        finally:
            page.close()

    def close(self):
        if self.pdf is not None:
            self.pdf.close()


def compose_sheets(doc, opts, dpi=DPI):
    """Chune hue pages ko kaagaz (sheet) mein badlo. Har sheet: (image, asli size inch mein ya None)."""
    from PIL import Image
    sel = select_pages(opts["pages"], doc.count)
    if opts["reverse"]:
        sel = sel[::-1]
    nup = opts["nup"]
    for k in range(0, len(sel), nup):
        group = sel[k:k + nup]
        if nup == 1:
            img, inches = doc.render(group[0], dpi)
        else:
            # 2 page: aada (landscape) kaagaz par agal-bagal. 4 page: seedhe kaagaz par 2x2.
            sw, sh = (A4[1], A4[0]) if nup == 2 else A4
            if dpi != DPI:
                sw, sh = int(sw * dpi / DPI), int(sh * dpi / DPI)
            cols, rows = (2, 1) if nup == 2 else (2, 2)
            sheet = Image.new("RGB", (sw, sh), "white")
            gap = int(sw * 0.02)
            cw, ch = (sw - gap * (cols + 1)) // cols, (sh - gap * (rows + 1)) // rows
            for j, idx in enumerate(group):
                pg, _ = doc.render(idx, dpi // 2 if nup == 4 else int(dpi * 0.7))
                s = min(cw / pg.width, ch / pg.height)
                pg = pg.resize((max(1, int(pg.width * s)), max(1, int(pg.height * s))), Image.LANCZOS)
                cx = gap + (j % cols) * (cw + gap) + (cw - pg.width) // 2
                cy = gap + (j // cols) * (ch + gap) + (ch - pg.height) // 2
                sheet.paste(pg, (cx, cy))
            img, inches = sheet, None
        if opts["color"] == "bw":
            img = img.convert("L").convert("RGB")
        yield img, inches


# ---------------------------------------------------------------- Office -> PDF
def find_soffice():
    for c in [shutil.which("soffice"), shutil.which("libreoffice"),
              os.path.join(os.environ.get("ProgramFiles", ""), "LibreOffice", "program", "soffice.exe"),
              os.path.join(os.environ.get("ProgramFiles(x86)", ""), "LibreOffice", "program", "soffice.exe")]:
        if c and os.path.isfile(c):
            return c
    return None


def office_to_pdf(path, ext):
    """Word/Excel/PowerPoint file ko PDF banao. Nahi bana to None."""
    out_dir = tempfile.mkdtemp(prefix="pb_conv_")
    out = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + ".pdf")
    if IS_WIN:
        try:
            _ms_office_to_pdf(path, ext, out)
            if os.path.exists(out):
                return out
        except Exception:
            pass
    soffice = find_soffice()
    if soffice:
        try:
            subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", out_dir, path],
                           capture_output=True, timeout=180,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if os.path.exists(out):
                return out
        except Exception:
            pass
    return None


def _ms_office_to_pdf(path, ext, out):
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    path, out = os.path.abspath(path), os.path.abspath(out)
    try:
        if ext in (".doc", ".docx", ".rtf", ".odt"):
            app = win32com.client.DispatchEx("Word.Application")
            app.Visible = False
            app.DisplayAlerts = 0
            try:
                doc = app.Documents.Open(path, ReadOnly=True, AddToRecentFiles=False)
                doc.ExportAsFixedFormat(out, 17)  # 17 = PDF
                doc.Close(False)
            finally:
                app.Quit()
        elif ext in (".xls", ".xlsx"):
            app = win32com.client.DispatchEx("Excel.Application")
            app.Visible = False
            app.DisplayAlerts = False
            try:
                wb = app.Workbooks.Open(path, ReadOnly=True)
                wb.ExportAsFixedFormat(0, out)  # 0 = PDF
                wb.Close(False)
            finally:
                app.Quit()
        elif ext in (".ppt", ".pptx"):
            app = win32com.client.DispatchEx("PowerPoint.Application")
            try:
                pres = app.Presentations.Open(path, ReadOnly=True, WithWindow=False)
                pres.SaveAs(out, 32)  # 32 = PDF
                pres.Close()
            finally:
                app.Quit()
    finally:
        pythoncom.CoUninitialize()


# ---------------------------------------------------------------- printers
def list_printers():
    """(printers ki list, default printer, {naam: 'offline'/'ready'}) lautata hai."""
    if TEST_MODE:
        return [TEST_PRINTER], TEST_PRINTER, {TEST_PRINTER: "ready"}
    if IS_WIN:
        import win32print
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        names = [p[2] for p in win32print.EnumPrinters(flags)]
        try:
            default = win32print.GetDefaultPrinter()
        except Exception:
            default = names[0] if names else None
        status = {n: _win_printer_status(n) for n in names}
        return names, default, status
    names, default = [], None
    try:
        out = subprocess.run(["lpstat", "-e"], capture_output=True, text=True, timeout=10).stdout
        names = [l.strip() for l in out.splitlines() if l.strip()]
        out = subprocess.run(["lpstat", "-d"], capture_output=True, text=True, timeout=10).stdout
        if ":" in out:
            default = out.split(":", 1)[1].strip() or None
    except Exception:
        pass
    if default not in names:
        default = names[0] if names else None
    return names, default, {n: "ready" for n in names}


def _win_printer_status(name):
    import win32print
    try:
        h = win32print.OpenPrinter(name)
        try:
            info = win32print.GetPrinter(h, 2)
        finally:
            win32print.ClosePrinter(h)
        offline = (info.get("Attributes", 0) & 0x400) or (info.get("Status", 0) & 0x80)  # WORK_OFFLINE / OFFLINE
        return "offline" if offline else "ready"
    except Exception:
        return "ready"


# ---------------------------------------------------------------- printing
def print_file(path, printer, copies=1, title="PrintBridge", opts=None):
    ext = os.path.splitext(path)[1].lower()
    if ext not in ALLOWED_EXT:
        raise PrintError(f"'{ext}' file print nahi ho sakti")
    copies = max(1, min(int(copies), 50))
    opts = clean_opts(opts)
    if not TEST_MODE and not printer:
        raise PrintError("Laptop par koi printer nahi mila")

    if not IS_WIN and not TEST_MODE:
        return _cups_print(path, printer, copies, title, opts)

    # Office file -> PDF (taaki saari settings chalein)
    note = ""
    work = path
    if ext in OFFICE_EXT:
        pdf = None if TEST_MODE else office_to_pdf(path, ext)
        if pdf:
            work, ext = pdf, ".pdf"
        else:
            note = " (is file par print settings nahi chali)"
    if ext in TEXT_EXT or ext in OFFICE_EXT:
        if TEST_MODE:
            os.makedirs(OUT_DIR, exist_ok=True)
            shutil.copy(path, os.path.join(OUT_DIR, os.path.basename(path)))
            return f"TEST: file save hui{note}"
        _win_shell_print(path, printer, copies)
        return f"{copies} copy '{printer}' par bhej di gayi{note}"

    doc = Doc(work, ext)
    try:
        select_pages(opts["pages"], doc.count)  # galat range ho to print shuru hone se pehle hi bata do
        if TEST_MODE:
            return _test_print(doc, opts, copies, os.path.basename(path))
        sheets = _win_draw(printer, title, lambda: compose_sheets(doc, opts), copies, opts["fit"])
    finally:
        doc.close()
        if work != path:
            shutil.rmtree(os.path.dirname(work), ignore_errors=True)
    return f"{sheets} kaagaz × {copies} copy '{printer}' par bhej di gayi"


def _test_print(doc, opts, copies, name):
    os.makedirs(OUT_DIR, exist_ok=True)
    base = os.path.splitext(name)[0]
    n = 0
    for n, (img, _) in enumerate(compose_sheets(doc, opts, dpi=60), 1):
        img.save(os.path.join(OUT_DIR, f"{base}_sheet{n}.png"))
    return f"TEST: {n} kaagaz × {copies} copy save hui"


def _win_draw(printer, title, make_sheets, copies, fit):
    """Har sheet ko printer ke kaagaz par chhaapo. Kitne kaagaz chhape, wo lautao."""
    import win32con
    import win32ui
    from PIL import ImageWin

    hdc = win32ui.CreateDC()
    try:
        hdc.CreatePrinterDC(printer)
    except Exception as e:
        raise PrintError(f"Printer '{printer}' se baat nahi ho paayi") from e
    count = 0
    try:
        pw = hdc.GetDeviceCaps(win32con.HORZRES)
        ph = hdc.GetDeviceCaps(win32con.VERTRES)
        dpx = hdc.GetDeviceCaps(win32con.LOGPIXELSX)
        dpy = hdc.GetDeviceCaps(win32con.LOGPIXELSY)
        for _ in range(copies):
            hdc.StartDoc(title)
            count = 0
            try:
                for img, inches in make_sheets():
                    rotated = False
                    if (img.width > img.height) != (pw > ph):  # aada page seedhe kaagaz par -> ghumao
                        img = img.rotate(90, expand=True)
                        rotated = True
                    if fit == "actual" and inches:
                        iw, ih = (inches[1], inches[0]) if rotated else inches
                        w, h = int(iw * dpx), int(ih * dpy)
                        if w > pw or h > ph:  # kaagaz se bada ho to fit kar do
                            s = min(pw / w, ph / h)
                            w, h = int(w * s), int(h * s)
                    else:
                        s = min(pw / img.width, ph / img.height)
                        w, h = int(img.width * s), int(img.height * s)
                    x, y = (pw - w) // 2, (ph - h) // 2
                    hdc.StartPage()
                    ImageWin.Dib(img).draw(hdc.GetHandleOutput(), (x, y, x + w, y + h))
                    hdc.EndPage()
                    count += 1
            except Exception:
                try:
                    hdc.AbortDoc()
                except Exception:
                    pass
                raise
            hdc.EndDoc()
    finally:
        hdc.DeleteDC()
    return count


def _win_shell_print(path, printer, copies):
    import win32api
    for _ in range(copies):
        try:
            win32api.ShellExecute(0, "printto", path, f'"{printer}"', ".", 0)
        except Exception as e:
            raise PrintError(
                "Is file ko kholne wali app laptop par nahi mili "
                "(Word/Excel files ke liye MS Office ya LibreOffice chahiye)"
            ) from e
        time.sleep(2)


def _cups_print(path, printer, copies, title, opts):
    cmd = ["lp", "-d", printer, "-n", str(copies), "-t", title]
    if opts["pages"] not in ("all", "odd", "even"):
        cmd += ["-o", f"page-ranges={opts['pages'].replace(' ', '')}"]
    elif opts["pages"] in ("odd", "even"):
        cmd += ["-o", f"page-set={opts['pages']}"]
    if opts["color"] == "bw":
        cmd += ["-o", "ColorModel=Gray"]
    if opts["nup"] > 1:
        cmd += ["-o", f"number-up={opts['nup']}"]
    if opts["reverse"]:
        cmd += ["-o", "outputorder=reverse"]
    if opts["fit"] == "fit":
        cmd += ["-o", "fit-to-page"]
    r = subprocess.run(cmd + [path], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise PrintError(r.stderr.strip() or "lp command fail hua")
    return f"{copies} copy '{printer}' par bhej di gayi"
