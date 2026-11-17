"""
PrintBridge - laptop wala program (v0.8)

Naya kya hai:
  - Laptop internet par PrintBridge server se juda rehta hai, isliye phone kahin se bhi print bhej sakta hai.
  - Phone Laptop ID + 6 ank ke PIN se judta hai (ya QR scan karke).
  - Windows ke saath apne aap start ho sakta hai.

Chalane ka tareeka:  python printbridge.py                    (window ke saath)
                     python printbridge.py --nogui --server URL (sirf console, testing ke liye)
"""
import asyncio
import hashlib
import json
import logging
import os
import queue
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime

import printer_backend as pb
import scanner as sc

VERSION = "0.8"
DEFAULT_SERVER = "https://printbridge.onrender.com"
FROZEN = getattr(sys, "frozen", False)  # PrintBridge.exe ke roop me chal raha hai?
APP_DIR = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
HEARTBEAT_SEC = 240  # har 4 minute me server ko "main hoon" bolo (free server sone na paaye)
GITHUB_REPO = "rg241085/printbridge-server"  # yahin se naya version (PrintBridge.exe) aata hai
UPDATE_API = os.environ.get("PRINTBRIDGE_UPDATE_API", f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest")
UPDATE_EVERY_SEC = 6 * 3600


# ---------------------------------------------------------------- config
def data_dir():
    base = os.environ.get("PRINTBRIDGE_HOME")
    if not base:
        if sys.platform == "win32":
            base = os.path.join(os.environ.get("APPDATA", APP_DIR), "PrintBridge")
        else:
            base = os.path.join(os.path.expanduser("~"), ".printbridge")
    os.makedirs(base, exist_ok=True)
    return base


DATA_DIR = data_dir()
JOBS_DIR = os.path.join(DATA_DIR, "jobs")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
os.makedirs(JOBS_DIR, exist_ok=True)

logging.basicConfig(filename=os.path.join(DATA_DIR, "printbridge.log"), level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("printbridge")


def id_from_secret(secret):
    """Laptop ID secret se banti hai. Server bhi yahi hisaab lagata hai, isliye ID chura nahi sakte."""
    h = hashlib.sha256(("printbridge:" + secret).encode()).digest()
    c = "".join(ID_ALPHABET[b % 32] for b in h[:8])
    return f"PB-{c[:4]}-{c[4:]}"


def new_pin():
    return f"{secrets.randbelow(10 ** 6):06d}"


def hash_token(t):
    return hashlib.sha256(t.encode()).hexdigest()


def save_config():
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(CFG, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def load_config():
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    cfg.setdefault("agent_secret", secrets.token_urlsafe(32))
    cfg.setdefault("name", socket.gethostname())
    cfg.setdefault("printer", None)
    cfg.setdefault("pin", new_pin())
    cfg.setdefault("phones", {})
    cfg.setdefault("ask_approval", True)  # naya phone judne se pehle laptop par "Allow?" poochho
    if not cfg.get("server_url"):
        txt = os.path.join(APP_DIR, "server.txt")
        cfg["server_url"] = (open(txt, encoding="utf-8").read().strip() if os.path.exists(txt) else "") or DEFAULT_SERVER
    cfg["device_id"] = id_from_secret(cfg["agent_secret"])
    for old in ("key", "port"):  # v0.1 ki purani settings
        cfg.pop(old, None)
    return cfg


CFG = load_config()
CFG_LOCK = threading.Lock()
EVENTS = queue.Queue()
PRINT_LOCK = threading.Lock()
PIN_FAILS = []
UI_EVENTS = queue.Queue()  # "show" / "quit": tray icon ya doosri baar exe kholne se
APPROVALS = queue.Queue()  # (phone ka naam, Event, result) -> window "Allow?" poochti hai
GUI_MODE = "--nogui" not in sys.argv


def emit(msg):
    log.info(msg)
    EVENTS.put(f"{datetime.now():%H:%M:%S}  {msg}")


def server_base():
    return CFG.get("server_url", "").strip().rstrip("/")


def pairing_url():
    b = server_base()
    return f"{b}/#id={CFG['device_id']}&pin={CFG['pin']}" if b else ""


def check_token(token):
    return bool(token) and hash_token(token) in CFG["phones"]


# ---------------------------------------------------------------- phone ki requests (RPC)
def rpc_pair(p):
    now = time.time()
    PIN_FAILS[:] = [t for t in PIN_FAILS if now - t < 300]
    if len(PIN_FAILS) >= 5:
        return {"ok": False, "error": "Bahut baar galat PIN. 5 minute baad try kijiye."}
    if not secrets.compare_digest(str(p.get("pin", "")), CFG["pin"]):
        PIN_FAILS.append(now)
        emit("Kisi ne galat PIN daala")
        return {"ok": False, "error": "PIN galat hai"}
    phone = str(p.get("phone", "Phone"))[:40]
    if CFG.get("ask_approval", True) and not ask_approval(phone):
        emit(f"Phone ko mana kiya: {phone}")
        return {"ok": False, "denied": True, "error": "Laptop par mana kar diya gaya (ya kisi ne Allow nahi dabaya)"}
    token = secrets.token_urlsafe(24)
    with CFG_LOCK:
        CFG["phones"][hash_token(token)] = {"name": phone, "added": datetime.now().isoformat(timespec="seconds")}
        save_config()
    emit(f"Naya phone juda: {phone}")
    return {"ok": True, "token": token}


def ask_approval(phone):
    """Laptop ki window me "Allow?" popup. 60 sec me jawab nahi to mana."""
    if not GUI_MODE:
        return True  # console/test mode
    ev, box = threading.Event(), {"ok": False, "expired": False}
    APPROVALS.put((phone, ev, box))
    if not ev.wait(60):
        box["expired"] = True
        return False
    return box["ok"]


def change_pin():
    with CFG_LOCK:
        CFG["pin"] = new_pin()
        save_config()
    CLOUD.send({"type": "set_pin", "pin": CFG["pin"]})
    CLOUD.pin_changed = True


def rpc_printers(p):
    if not check_token(p.get("token")):
        return {"ok": False, "auth": False, "error": "Ye phone laptop se juda nahi hai"}
    names, default, status, caps = pb.list_printers()
    chosen = CFG.get("printer") if CFG.get("printer") in names else default
    return {"ok": True, "printers": names, "default": chosen, "status": status, "caps": caps,
            "scanners": sc.list_scanners()}


def rpc_job(p):
    if not check_token(p.get("token")):
        return {"ok": False, "auth": False, "error": "Ye phone laptop se juda nahi hai"}
    name = str(p.get("filename", "file"))
    ext = os.path.splitext(name)[1].lower()
    if ext not in pb.ALLOWED_EXT:
        return {"ok": False, "error": f"'{ext}' file print nahi ho sakti"}
    names, default, _, _ = pb.list_printers()
    printer = p.get("printer") or CFG.get("printer") or default
    if printer not in names:
        return {"ok": False, "error": "Ye printer laptop par nahi mila"}
    p["printer"] = printer
    p["opts"] = pb.clean_opts(p.get("opts"))
    threading.Thread(target=job_worker, args=(p,), daemon=True).start()
    return {"ok": True}




ACTIVE = {}  # job_id -> {"cancel": Event, "spool": [Windows job ids], "printer": naam}


def job_worker(p):
    jid, name = p["job_id"], p["filename"]
    copies = max(1, min(int(p.get("copies", 1)), 50))
    phone = p.get("phone", "Phone")
    emit(f"Aaya: {phone} se '{name}' ({copies} copy)")
    ext = os.path.splitext(name)[1].lower()
    path = os.path.join(JOBS_DIR, f"{jid[:8]}{ext}")
    job = ACTIVE[jid] = {"cancel": threading.Event(), "spool": [], "printer": p["printer"]}

    def status(st, msg):
        CLOUD.send({"type": "job_status", "job_id": jid, "status": st, "message": msg})

    try:
        status("downloading", "Laptop file le raha hai")
        req = urllib.request.Request(f"{server_base()}/api/agent/jobs/{jid}/file",
                                     headers={"X-DL-Key": p["dl_key"], "User-Agent": f"PrintBridge/{VERSION}"})
        with urllib.request.urlopen(req, timeout=180) as r, open(path, "wb") as f:
            shutil.copyfileobj(r, f)
        if job["cancel"].is_set():
            raise pb.Cancelled()
        status("queued" if PRINT_LOCK.locked() else "printing",
               "Line mein hai (pehle wala print chal raha hai)" if PRINT_LOCK.locked() else "Print ho raha hai")
        with PRINT_LOCK:
            if job["cancel"].is_set():
                raise pb.Cancelled()
            status("printing", "Print ho raha hai")
            msg = pb.print_file(path, p["printer"], copies, title=f"PrintBridge - {name}", opts=p.get("opts"),
                                cancel=job["cancel"], spool=job["spool"])
        if job["cancel"].is_set():
            raise pb.Cancelled()
        status("done", msg)
        emit(f"Print hua: '{name}' -> {p['printer']}")
    except pb.Cancelled:
        pb.cancel_spool(job["printer"], job["spool"])
        status("cancelled", "Print cancel ho gaya")
        emit(f"Cancel hua: '{name}'")
    except Exception as e:
        log.exception("job fail")
        status("error", str(e)[:250])
        emit(f"ERROR: '{name}' print nahi hui: {e}")
    finally:
        ACTIVE.pop(jid, None)


def rpc_cancel(p):
    if not check_token(p.get("token")):
        return {"ok": False, "auth": False, "error": "Ye phone laptop se juda nahi hai"}
    job = ACTIVE.get(p.get("job_id"))
    if not job:
        return {"ok": True, "message": "Print pehle hi khatam ho chuka tha"}
    job["cancel"].set()
    pb.cancel_spool(job["printer"], job["spool"])  # jo Windows queue mein pahunch chuka, use bhi hatao
    return {"ok": True}


def rpc_scan(p):
    if not check_token(p.get("token")):
        return {"ok": False, "auth": False, "error": "Ye phone laptop se juda nahi hai"}
    if not sc.list_scanners():
        return {"ok": False, "error": "Laptop se koi scanner juda nahi mila"}
    threading.Thread(target=scan_worker, args=(p,), daemon=True).start()
    return {"ok": True}


SCAN_LOCK = threading.Lock()


def scan_worker(p):
    sid = p["scan_id"]
    path = os.path.join(JOBS_DIR, f"scan_{sid[:8]}.jpg")

    def status(st, msg):
        CLOUD.send({"type": "scan_status", "scan_id": sid, "status": st, "message": msg})

    try:
        emit(f"Scan: {p.get('phone', 'Phone')} ne scan maanga")
        with SCAN_LOCK:
            status("scanning", "Scan ho raha hai...")
            sc.scan(path, color=p.get("color", "color"), dpi=int(p.get("dpi", 200)))
        with open(path, "rb") as f:
            data = f.read()
        req = urllib.request.Request(f"{server_base()}/api/agent/scans/{sid}", data=data, method="POST",
                                     headers={"X-Up-Key": p["up_key"], "Content-Type": "image/jpeg",
                                              "User-Agent": f"PrintBridge/{VERSION}"})
        with urllib.request.urlopen(req, timeout=180) as r:
            r.read()
        emit("Scan phone ko bhej diya")
    except Exception as e:
        log.exception("scan fail")
        status("error", str(e)[:250])
        emit(f"ERROR scan: {e}")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


RPC = {"pair": rpc_pair, "printers": rpc_printers, "job": rpc_job, "cancel": rpc_cancel, "scan": rpc_scan}


def cleanup_loop():
    """Print ho chuki files 10 minute baad delete (privacy ke liye)."""
    while True:
        cutoff = time.time() - 600
        for n in os.listdir(JOBS_DIR):
            fp = os.path.join(JOBS_DIR, n)
            try:
                if os.path.getmtime(fp) < cutoff:
                    os.remove(fp)
            except OSError:
                pass
        time.sleep(60)


# ---------------------------------------------------------------- auto-update
UPDATE_STATE = {"busy": False, "latest": None}


def parse_version(text):
    import re
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    return tuple(int(x or 0) for x in m.groups()) if m else None


def check_for_update():
    """GitHub par naya release hai? (version, exe ka link) ya None."""
    req = urllib.request.Request(UPDATE_API, headers={"User-Agent": f"PrintBridge/{VERSION}",
                                                      "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rel = json.load(r)
    latest = parse_version(rel.get("name") or rel.get("tag_name"))
    asset = next((a for a in rel.get("assets", []) if a.get("name", "").lower() == "printbridge.exe"), None)
    UPDATE_STATE["latest"] = latest
    if latest and asset and latest > parse_version(VERSION):
        return ".".join(map(str, latest)), asset["browser_download_url"]
    return None


def install_update(ver, url):
    """Nayi exe download karo, phir chhoti .bat file purani exe ko badal kar naya program chalu karti hai."""
    exe = sys.executable
    new = exe + ".new"
    emit(f"Naya version v{ver} mila. Download ho raha hai...")
    req = urllib.request.Request(url, headers={"User-Agent": f"PrintBridge/{VERSION}"})
    with urllib.request.urlopen(req, timeout=300) as r, open(new, "wb") as f:
        shutil.copyfileobj(r, f)
    with open(new, "rb") as f:
        ok = f.read(2) == b"MZ"
    if not ok or os.path.getsize(new) < 1_000_000:
        os.remove(new)
        raise RuntimeError("download adhoora tha")
    bat = os.path.join(DATA_DIR, "update.bat")
    with open(bat, "w", encoding="mbcs") as f:
        f.write(
            "@echo off\r\n"
            "set n=0\r\n"
            ":retry\r\n"
            "timeout /t 2 /nobreak >nul\r\n"
            f'move /y "{new}" "{exe}" >nul 2>&1\r\n'
            "if not errorlevel 1 goto run\r\n"
            "set /a n+=1\r\n"
            "if %n% lss 30 goto retry\r\n"
            ":run\r\n"
            f'start "" "{exe}" --minimized\r\n'
            '(goto) 2>nul & del "%~f0"\r\n')
    emit(f"v{ver} install ho raha hai, PrintBridge kuch second me dobara chalu hoga...")
    log.info("update to %s", ver)
    with PRINT_LOCK:  # koi print chal raha ho to uske khatam hone ka intezaar
        subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000 | 0x00000008,  # NO_WINDOW | DETACHED
                         close_fds=True)
        time.sleep(0.5)
        os._exit(0)


def update_now(manual=False):
    """manual=True: 'Update' button se. Sirf exe (Windows) par khud update hota hai."""
    if UPDATE_STATE["busy"]:
        return
    UPDATE_STATE["busy"] = True
    try:
        found = check_for_update()
        if not found:
            if manual:
                emit(f"Aap sabse naye version (v{VERSION}) par hain")
            return
        ver, url = found
        if not (FROZEN and sys.platform == "win32"):
            emit(f"Naya version v{ver} hai (auto-update sirf PrintBridge.exe me hota hai)")
            return
        install_update(ver, url)
    except Exception as e:
        log.info("update check fail: %r", e)
        if manual:
            emit(f"Update check nahi ho paaya: {e}")
    finally:
        UPDATE_STATE["busy"] = False


def update_loop():
    time.sleep(45)  # pehle program aaram se chalu ho jaaye
    while True:
        update_now()
        time.sleep(UPDATE_EVERY_SEC)


# ---------------------------------------------------------------- server connection
class Cloud:
    def __init__(self):
        self.loop = None
        self.ws = None
        self.send_lock = None
        self.status = ("off", "Shuru ho raha hai...")
        self.pin_changed = False

    def set_status(self, kind, text):
        if self.status != (kind, text):
            self.status = (kind, text)
            log.info("status %s: %s", kind, text)

    def start(self):
        threading.Thread(target=lambda: asyncio.run(self.main()), daemon=True).start()

    def send(self, msg):
        """Kisi bhi thread se server ko message bhejo."""
        if self.loop and self.ws:
            asyncio.run_coroutine_threadsafe(self._send(self.ws, msg), self.loop)

    async def _send(self, ws, msg):
        async with self.send_lock:
            await ws.send(json.dumps(msg))

    def reconnect(self):
        if self.loop and self.ws:
            asyncio.run_coroutine_threadsafe(self.ws.close(), self.loop)

    async def main(self):
        from websockets.asyncio.client import connect
        self.loop = asyncio.get_running_loop()
        self.send_lock = asyncio.Lock()
        backoff = 2
        while True:
            base = server_base()
            if not base:
                self.set_status("off", "Server URL set nahi hai")
                await asyncio.sleep(2)
                continue
            ws_url = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/ws/agent"
            self.set_status("wait", "Server se jud raha hai...")
            try:
                async with connect(ws_url, open_timeout=90, ping_interval=20, ping_timeout=30,
                                   max_size=2 ** 20) as ws:
                    self.ws = ws
                    await self._send(ws, {"type": "hello", "secret": CFG["agent_secret"], "name": CFG["name"],
                                          "version": VERSION, "pin": CFG["pin"]})
                    hb = asyncio.create_task(self._heartbeat(ws))
                    try:
                        async for raw in ws:
                            msg = json.loads(raw)
                            t = msg.get("type")
                            if t == "welcome":
                                backoff = 2
                                self.set_status("on", "Online: phone se print bhej sakte hain")
                                emit("Server se jud gaye")
                            elif t == "rpc":
                                asyncio.create_task(self._rpc(ws, msg))
                    finally:
                        hb.cancel()
                        self.ws = None
                if ws.close_code == 4002:
                    self.set_status("off", "Ye laptop ID kisi aur jagah chal rahi hai")
            except Exception as e:
                log.info("connect fail: %r", e)
                self.ws = None
            if self.status[0] == "on":
                emit("Server se connection toota, dobara jod rahe hain...")
            self.set_status("wait", f"Server se connection nahi. {backoff} sec me dobara koshish...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _heartbeat(self, ws):
        while True:
            await asyncio.sleep(HEARTBEAT_SEC)
            await self._send(ws, {"type": "hb"})

    async def _rpc(self, ws, msg):
        fn = RPC.get(msg.get("method"))
        try:
            if not fn:
                result = {"ok": False, "error": "unknown"}
            else:
                result = await asyncio.get_running_loop().run_in_executor(None, fn, msg.get("params") or {})
        except Exception as e:
            log.exception("rpc")
            result = {"ok": False, "error": str(e)[:250]}
        try:
            await self._send(ws, {"type": "rpc_result", "rid": msg.get("rid"), "result": result})
        except Exception:
            pass


CLOUD = Cloud()


# ---------------------------------------------------------------- Windows ke saath start
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command():
    if FROZEN:
        return f'"{sys.executable}" --minimized'
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[:-10] + "pythonw.exe"
    return f'"{exe}" "{os.path.abspath(__file__)}" --minimized'


def autostart_enabled():
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "PrintBridge")
            return True
    except OSError:
        return False


def set_autostart(on):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if on:
            winreg.SetValueEx(k, "PrintBridge", 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, "PrintBridge")
            except OSError:
                pass
    # v0.2 wali purani Startup folder .bat file hata do
    old = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs",
                       "Startup", "PrintBridge.bat")
    if os.path.exists(old):
        try:
            os.remove(old)
        except OSError:
            pass


def first_run_setup():
    """exe pehli baar chale to Windows ke saath start apne aap ON kar do."""
    if sys.platform == "win32" and FROZEN and not CFG.get("autostart_done"):
        try:
            set_autostart(True)
        except Exception:
            log.exception("autostart")
        with CFG_LOCK:
            CFG["autostart_done"] = True
            save_config()
    elif sys.platform == "win32" and autostart_enabled():
        try:
            set_autostart(True)  # exe ki jagah badli ho to path update
        except Exception:
            pass


# ---------------------------------------------------------------- window
def make_qr_image(data, size=200):
    import qrcode
    qr = qrcode.QRCode(border=2, box_size=10)
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").get_image().resize((size, size))


def run_gui():
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk
    from PIL import Image, ImageTk

    root = tk.Tk()
    root.title("PrintBridge")
    root.geometry("440x820")
    root.minsize(400, 700)
    root.configure(bg="white")
    icon_path = os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "icon.png")
    try:
        if os.path.exists(icon_path):
            root.iconphoto(True, ImageTk.PhotoImage(Image.open(icon_path)))
    except Exception:
        pass
    F = "Segoe UI"
    BLUE = "#1a56db"

    tk.Label(root, text="PrintBridge", font=(F, 20, "bold"), fg=BLUE, bg="white").pack(pady=(14, 0))
    tk.Label(root, text=f"v{VERSION}", font=(F, 8), fg="#999", bg="white").pack()
    status_lbl = tk.Label(root, font=(F, 10, "bold"), bg="white")
    status_lbl.pack(pady=(2, 0))

    tk.Label(root, text="Phone app mein QR scan kijiye, ya Laptop ID + PIN daaliye",
             font=(F, 10), fg="#444", bg="white").pack(pady=(10, 0))
    box = tk.Frame(root, bg="#eef2ff", padx=14, pady=10)
    box.pack(fill="x", padx=18, pady=(6, 0))
    box.columnconfigure(0, weight=1)
    box.columnconfigure(1, weight=1)
    tk.Label(box, text="Laptop ID", font=(F, 9), fg="#555", bg="#eef2ff").grid(row=0, column=0)
    tk.Label(box, text="PIN", font=(F, 9), fg="#555", bg="#eef2ff").grid(row=0, column=1)
    id_lbl = tk.Label(box, font=("Consolas", 20, "bold"), fg=BLUE, bg="#eef2ff")
    id_lbl.grid(row=1, column=0)
    pin_lbl = tk.Label(box, font=("Consolas", 20, "bold"), fg="#111", bg="#eef2ff")
    pin_lbl.grid(row=1, column=1)
    site_lbl = tk.Label(box, font=(F, 9), fg="#555", bg="#eef2ff")
    site_lbl.grid(row=2, column=0, columnspan=2, pady=(4, 0))

    tk.Label(root, text="QR code", font=(F, 9), fg="#666", bg="white").pack(pady=(10, 2))
    qr_lbl = tk.Label(root, bg="white")
    qr_lbl.pack()

    pf = tk.Frame(root, bg="white")
    pf.pack(fill="x", padx=18, pady=(10, 0))
    tk.Label(pf, text="Printer:", font=(F, 10), bg="white").pack(side="left")
    printer_var = tk.StringVar()
    printer_box = ttk.Combobox(pf, textvariable=printer_var, state="readonly", width=34)
    printer_box.pack(side="left", padx=6)

    phones_lbl = tk.Label(root, font=(F, 9), fg="#555", bg="white", anchor="w", justify="left", wraplength=400)
    phones_lbl.pack(fill="x", padx=18, pady=(6, 0))

    tk.Label(root, text="Print history", font=(F, 10, "bold"), bg="white").pack(anchor="w", padx=18, pady=(6, 0))
    log_box = tk.Listbox(root, height=6, font=(F, 9), bd=1, relief="solid")
    log_box.pack(fill="both", expand=True, padx=18)

    def load_printers():
        try:
            names, default, _, _ = pb.list_printers()
        except Exception as e:
            names, default = [], None
            emit(f"ERROR printer list: {e}")
        printer_box["values"] = names
        cur = CFG.get("printer") if CFG.get("printer") in names else default
        printer_var.set(cur or "(koi printer nahi mila)")

    def on_printer(_e=None):
        with CFG_LOCK:
            CFG["printer"] = printer_var.get()
            save_config()
        emit(f"Default printer: {CFG['printer']}")

    printer_box.bind("<<ComboboxSelected>>", on_printer)

    def refresh():
        id_lbl.configure(text=CFG["device_id"])
        pin_lbl.configure(text=f"{CFG['pin'][:3]} {CFG['pin'][3:]}")
        host = server_base().split("://", 1)[-1]
        site_lbl.configure(text=f"Phone app: {host}")
        url = pairing_url()
        img = ImageTk.PhotoImage(make_qr_image(url) if url else Image.new("RGB", (200, 200), "#eeeeee"))
        qr_lbl.configure(image=img)
        qr_lbl.image = img
        n = len(CFG["phones"])
        names = ", ".join(v["name"] for v in list(CFG["phones"].values())[-3:])
        phones_lbl.configure(text=f"Jude hue phones: {n}" + (f"  ({names})" if n else ""))

    def ask_server():
        url = simpledialog.askstring("Server address", "PrintBridge server ka address:",
                                     initialvalue=server_base(), parent=root)
        if not url:
            return
        url = url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        with CFG_LOCK:
            CFG["server_url"] = url
            save_config()
        emit(f"Server: {url}")
        CLOUD.reconnect()
        refresh()

    def new_pin_click():
        change_pin()
        refresh()
        emit("Naya PIN bana (jude hue phones chalte rahenge)")

    def remove_phones():
        if messagebox.askyesno("Phones hataiye", "Saare jude hue phones hat jaayenge aur unhe dobara jodna padega.\n"
                                                "PIN bhi badal jaayega. Aage badhein?"):
            with CFG_LOCK:
                CFG["phones"] = {}
                save_config()
            change_pin()
            refresh()
            emit("Saare phones hata diye, naya PIN bana")

    bf = tk.Frame(root, bg="white")
    bf.pack(pady=(8, 2))
    ttk.Button(bf, text="Naya PIN", command=new_pin_click).pack(side="left", padx=3)
    ttk.Button(bf, text="Phones hatao", command=remove_phones).pack(side="left", padx=3)
    ttk.Button(bf, text="Refresh", command=lambda: (refresh(), load_printers())).pack(side="left", padx=3)
    ttk.Button(bf, text="Server", command=ask_server).pack(side="left", padx=3)
    ttk.Button(bf, text="Update", command=lambda: threading.Thread(
        target=update_now, args=(True,), daemon=True).start()).pack(side="left", padx=3)

    opts = tk.Frame(root, bg="white")
    opts.pack(pady=(4, 10))
    approve_var = tk.BooleanVar(value=CFG.get("ask_approval", True))

    def on_approve():
        with CFG_LOCK:
            CFG["ask_approval"] = approve_var.get()
            save_config()
    ttk.Checkbutton(opts, text="Naya phone judne se pehle mujhse poochho (Allow)", variable=approve_var,
                    command=on_approve).pack(anchor="w")

    if sys.platform == "win32":
        auto_var = tk.BooleanVar(value=autostart_enabled())

        def on_auto():
            try:
                set_autostart(auto_var.get())
                emit("Windows ke saath start: " + ("ON" if auto_var.get() else "OFF"))
            except Exception as e:
                messagebox.showerror("PrintBridge", str(e))
        ttk.Checkbutton(opts, text="Windows ke saath apne aap start ho", variable=auto_var,
                        command=on_auto).pack(anchor="w")

    colors = {"on": "#059669", "wait": "#d97706", "off": "#dc2626"}
    asking = {"busy": False}

    def handle_approval():
        phone, ev, box_ = APPROVALS.get()
        if box_["expired"]:
            return
        asking["busy"] = True
        was_hidden = root.state() in ("withdrawn", "iconic")
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
        root.bell()
        ok = messagebox.askyesno("Naya phone", f"'{phone}' is laptop ke printer se judna chahta hai.\n\n"
                                               "Kya ye aapka (ya aapke jaan-pehchaan ka) phone hai?\n"
                                               "Allow karein?", parent=root)
        root.attributes("-topmost", False)
        box_["ok"] = ok and not box_["expired"]
        ev.set()
        asking["busy"] = False
        if was_hidden:
            hide_window(notify=False)

    def poll():
        while not EVENTS.empty():
            log_box.insert(0, EVENTS.get())
        kind, text = CLOUD.status
        status_lbl.configure(text="● " + text, fg=colors[kind])
        if CLOUD.pin_changed:
            CLOUD.pin_changed = False
            refresh()
        elif len(CFG["phones"]) != getattr(poll, "nphones", -1):
            poll.nphones = len(CFG["phones"])
            refresh()
        if not asking["busy"] and not APPROVALS.empty():
            root.after(10, handle_approval)
        while not UI_EVENTS.empty():
            ev = UI_EVENTS.get()
            if ev == "show":
                show_window()
            elif ev == "quit":
                quit_app()
                return
        root.after(400, poll)

    # ---- system tray (ghadi ke paas wala icon): window band karne par bhi program chalta rahe
    tray = {"icon": None}

    def start_tray():
        try:
            if sys.platform == "win32":
                import pystray._win32  # noqa: F401  (exe mein Windows wala hissa zaroor shaamil ho)
            import pystray
            img = Image.open(icon_path) if os.path.exists(icon_path) else Image.new("RGB", (64, 64), BLUE)
            menu = pystray.Menu(
                pystray.MenuItem("PrintBridge kholiye", lambda: UI_EVENTS.put("show"), default=True),
                pystray.MenuItem("PrintBridge band karein", lambda: UI_EVENTS.put("quit")))
            icon = pystray.Icon("PrintBridge", img, "PrintBridge: mobile se print (chal raha hai)", menu)
            threading.Thread(target=icon.run, daemon=True).start()
            tray["icon"] = icon
        except Exception:
            log.exception("tray icon")

    def show_window():
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
        root.after(300, lambda: root.attributes("-topmost", False))
        root.focus_force()

    def hide_window(notify=True):
        if tray["icon"] is None:
            root.iconify()  # tray nahi bana to bas chhota kar do
            return
        root.withdraw()
        if notify and not CFG.get("tray_told"):
            try:
                tray["icon"].notify("PrintBridge peeche chal raha hai. Phone se print aate rahenge.\n"
                                    "Window kholne ke liye ghadi ke paas PrintBridge icon par double-click kijiye.",
                                    "PrintBridge")
            except Exception:
                pass
            with CFG_LOCK:
                CFG["tray_told"] = True
                save_config()

    def quit_app():
        show_window()
        if messagebox.askyesno("PrintBridge band karein?",
                               "PrintBridge poori tarah band ho jaayega aur phone se print NAHI aayenge.\n\n"
                               "Band karein?", parent=root):
            if tray["icon"] is not None:
                try:
                    tray["icon"].stop()
                except Exception:
                    pass
            root.destroy()
        else:
            root.after(400, poll)

    root.protocol("WM_DELETE_WINDOW", hide_window)  # ✕ = chhupao, band nahi
    start_tray()
    refresh()
    load_printers()
    poll()
    if "--minimized" in sys.argv:
        root.after(50, lambda: hide_window(notify=False))
    root.mainloop()


def run_console():
    import qrcode
    print(f"\nPrintBridge v{VERSION}")
    print(f"PIN       : {CFG['pin']}")
    print(f"Laptop ID : {CFG['device_id']}")
    print(f"Server    : {server_base() or '(set nahi)'}")
    if pairing_url():
        print(f"QR link   : {pairing_url()}\n")
        qr = qrcode.QRCode(border=1)
        qr.add_data(pairing_url())
        qr.print_ascii(invert=True)
    print("\nBand karne ke liye Ctrl+C.\n", flush=True)
    last = None
    try:
        while True:
            try:
                print(EVENTS.get(timeout=1), flush=True)
            except queue.Empty:
                pass
            if CLOUD.pin_changed:
                CLOUD.pin_changed = False
                print(f"[PIN badla] Naya PIN: {CFG['pin']}", flush=True)
            if CLOUD.status != last:
                last = CLOUD.status
                print(f"[status] {last[1]}", flush=True)
    except KeyboardInterrupt:
        pass


LOCK_PORT = 47651


def single_instance():
    """Ek laptop par PrintBridge ek hi baar chale. Doosri baar khole to pehle wale ki window dikhao."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        return None
    s.listen(4)

    def accept_loop():
        while True:
            try:
                c, _ = s.accept()
                with c:
                    c.settimeout(2)
                    if c.recv(16).startswith(b"show"):
                        UI_EVENTS.put("show")
            except Exception:
                time.sleep(0.5)
    threading.Thread(target=accept_loop, daemon=True).start()
    return s


def show_running_instance():
    try:
        with socket.create_connection(("127.0.0.1", LOCK_PORT), timeout=2) as c:
            c.sendall(b"show")
        return True
    except OSError:
        return False


def main():
    if "--server" in sys.argv:
        CFG["server_url"] = sys.argv[sys.argv.index("--server") + 1]
        save_config()
    save_config()
    lock = single_instance()
    if not lock:
        if "--nogui" not in sys.argv and show_running_instance():
            return  # pehle se chal raha hai: uski window saamne aa gayi
        msg = "PrintBridge pehle se chal raha hai."
        if "--nogui" in sys.argv:
            print(msg)
        else:
            import tkinter as tk
            from tkinter import messagebox
            tk.Tk().withdraw()
            messagebox.showinfo("PrintBridge", msg)
        return
    log.info("start v%s id=%s", VERSION, CFG["device_id"])
    first_run_setup()
    CLOUD.start()
    threading.Thread(target=cleanup_loop, daemon=True).start()
    threading.Thread(target=update_loop, daemon=True).start()
    if "--nogui" in sys.argv:
        run_console()
    else:
        run_gui()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception("crash")
        try:
            import tkinter as tk
            from tkinter import messagebox
            tk.Tk().withdraw()
            messagebox.showerror("PrintBridge error", f"{e}\n\nLog: {os.path.join(DATA_DIR, 'printbridge.log')}")
        except Exception:
            raise
