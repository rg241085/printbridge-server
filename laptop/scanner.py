"""
PrintBridge - laptop se jude scanner se scan (Windows WIA)

  list_scanners()                  -> scanners ke naam
  scan(out_path, color, dpi)       -> JPEG file banata hai

Har Windows scanner/all-in-one printer jiska driver install hai (WIA) is tareeke se chalta hai.
Test mode (PRINTBRIDGE_TEST=1): ek nakli "Test Scanner" jo ek namoona page banata hai.
"""
import os
import sys
import time

IS_WIN = sys.platform == "win32"
TEST_MODE = os.environ.get("PRINTBRIDGE_TEST") == "1"
TEST_SCANNER = "Test Scanner"

WIA_SCANNER = 1
WIA_INTENT = 6146   # 1 = color, 2 = grayscale, 4 = black & white text
WIA_XRES, WIA_YRES = 6147, 6148
WIA_XEXT, WIA_YEXT = 6151, 6152

_cache = {"t": 0, "names": []}


class ScanError(Exception):
    pass


def _com():
    import pythoncom
    pythoncom.CoInitialize()
    return pythoncom


def list_scanners(max_age=120):
    """Scanners ki list (2 minute tak yaad rakhte hain, kyunki dhoondhne mein samay lagta hai)."""
    if TEST_MODE:
        return [TEST_SCANNER]
    if not IS_WIN:
        return []
    if time.time() - _cache["t"] < max_age:
        return _cache["names"]
    names = []
    try:
        pc = _com()
        try:
            import win32com.client
            dm = win32com.client.Dispatch("WIA.DeviceManager")
            for i in range(1, dm.DeviceInfos.Count + 1):
                info = dm.DeviceInfos.Item(i)
                if info.Type == WIA_SCANNER:
                    names.append(_prop(info.Properties, "Name") or f"Scanner {i}")
        finally:
            pc.CoUninitialize()
    except Exception:
        names = []
    _cache.update(t=time.time(), names=names)
    return names


def _prop(props, name):
    try:
        for i in range(1, props.Count + 1):
            p = props.Item(i)
            if p.Name == name:
                return p.Value
    except Exception:
        pass
    return None


def _set(item, pid, value=None, use_max=False):
    for i in range(1, item.Properties.Count + 1):
        p = item.Properties.Item(i)
        if p.PropertyID == pid:
            try:
                p.Value = p.SubTypeMax if use_max else value
            except Exception:
                pass
            return


def scan(out_path, color="color", dpi=200, scanner_name=None):
    """Ek page scan karke out_path par JPEG save karo."""
    dpi = 150 if dpi < 200 else (300 if dpi >= 300 else 200)
    if TEST_MODE:
        return _fake_scan(out_path, color, dpi)
    if not IS_WIN:
        raise ScanError("Scanning is only supported on Windows")
    pc = _com()
    try:
        import win32com.client
        dm = win32com.client.Dispatch("WIA.DeviceManager")
        info = None
        for i in range(1, dm.DeviceInfos.Count + 1):
            d = dm.DeviceInfos.Item(i)
            if d.Type == WIA_SCANNER and (not scanner_name or _prop(d.Properties, "Name") == scanner_name):
                info = d
                break
        if info is None:
            raise ScanError("No scanner is connected to the computer")
        try:
            dev = info.Connect()
        except Exception as e:
            raise ScanError("Could not communicate with the scanner (is it switched off or in use by another program?)") from e
        item = dev.Items.Item(1)
        _set(item, WIA_INTENT, {"color": 1, "gray": 2, "bw": 4}.get(color, 1))
        _set(item, WIA_XRES, dpi)
        _set(item, WIA_YRES, dpi)
        _set(item, WIA_XEXT, use_max=True)  # poora scanner glass
        _set(item, WIA_YEXT, use_max=True)
        try:
            img = item.Transfer()  # zyadatar scanners BMP dete hain
        except Exception as e:
            raise ScanError("The scan failed. Place the document on the scanner and try again.") from e
        tmp = out_path + ".raw"
        if os.path.exists(tmp):
            os.remove(tmp)
        img.SaveFile(tmp)
    finally:
        pc.CoUninitialize()
    from PIL import Image
    try:
        im = Image.open(tmp).convert("L" if color in ("gray", "bw") else "RGB")
        im.save(out_path, "JPEG", quality=88)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return out_path


def _fake_scan(out_path, color, dpi):
    from PIL import Image, ImageDraw
    w, h = int(8.27 * dpi / 2), int(11.69 * dpi / 2)
    im = Image.new("RGB", (w, h), (250, 250, 245))
    d = ImageDraw.Draw(im)
    d.rectangle([w * .08, h * .06, w * .92, h * .12], fill=(30, 80, 200))
    for k in range(18):
        y = h * .18 + k * h * .04
        d.rectangle([w * .08, y, w * (0.92 - (k % 3) * .12), y + h * .012], fill=(60, 60, 60))
    if color != "color":
        im = im.convert("L")
    time.sleep(float(os.environ.get("PRINTBRIDGE_TEST_DELAY", "0")))
    im.save(out_path, "JPEG", quality=85)
    return out_path
