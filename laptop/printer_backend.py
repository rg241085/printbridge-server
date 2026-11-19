"""
PrintBridge - printing ka hissa (v1.1)

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
  paper     : "default" | "a4" | "letter" | "legal" | "a3" | "a5"
  duplex    : "off" | "long" | "short"  (dono taraf; sirf jin printers mein suvidha ho)
  password  : password wali PDF ka password (kahin save nahi hota)

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
TEST_PRINTER = "Test Printer (saves to folder)"

DEFAULT_OPTS = {"pages": "all", "color": "color", "nup": 1, "fit": "fit", "reverse": False,
                "paper": "default", "duplex": "off", "password": ""}
PAPERS = {"a4": 9, "letter": 1, "legal": 5, "a3": 8, "a5": 11}  # Windows DMPAPER_* numbers


class Cancelled(Exception):
    pass


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
    o["paper"] = opts.get("paper") if opts.get("paper") in PAPERS else "default"
    o["duplex"] = opts.get("duplex") if opts.get("duplex") in ("long", "short") else "off"
    pw = opts.get("password")
    o["password"] = pw[:200] if isinstance(pw, str) else ""
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
            raise PrintError("This file has only one page, so there are no even pages")
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
            raise PrintError(f"Invalid page number: '{part}' (use a format like 1-3, 5)")
        if a < 1 or b < a:
            raise PrintError(f"Invalid page range: '{part}'")
        if a > n:
            raise PrintError(f"The file has only {n} pages, so page {a} does not exist")
        out.extend(range(a - 1, min(b, n)))
    if not out:
        raise PrintError("No pages were selected")
    seen = set()
    return [i for i in out if not (i in seen or seen.add(i))]


# ---------------------------------------------------------------- documents
class Doc:
    """Kisi bhi file ke pages: count aur render(i) -> PIL image."""

    def __init__(self, path, ext, password=""):
        self.ext = ext
        self.pdf = None
        self.img = None
        if ext in IMAGE_EXT:
            from PIL import Image, ImageOps
            try:
                self.img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
            except Exception as e:
                raise PrintError("Could not open the image") from e
            self.count = 1
        else:
            import pypdfium2 as pdfium
            try:
                self.pdf = pdfium.PdfDocument(path, password=password or None)
            except Exception as e:
                if "password" in str(e).lower():
                    raise PrintError("This PDF is password-protected. Choose the file in the phone app and enter the correct password."
                                     if not password else "Incorrect PDF password") from e
                raise PrintError("Could not open the PDF (the file may be damaged)") from e
            self.count = len(self.pdf)
            if self.count == 0:
                raise PrintError("The PDF has no pages")
            if self.count > MAX_PAGES:
                raise PrintError(f"The file has {self.count} pages (maximum {MAX_PAGES})")

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
        return [TEST_PRINTER], TEST_PRINTER, {TEST_PRINTER: "ready"}, {TEST_PRINTER: {"duplex": True, "papers": list(PAPERS)}}
    if IS_WIN:
        import win32print
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        names = [p[2] for p in win32print.EnumPrinters(flags)]
        try:
            default = win32print.GetDefaultPrinter()
        except Exception:
            default = names[0] if names else None
        status, caps = {}, {}
        for n in names:
            status[n], caps[n] = _win_printer_info(n)
        return names, default, status, caps
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
    return names, default, {n: "ready" for n in names}, {n: {"duplex": False, "papers": []} for n in names}


def _win_printer_info(name):
    """Printer offline hai ya nahi, aur kya-kya kar sakta hai (dono taraf, kaagaz ke size)."""
    import win32print
    status, caps = "ready", {"duplex": False, "papers": []}
    try:
        h = win32print.OpenPrinter(name)
        try:
            info = win32print.GetPrinter(h, 2)
        finally:
            win32print.ClosePrinter(h)
        if (info.get("Attributes", 0) & 0x400) or (info.get("Status", 0) & 0x80):  # WORK_OFFLINE / OFFLINE
            status = "offline"
        port = info.get("pPortName") or ""
        try:
            caps["duplex"] = win32print.DeviceCapabilities(name, port, 7) == 1  # DC_DUPLEX
        except Exception:
            pass
        try:
            ids = set(win32print.DeviceCapabilities(name, port, 2) or [])  # DC_PAPERS
            caps["papers"] = [k for k, v in PAPERS.items() if v in ids]
        except Exception:
            pass
    except Exception:
        pass
    return status, caps


# ---------------------------------------------------------------- printing
def count_sheets(path, opts=None, password=None):
    """Kitne kaagaz lagenge (1 copy ke). Word/Excel jaisi file ka pata na chale to None."""
    ext = os.path.splitext(path)[1].lower()
    opts = clean_opts(opts)
    if password is not None:
        opts["password"] = password
    work, tmp = path, None
    if ext in OFFICE_EXT and IS_WIN and not TEST_MODE:
        tmp = office_to_pdf(path, ext)
        if tmp:
            work, ext = tmp, ".pdf"
    if ext not in IMAGE_EXT | PDF_EXT:
        return None
    try:
        doc = Doc(work, ext, opts["password"])
        try:
            n = len(select_pages(opts["pages"], doc.count))
        finally:
            doc.close()
        return -(-n // opts["nup"])  # upar ki taraf round
    finally:
        if tmp:
            shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)


def print_file(path, printer, copies=1, title="PrintBridge", opts=None, cancel=None, spool=None, stats=None):
    """cancel: threading.Event (set ho to print rok do). spool: list, isme Windows print job IDs aate hain.
    stats: dict, isme {"sheets": kaagaz (1 copy), "copies", "color": "color"/"bw"} bhar diya jaata hai."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in ALLOWED_EXT:
        raise PrintError(f"'{ext}' files cannot be printed")
    copies = max(1, min(int(copies), 50))
    opts = clean_opts(opts)
    if stats is not None:
        stats.update(sheets=None, copies=copies, color=opts["color"])
    if not TEST_MODE and not printer:
        raise PrintError("No printer was found on the computer")

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
            note = " (print settings could not be applied to this file)"
    if ext in TEXT_EXT or ext in OFFICE_EXT:
        if TEST_MODE:
            os.makedirs(OUT_DIR, exist_ok=True)
            shutil.copy(path, os.path.join(OUT_DIR, os.path.basename(path)))
            return f"TEST: file saved{note}"
        _win_shell_print(path, printer, copies)
        return f"Sent {copies} {'copy' if copies == 1 else 'copies'} to '{printer}'{note}"

    doc = Doc(work, ext, opts["password"])
    try:
        select_pages(opts["pages"], doc.count)  # galat range ho to print shuru hone se pehle hi bata do
        if stats is not None:
            stats["sheets"] = -(-len(select_pages(opts["pages"], doc.count)) // opts["nup"])
        if TEST_MODE:
            return _test_print(doc, opts, copies, os.path.basename(path), cancel) + \
                (f" [paper={opts['paper']}, duplex={opts['duplex']}]" if opts["paper"] != "default" or opts["duplex"] != "off" else "")
        sheets, note2 = _win_draw(printer, title, lambda: compose_sheets(doc, opts), copies, opts, cancel, spool)
        note += note2
    finally:
        doc.close()
        if work != path:
            shutil.rmtree(os.path.dirname(work), ignore_errors=True)
    return f"Sent {sheets} {'sheet' if sheets == 1 else 'sheets'} × {copies} {'copy' if copies == 1 else 'copies'} to '{printer}'{note}"


def _test_print(doc, opts, copies, name, cancel=None):
    os.makedirs(OUT_DIR, exist_ok=True)
    base = os.path.splitext(name)[0]
    n = 0
    delay = float(os.environ.get("PRINTBRIDGE_TEST_DELAY", "0"))
    for n, (img, _) in enumerate(compose_sheets(doc, opts, dpi=60), 1):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        time.sleep(delay)
        img.save(os.path.join(OUT_DIR, f"{base}_sheet{n}.png"))
    return f"TEST: saved {n} {'sheet' if n == 1 else 'sheets'} × {copies} {'copy' if copies == 1 else 'copies'}"


def _make_dc(printer, opts):
    """Printer ka DC banao. Kaagaz ka size / dono taraf chahiye to printer ki DEVMODE badal kar."""
    import win32con
    import win32gui
    import win32print
    import win32ui
    if opts["paper"] != "default" or opts["duplex"] != "off":
        try:
            h = win32print.OpenPrinter(printer)
            try:
                dm = win32print.GetPrinter(h, 2)["pDevMode"]
                if dm is None:
                    raise RuntimeError("no devmode")
                if opts["paper"] != "default":
                    dm.PaperSize = PAPERS[opts["paper"]]
                    dm.Fields |= win32con.DM_PAPERSIZE
                if opts["duplex"] != "off":
                    dm.Duplex = 2 if opts["duplex"] == "long" else 3  # DMDUP_VERTICAL / DMDUP_HORIZONTAL
                    dm.Fields |= win32con.DM_DUPLEX
                win32print.DocumentProperties(0, h, printer, dm, dm,
                                              win32con.DM_IN_BUFFER | win32con.DM_OUT_BUFFER)
            finally:
                win32print.ClosePrinter(h)
            return win32ui.CreateDCFromHandle(win32gui.CreateDC("WINSPOOL", printer, dm)), ""
        except Exception:
            note = " (the printer did not accept the paper size or two-sided setting)"
    else:
        note = ""
    hdc = win32ui.CreateDC()
    try:
        hdc.CreatePrinterDC(printer)
    except Exception as e:
        raise PrintError(f"Could not communicate with printer '{printer}'") from e
    return hdc, note


def cancel_spool(printer, job_ids):
    """Windows print queue mein pahunch chuke jobs ko hatao."""
    if not IS_WIN or not job_ids:
        return
    import win32print
    try:
        h = win32print.OpenPrinter(printer)
        try:
            for jid in job_ids:
                try:
                    win32print.SetJob(h, jid, 0, None, win32print.JOB_CONTROL_DELETE)
                except Exception:
                    pass
        finally:
            win32print.ClosePrinter(h)
    except Exception:
        pass


def _win_draw(printer, title, make_sheets, copies, opts, cancel=None, spool=None):
    """Har sheet ko printer ke kaagaz par chhaapo. (kitne kaagaz, note) lautao."""
    import win32con
    from PIL import ImageWin

    hdc, note = _make_dc(printer, opts)
    fit = opts["fit"]
    count = 0
    try:
        pw = hdc.GetDeviceCaps(win32con.HORZRES)
        ph = hdc.GetDeviceCaps(win32con.VERTRES)
        dpx = hdc.GetDeviceCaps(win32con.LOGPIXELSX)
        dpy = hdc.GetDeviceCaps(win32con.LOGPIXELSY)
        # Poore kaagaz ka naap aur chhapne layak hisse ki shuruaat (printer kinaare par nahi chhaap pata)
        fw = hdc.GetDeviceCaps(110) or pw   # PHYSICALWIDTH
        fh = hdc.GetDeviceCaps(111) or ph   # PHYSICALHEIGHT
        offx = hdc.GetDeviceCaps(112)       # PHYSICALOFFSETX
        offy = hdc.GetDeviceCaps(113)       # PHYSICALOFFSETY
        for _ in range(copies):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            jid = hdc.StartDoc(title)
            if spool is not None and isinstance(jid, int) and jid > 0:
                spool.append(jid)
            count = 0
            try:
                for img, inches in make_sheets():
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    rotated = False
                    if (img.width > img.height) != (pw > ph):  # aada page seedhe kaagaz par -> ghumao
                        img = img.rotate(90, expand=True)
                        rotated = True
                    if fit == "actual" and inches:
                        # Asli size: bilkul naap ke hisaab se, poore kaagaz ke beech mein (ID card 85.6 mm hi chhape)
                        iw, ih = (inches[1], inches[0]) if rotated else inches
                        w, h = int(iw * dpx), int(ih * dpy)
                        if w > fw * 1.02 or h > fh * 1.02:  # kaagaz se kaafi bada ho tabhi chhota karo
                            s = min(fw / w, fh / h)
                            w, h = int(w * s), int(h * s)
                        x, y = (fw - w) // 2 - offx, (fh - h) // 2 - offy
                    else:
                        s = min(pw / img.width, ph / img.height)
                        w, h = int(img.width * s), int(img.height * s)
                        x, y = (pw - w) // 2, (ph - h) // 2
                    hdc.StartPage()
                    ImageWin.Dib(img).draw(hdc.GetHandleOutput(), (x, y, x + w, y + h))
                    hdc.EndPage()
                    count += 1
            except BaseException:
                try:
                    hdc.AbortDoc()
                except Exception:
                    pass
                raise
            hdc.EndDoc()
    finally:
        hdc.DeleteDC()
    return count, note


def _win_shell_print(path, printer, copies):
    import win32api
    for _ in range(copies):
        try:
            win32api.ShellExecute(0, "printto", path, f'"{printer}"', ".", 0)
        except Exception as e:
            raise PrintError(
                "No application was found on this computer to open this file "
                "(Word/Excel files require Microsoft Office or LibreOffice)"
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
        raise PrintError(r.stderr.strip() or "The lp command failed")
    return f"Sent {copies} {'copy' if copies == 1 else 'copies'} to '{printer}'"
