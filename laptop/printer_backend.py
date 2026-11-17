"""
PrintBridge - printing ka hissa.

Windows par:
  - Photo (jpg/png/...)  -> page par fit karke seedha printer par
  - PDF                  -> program ke andar hi har page ko image banakar printer par
                            (koi PDF reader install karne ki zaroorat nahi)
  - Word/Excel/TXT/...   -> us file ki default app se ("printto"), MS Office/LibreOffice chahiye
Linux/Mac par: CUPS ka `lp` command.
Test mode (PRINTBRIDGE_TEST=1): printer ki jagah "test_prints" folder me PNG pages save hote hain.
"""
import os
import shutil
import subprocess
import sys
import time

IS_WIN = sys.platform == "win32"
TEST_MODE = os.environ.get("PRINTBRIDGE_TEST") == "1"
OUT_DIR = os.path.join(os.getcwd(), "test_prints")

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
PDF_EXT = {".pdf"}
OFFICE_EXT = {".txt", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf", ".odt"}
ALLOWED_EXT = IMAGE_EXT | PDF_EXT | OFFICE_EXT

PDF_DPI = 300
MAX_PDF_PAGES = 300
TEST_PRINTER = "Test Printer (sirf folder me save)"


class PrintError(Exception):
    pass


# ---------------------------------------------------------------- printers
def list_printers():
    """(printers ki list, default printer) lautata hai."""
    if TEST_MODE:
        return [TEST_PRINTER], TEST_PRINTER
    if IS_WIN:
        import win32print
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        names = [p[2] for p in win32print.EnumPrinters(flags)]
        try:
            default = win32print.GetDefaultPrinter()
        except Exception:
            default = names[0] if names else None
        return names, default
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
    return names, default


# ---------------------------------------------------------------- pages (images)
def image_pages(path):
    from PIL import Image, ImageOps
    img = Image.open(path)
    yield ImageOps.exif_transpose(img).convert("RGB")


def pdf_pages(path, dpi=PDF_DPI):
    import pypdfium2 as pdfium
    try:
        pdf = pdfium.PdfDocument(path)
    except Exception as e:
        raise PrintError("PDF khul nahi paayi (shayad kharab ya password wali hai)") from e
    try:
        n = len(pdf)
        if n == 0:
            raise PrintError("PDF mein koi page nahi hai")
        if n > MAX_PDF_PAGES:
            raise PrintError(f"PDF mein {n} page hain (max {MAX_PDF_PAGES})")
        for i in range(n):
            page = pdf[i]
            w, h = page.get_size()  # points (1/72 inch)
            scale = dpi / 72
            if w * h * scale * scale > 40_000_000:  # bahut bade page ko chhota karke render
                scale = (40_000_000 / (w * h)) ** 0.5
            bmp = page.render(scale=scale)
            yield bmp.to_pil().convert("RGB")
            page.close()
    finally:
        pdf.close()


# ---------------------------------------------------------------- printing
def print_file(path, printer, copies=1, title="PrintBridge"):
    ext = os.path.splitext(path)[1].lower()
    if ext not in ALLOWED_EXT:
        raise PrintError(f"'{ext}' file print nahi ho sakti")
    copies = max(1, min(int(copies), 50))

    if TEST_MODE:
        return _test_print(path, ext, copies)
    if not printer:
        raise PrintError("Laptop par koi printer nahi mila")

    if IS_WIN:
        if ext in IMAGE_EXT:
            pages = _win_draw_pages(printer, title, lambda: image_pages(path), copies)
        elif ext in PDF_EXT:
            pages = _win_draw_pages(printer, title, lambda: pdf_pages(path), copies)
        else:
            _win_shell_print(path, printer, copies)
            return f"{copies} copy '{printer}' par bhej di gayi"
        return f"{pages} page × {copies} copy '{printer}' par bhej di gayi"

    r = subprocess.run(["lp", "-d", printer, "-n", str(copies), "-t", title, path],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise PrintError(r.stderr.strip() or "lp command fail hua")
    return f"{copies} copy '{printer}' par bhej di gayi"


def _test_print(path, ext, copies):
    os.makedirs(OUT_DIR, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    if ext in IMAGE_EXT | PDF_EXT:
        gen = image_pages(path) if ext in IMAGE_EXT else pdf_pages(path, dpi=60)
        n = 0
        for n, img in enumerate(gen, 1):
            img.save(os.path.join(OUT_DIR, f"{base}_page{n}.png"))
        return f"TEST: {n} page × {copies} copy save hui"
    shutil.copy(path, os.path.join(OUT_DIR, os.path.basename(path)))
    return f"TEST: {copies} copy save hui"


def _win_draw_pages(printer, title, make_pages, copies):
    """Har page (PIL image) ko printer ke page par fit karke chhaapo. Kitne page chhape, wo lautao."""
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
        for _ in range(copies):
            hdc.StartDoc(title)
            count = 0
            try:
                for img in make_pages():
                    # Landscape page ko portrait kaagaz par ghuma do taaki poora bada chhape
                    if (img.width > img.height) != (pw > ph):
                        img = img.rotate(90, expand=True)
                    scale = min(pw / img.width, ph / img.height)
                    w, h = int(img.width * scale), int(img.height * scale)
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
