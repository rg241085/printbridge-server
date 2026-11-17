"""
PrintBridge - laptop wala program (v1.3)

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
import local_server as ls
import shop as sh
import usage as us

VERSION = "1.3"
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
    cfg.setdefault("ask_approval", True)
    cfg.setdefault("local_key", secrets.token_urlsafe(12))  # bina internet print ki chaabi (QR mein)
    cfg.setdefault("shop", {})
    for k, v in {"enabled": False, "name": "", "bw_rate": 2, "color_rate": 10, "auto_print": False,
                 "message": "", "allow_private": True}.items():
        cfg["shop"].setdefault(k, v)
    cfg.setdefault("rules", {})  # phone (hash) -> {"no_color": True/False, "daily_limit": kaagaz}  # naya phone judne se pehle laptop par "Allow?" poochho
    if not cfg.get("server_url"):
        txt = os.path.join(APP_DIR, "server.txt")
        cfg["server_url"] = (open(txt, encoding="utf-8").read().strip() if os.path.exists(txt) else "") or DEFAULT_SERVER
    cfg["device_id"] = id_from_secret(cfg["agent_secret"])
    for old in ("key", "port"):  # v0.1 ki purani settings
        cfg.pop(old, None)
    return cfg


CFG = load_config()
us.init(DATA_DIR)
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
    # Phone ne apna naam badla ho to laptop par bhi naya naam
    h = hash_token(p.get("token", ""))
    phone = str(p.get("phone", ""))[:40]
    p["uid"] = h[:12]
    rule = CFG["rules"].get(h, {})
    if rule.get("no_color") and p["opts"]["color"] == "color":
        p["opts"]["color"] = "bw"
        p["note"] = " (is phone ke liye color band hai, isliye Black & White chhapa)"
    p["daily_limit"] = int(rule.get("daily_limit") or 0)
    if phone and h in CFG["phones"] and CFG["phones"][h].get("name") != phone:
        with CFG_LOCK:
            CFG["phones"][h]["name"] = phone
            save_config()
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
        limit = p.get("daily_limit") or 0
        if limit:  # Office mode: roz ki page limit
            need = (pb.count_sheets(path, p.get("opts")) or 0) * copies
            done = us.today_pages(p.get("uid", ""))
            if done + need > limit:
                raise pb.PrintError(f"Aaj ki limit {limit} kaagaz hai. Aap {done} chhaap chuke hain, "
                                    f"is file ke {need} aur nahi chhap sakte.")
        status("queued" if PRINT_LOCK.locked() else "printing",
               "Line mein hai (pehle wala print chal raha hai)" if PRINT_LOCK.locked() else "Print ho raha hai")
        with PRINT_LOCK:
            if job["cancel"].is_set():
                raise pb.Cancelled()
            status("printing", "Print ho raha hai")
            stats = {}
            msg = pb.print_file(path, p["printer"], copies, title=f"PrintBridge - {name}", opts=p.get("opts"),
                                cancel=job["cancel"], spool=job["spool"], stats=stats)
        if job["cancel"].is_set():
            raise pb.Cancelled()
        us.log("phone", p.get("uid", ""), phone, name, stats.get("sheets"), copies, stats.get("color", ""), p["printer"])
        status("done", msg + p.get("note", ""))
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



def local_print(path, printer, copies, opts, phone, filename):
    """Bina internet (same WiFi) se aaya print."""
    names, default, _, _ = pb.list_printers()
    if printer not in names:
        printer = CFG.get("printer") if CFG.get("printer") in names else default
    copies = max(1, min(int(copies), 50))
    emit(f"Aaya (bina internet): {phone} se '{filename}' ({copies} copy)")
    try:
        stats = {}
        with PRINT_LOCK:
            msg = pb.print_file(path, printer, copies, title=f"PrintBridge - {filename}", opts=pb.clean_opts(opts),
                                stats=stats)
        us.log("local", "local:" + phone, phone, filename, stats.get("sheets"), copies, stats.get("color", ""), printer)
        emit(f"Print hua: '{filename}' -> {printer}")
        return msg
    except Exception as e:
        emit(f"ERROR: '{filename}' print nahi hui: {e}")
        raise


# ---------------------------------------------------------------- Print shop mode
SHOP_DIRTY = threading.Event()   # window ko taaza karna hai


def shop_changed(kind, order):
    SHOP_DIRTY.set()
    if kind == "new":
        emit(f"SHOP: naya order #{order['token']} - {order['customer']}")
        UI_EVENTS.put(("shop_new", order["token"], order["customer"]))
    elif kind == "auto_print":
        threading.Thread(target=shop_print, args=(order["id"],), daemon=True).start()


SHOP = sh.ShopQueue(DATA_DIR, lambda: CFG["shop"], on_change=shop_changed)


def shop_name():
    return CFG["shop"].get("name") or CFG["name"]


def shop_link():
    b = server_base()
    return f"{b}/s/{CFG['device_id'].replace('PB-', '').replace('-', '')}" if b else ""


def shop_print(order_id, printer=None):
    names, default, _, _ = pb.list_printers()
    printer = printer if printer in names else (CFG.get("printer") if CFG.get("printer") in names else default)
    o = SHOP.get(order_id)
    if not o:
        return False, "Order nahi mila"
    emit(f"SHOP: order #{o['token']} ({o['customer']}) print ho raha hai")

    def one(path, prn, copies, opts, stats, f, order):
        with PRINT_LOCK:
            msg = pb.print_file(path, prn, copies, title=f"Shop #{order['token']} - {f['name']}", opts=opts, stats=stats)
        sheets = stats.get("sheets")
        amount = round(sheets * copies * SHOP.rate(stats.get("color")), 2) if sheets is not None else ""
        us.log("shop", "shop:" + order["customer"], f"#{order['token']} {order['customer']}", f["name"], sheets,
               copies, stats.get("color", ""), prn, "done", amount)
        return msg

    ok, err = SHOP.print_order(order_id, printer, one)
    emit(f"SHOP: order #{o['token']} " + ("taiyaar" if ok else f"ERROR {err}"))
    return ok, err


def shop_done(order_id):
    """Customer ne print le liya. Rupaye badle gaye the to report mein farq jodo."""
    o = SHOP.get(order_id)
    if not o:
        return
    if o.get("amount_override") is not None and o["status"] in ("ready", "waiting"):
        diff = round(o["amount_override"] - o.get("calc_total", 0), 2)
        if diff:
            us.log("shop", "shop:" + o["customer"], f"#{o['token']} {o['customer']}", "(rupaye badle)", None, 1, "",
                   "", "done", diff)
    SHOP.set_status(order_id, "done", "Print le liya gaya")


def valid_order_id(x):
    return isinstance(x, str) and 16 <= len(x) <= 40 and x.replace("-", "").replace("_", "").isalnum()


def rpc_shop_info(p):
    s = CFG["shop"]
    if not s.get("enabled"):
        return {"ok": False, "error": "Ye shop abhi online order nahi le rahi. Counter par baat kijiye."}
    caps = {"duplex": False, "papers": []}
    try:
        names, default, _, allcaps = pb.list_printers()
        prn = CFG.get("printer") if CFG.get("printer") in names else default
        caps = allcaps.get(prn, caps)
    except Exception:
        pass
    return {"ok": True, "name": shop_name(), "bw_rate": s["bw_rate"], "color_rate": s["color_rate"],
            "message": s.get("message", ""), "allow_private": s.get("allow_private", True), "caps": caps}


def rpc_shop_job(p):
    if not CFG["shop"].get("enabled"):
        return {"ok": False, "error": "Ye shop abhi online order nahi le rahi"}
    if not valid_order_id(p.get("order_id")):
        return {"ok": False, "error": "Galat order"}
    name = str(p.get("filename", "file"))
    if os.path.splitext(name)[1].lower() not in pb.ALLOWED_EXT:
        return {"ok": False, "error": f"'{name}' print nahi ho sakti"}
    private = bool(p.get("private"))
    if private and not CFG["shop"].get("allow_private", True):
        return {"ok": False, "error": "Ye shop private (seedha print) order nahi leti"}
    try:
        o, f = SHOP.start_file(p["order_id"], str(p.get("customer", "")).strip(), str(p.get("mobile", "")).strip(),
                               name, p.get("copies", 1), p.get("opts"), str(p.get("note", "")), private)
    except pb.PrintError as e:
        return {"ok": False, "error": str(e)}

    def fetch():
        try:
            req = urllib.request.Request(f"{server_base()}/api/agent/jobs/{p['job_id']}/file",
                                         headers={"X-DL-Key": p["dl_key"], "User-Agent": f"PrintBridge/{VERSION}"})
            with urllib.request.urlopen(req, timeout=180) as r, open(f["path"], "wb") as out:
                shutil.copyfileobj(r, out)
            CLOUD.send({"type": "job_status", "job_id": p["job_id"], "status": "done", "message": "Shop ko mil gaya"})
            SHOP.file_ready(o["id"], f["fid"], True)
        except Exception as e:
            CLOUD.send({"type": "job_status", "job_id": p["job_id"], "status": "error", "message": str(e)[:200]})
            SHOP.file_ready(o["id"], f["fid"], False, str(e))
    threading.Thread(target=fetch, daemon=True).start()
    return {"ok": True, "token": o["token"]}


def rpc_shop_status(p):
    st = SHOP.status(p.get("order_id")) if valid_order_id(p.get("order_id")) else None
    if not st:
        return {"ok": False, "error": "Order nahi mila (shayad purana ho gaya)"}
    st.update(ok=True, shop=shop_name())
    return st


def rpc_shop_cancel(p):
    if not valid_order_id(p.get("order_id")):
        return {"ok": False, "error": "Galat order"}
    ok, err = SHOP.cancel(p["order_id"])
    return {"ok": ok, "error": err}


LOCAL = ls.LocalServer({
    "key": lambda: CFG["local_key"],
    "name": lambda: CFG["name"],
    "printers": pb.list_printers,
    "print_file": local_print,
    "jobs_dir": JOBS_DIR,
})


RPC = {"pair": rpc_pair, "printers": rpc_printers, "job": rpc_job, "cancel": rpc_cancel, "scan": rpc_scan,
       "shop_info": rpc_shop_info, "shop_job": rpc_shop_job, "shop_status": rpc_shop_status,
       "shop_cancel": rpc_shop_cancel}



def cleanup_loop():
    """Print ho chuki files 10 minute baad delete (privacy ke liye)."""
    while True:
        try:
            SHOP.cleanup()
        except Exception:
            log.exception("shop cleanup")
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
    import gui_extra as gx
    import tkinter as tk
    M = sys.modules[__name__]
    from tkinter import messagebox, simpledialog, ttk
    from PIL import Image, ImageTk

    root = tk.Tk()
    root.title("PrintBridge")
    sh = root.winfo_screenheight()
    compact = sh < 940            # chhoti screen (jaise 1366x768): QR aur history chhote
    QR = 140 if compact else 200
    root.geometry(f"460x{min(880, sh - 70)}+20+10")
    root.minsize(400, 600)
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
    log_box = tk.Listbox(root, height=3 if compact else 6, font=(F, 9), bd=1, relief="solid")
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
        img = ImageTk.PhotoImage(make_qr_image(url, QR) if url else Image.new("RGB", (QR, QR), "#eeeeee"))
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
                CFG["local_key"] = secrets.token_urlsafe(12)
                save_config()
            change_pin()
            refresh()
            emit("Saare phones hata diye, naya PIN bana")

    bf = tk.Frame(root, bg="white")
    bf.pack(pady=(8, 2))
    ttk.Button(bf, text="Naya PIN", command=new_pin_click).pack(side="left", padx=3)
    ttk.Button(bf, text="Phones", command=lambda: gx.open_phones(root, M, refresh)).pack(side="left", padx=3)
    ttk.Button(bf, text="Refresh", command=lambda: (refresh(), load_printers())).pack(side="left", padx=3)
    ttk.Button(bf, text="Server", command=ask_server).pack(side="left", padx=3)
    ttk.Button(bf, text="Update", command=lambda: threading.Thread(
        target=update_now, args=(True,), daemon=True).start()).pack(side="left", padx=3)

    def offline_qr():
        win = tk.Toplevel(root)
        win.title("Bina internet print")
        win.configure(bg="white")
        win.resizable(False, False)
        tk.Label(win, text="Bina internet print", font=(F, 15, "bold"), fg="#0f766e", bg="white").pack(pady=(14, 2))
        if LOCAL.httpd is None:
            tk.Label(win, text=f"Ye suvidha abhi chalu nahi ho paayi.\n{LOCAL.error or ''}", fg="#dc2626",
                     bg="white", font=(F, 10)).pack(padx=20, pady=10)
            return
        tk.Label(win, text="Internet band ho tab bhi print ho sakta hai.\n"
                           "1. Phone aur laptop EK HI WiFi par hon\n"
                           "2. Phone ke camera se ye QR scan kijiye",
                 font=(F, 10), bg="white", justify="left").pack(padx=20)
        img = ImageTk.PhotoImage(make_qr_image(LOCAL.url(), 230))
        q = tk.Label(win, image=img, bg="white")
        q.image = img
        q.pack(pady=8)
        tk.Label(win, text=LOCAL.url().split("#")[0], font=(F, 9), fg="#666", bg="white").pack()
        tk.Label(win, text="Phone na khule to: Windows Firewall mein PrintBridge ko\n"
                           "'Private network' par Allow kijiye.",
                 font=(F, 8), fg="#888", bg="white").pack(pady=(6, 14))

    bf2 = tk.Frame(root, bg="white")
    bf2.pack(pady=(2, 0))
    ttk.Button(bf2, text="Print shop", command=lambda: gx.open_shop(root, M)).pack(side="left", padx=3)
    ttk.Button(bf2, text="Report", command=lambda: gx.open_report(root, M)).pack(side="left", padx=3)
    ttk.Button(bf2, text="Bina internet QR", command=offline_qr).pack(side="left", padx=3)
    ttk.Button(bf2, text="Sab phones hatao", command=remove_phones).pack(side="left", padx=3)

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
            if isinstance(ev, tuple) and ev[0] == "shop_new":
                root.bell()
                try:
                    if tray["icon"] is not None:
                        tray["icon"].notify(f"Naya order #{ev[1]}: {ev[2]}", "PrintBridge - Print shop")
                except Exception:
                    pass
                continue
            if isinstance(ev, tuple) and ev[0] == "error":
                messagebox.showerror("PrintBridge", ev[1], parent=root)
                continue
            if ev == "show":
                show_window()
            elif ev == "quit":
                quit_app()
                return
        root.after(400, poll)

    # ---- system tray (ghadi ke paas wala icon): window band karne par bhi program chalta rahe
    tray = {"icon": None, "ready": False}

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

            def setup(ic):
                ic.visible = True
                tray["ready"] = True  # icon sach mein ban gaya, ab window chhupana surakshit hai

            def run():
                try:
                    icon.run(setup=setup)
                except Exception:
                    log.exception("tray icon")
                tray["ready"] = False
            threading.Thread(target=run, daemon=True).start()
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
        if tray["icon"] is None or not tray["ready"]:
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
        root.after(1500, lambda: hide_window(notify=False))  # tray icon banne ka thoda samay
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
    if LOCAL.httpd is not None:
        print(f"Bina internet: {LOCAL.url()}")
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
    if not LOCAL.start():
        log.info("local server: %s", LOCAL.error)
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
