"""
PrintBridge - laptop wala program (v1.4)

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

VERSION = "2.1"
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
                 "message": "", "allow_private": True,
                 # v2.1: shop owner ki aur settings
                 "auto_mode": "all" if cfg["shop"].get("auto_print") else "off",   # off / all / bw (sirf B&W apne aap)
                 "bw_printer": "", "color_printer": "",          # khaali = default printer
                 "hours_on": False, "open_at": "09:00", "close_at": "21:00", "days": [0, 1, 2, 3, 4, 5, 6],
                 "min_charge": 0, "bulk_after": 0, "bulk_pct": 0,
                 "require_mobile": False, "sound": True, "max_files": 20}.items():
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
        return {"ok": False, "error": "Too many incorrect PIN attempts. Please try again in 5 minutes."}
    if not secrets.compare_digest(str(p.get("pin", "")), CFG["pin"]):
        PIN_FAILS.append(now)
        emit("Incorrect PIN entered")
        return {"ok": False, "error": "Incorrect PIN"}
    phone = str(p.get("phone", "Phone"))[:40]
    if CFG.get("ask_approval", True) and not ask_approval(phone):
        emit(f"Connection declined: {phone}")
        return {"ok": False, "denied": True, "error": "The request was declined on the computer (or nobody clicked Allow)"}
    token = secrets.token_urlsafe(24)
    with CFG_LOCK:
        CFG["phones"][hash_token(token)] = {"name": phone, "added": datetime.now().isoformat(timespec="seconds")}
        save_config()
    emit(f"New device connected: {phone}")
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
        return {"ok": False, "auth": False, "error": "This device is not connected to the computer"}
    names, default, status, caps = pb.list_printers()
    chosen = CFG.get("printer") if CFG.get("printer") in names else default
    return {"ok": True, "printers": names, "default": chosen, "status": status, "caps": caps,
            "scanners": sc.list_scanners()}


def rpc_job(p):
    if not check_token(p.get("token")):
        return {"ok": False, "auth": False, "error": "This device is not connected to the computer"}
    name = str(p.get("filename", "file"))
    ext = os.path.splitext(name)[1].lower()
    if ext not in pb.ALLOWED_EXT:
        return {"ok": False, "error": f"'{ext}' files cannot be printed"}
    names, default, _, _ = pb.list_printers()
    printer = p.get("printer") or CFG.get("printer") or default
    if printer not in names:
        return {"ok": False, "error": "This printer was not found on the computer"}
    p["printer"] = printer
    p["opts"] = pb.clean_opts(p.get("opts"))
    # Phone ne apna naam badla ho to laptop par bhi naya naam
    h = hash_token(p.get("token", ""))
    phone = str(p.get("phone", ""))[:40]
    p["uid"] = h[:12]
    rule = CFG["rules"].get(h, {})
    if rule.get("no_color") and p["opts"]["color"] == "color":
        p["opts"]["color"] = "bw"
        p["note"] = " (color printing is disabled for this device, so it was printed in black & white)"
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
    emit(f"Received from {phone}: '{name}' ({copies} {'copy' if copies == 1 else 'copies'})")
    ext = os.path.splitext(name)[1].lower()
    path = os.path.join(JOBS_DIR, f"{jid[:8]}{ext}")
    job = ACTIVE[jid] = {"cancel": threading.Event(), "spool": [], "printer": p["printer"]}

    def status(st, msg):
        CLOUD.send({"type": "job_status", "job_id": jid, "status": st, "message": msg})

    try:
        status("downloading", "The computer is receiving the file")
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
                raise pb.PrintError(f"Daily limit is {limit} sheets. {done} sheets have already been printed today, "
                                    f"so the {need} sheets for this file cannot be printed.")
        status("queued" if PRINT_LOCK.locked() else "printing",
               "Queued (another job is printing)" if PRINT_LOCK.locked() else "Printing")
        with PRINT_LOCK:
            if job["cancel"].is_set():
                raise pb.Cancelled()
            status("printing", "Printing")
            stats = {}
            msg = pb.print_file(path, p["printer"], copies, title=f"PrintBridge - {name}", opts=p.get("opts"),
                                cancel=job["cancel"], spool=job["spool"], stats=stats)
        if job["cancel"].is_set():
            raise pb.Cancelled()
        us.log("phone", p.get("uid", ""), phone, name, stats.get("sheets"), copies, stats.get("color", ""), p["printer"])
        status("done", msg + p.get("note", ""))
        emit(f"Printed: '{name}' -> {p['printer']}")
    except pb.Cancelled:
        pb.cancel_spool(job["printer"], job["spool"])
        status("cancelled", "Print cancelled")
        emit(f"Cancelled: '{name}'")
    except Exception as e:
        log.exception("job fail")
        status("error", str(e)[:250])
        emit(f"ERROR: '{name}' could not be printed: {e}")
    finally:
        ACTIVE.pop(jid, None)


def rpc_cancel(p):
    if not check_token(p.get("token")):
        return {"ok": False, "auth": False, "error": "This device is not connected to the computer"}
    job = ACTIVE.get(p.get("job_id"))
    if not job:
        return {"ok": True, "message": "The print job had already finished"}
    job["cancel"].set()
    pb.cancel_spool(job["printer"], job["spool"])  # jo Windows queue mein pahunch chuka, use bhi hatao
    return {"ok": True}


def rpc_scan(p):
    if not check_token(p.get("token")):
        return {"ok": False, "auth": False, "error": "This device is not connected to the computer"}
    if not sc.list_scanners():
        return {"ok": False, "error": "No scanner is connected to the computer"}
    threading.Thread(target=scan_worker, args=(p,), daemon=True).start()
    return {"ok": True}


SCAN_LOCK = threading.Lock()


def scan_worker(p):
    sid = p["scan_id"]
    path = os.path.join(JOBS_DIR, f"scan_{sid[:8]}.jpg")

    def status(st, msg):
        CLOUD.send({"type": "scan_status", "scan_id": sid, "status": st, "message": msg})

    try:
        emit(f"Scan requested by {p.get('phone', 'Phone')}")
        with SCAN_LOCK:
            status("scanning", "Scanning...")
            sc.scan(path, color=p.get("color", "color"), dpi=int(p.get("dpi", 200)))
        with open(path, "rb") as f:
            data = f.read()
        req = urllib.request.Request(f"{server_base()}/api/agent/scans/{sid}", data=data, method="POST",
                                     headers={"X-Up-Key": p["up_key"], "Content-Type": "image/jpeg",
                                              "User-Agent": f"PrintBridge/{VERSION}"})
        with urllib.request.urlopen(req, timeout=180) as r:
            r.read()
        emit("Scan sent to the device")
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
    emit(f"Received offline from {phone}: '{filename}' ({copies} {'copy' if copies == 1 else 'copies'})")
    try:
        stats = {}
        with PRINT_LOCK:
            msg = pb.print_file(path, printer, copies, title=f"PrintBridge - {filename}", opts=pb.clean_opts(opts),
                                stats=stats)
        us.log("local", "local:" + phone, phone, filename, stats.get("sheets"), copies, stats.get("color", ""), printer)
        emit(f"Printed: '{filename}' -> {printer}")
        return msg
    except Exception as e:
        emit(f"ERROR: '{filename}' could not be printed: {e}")
        raise


def self_print(path, printer, copies, opts):
    """Laptop par hi file chun kar isi laptop ke printer se print ('Print karein' window)."""
    names, default, _, _ = pb.list_printers()
    if printer not in names:
        printer = CFG.get("printer") if CFG.get("printer") in names else default
    if not printer:
        raise pb.PrintError("No printer was found on this computer")
    name = os.path.basename(path)
    copies = max(1, min(int(copies), 50))
    stats = {}
    with PRINT_LOCK:
        pb.print_file(path, printer, copies, title=f"PrintBridge - {name}", opts=pb.clean_opts(opts), stats=stats)
    us.log("self", "self", "This computer", name, stats.get("sheets"), copies, stats.get("color", ""), printer)
    emit(f"Printed from this computer: '{name}' -> {printer}")


# ---------------------------------------------------------------- Print shop mode
SHOP_DIRTY = threading.Event()   # window ko taaza karna hai


def shop_changed(kind, order):
    SHOP_DIRTY.set()
    if kind == "new":
        emit(f"SHOP: new order #{order['token']} - {order['customer']}")
        UI_EVENTS.put(("shop_new", order["token"], order["customer"]))
    elif kind == "auto_print":
        threading.Thread(target=shop_print, args=(order["id"],), daemon=True).start()


SHOP = sh.ShopQueue(DATA_DIR, lambda: CFG["shop"], on_change=shop_changed)


def shop_name():
    return CFG["shop"].get("name") or CFG["name"]


DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _fmt_time(hhmm):
    try:
        h, m = map(int, hhmm.split(":"))
        return datetime(2000, 1, 1, h, m).strftime("%I:%M %p").lstrip("0")
    except Exception:
        return hhmm


def shop_hours_text():
    s = CFG["shop"]
    if not s.get("hours_on"):
        return ""
    days = s.get("days") or []
    d = "Every day" if len(days) == 7 else ", ".join(DAY_NAMES[i] for i in sorted(days)) or "Closed all week"
    return f"{d}, {_fmt_time(s.get('open_at', '09:00'))} – {_fmt_time(s.get('close_at', '21:00'))}"


def shop_open_now():
    """(khuli hai?, wajah). Shop band ho ya samay ke bahar ho to customer ko saaf sandesh."""
    s = CFG["shop"]
    if not s.get("enabled"):
        return False, "This shop is not accepting online orders right now. Please ask at the counter."
    if s.get("hours_on"):
        now = datetime.now()
        hm = now.strftime("%H:%M")
        o, c = s.get("open_at", "09:00"), s.get("close_at", "21:00")
        inside = (o <= hm < c) if o <= c else (hm >= o or hm < c)   # raat bhar khuli shop bhi
        if now.weekday() not in (s.get("days") or []) or not inside:
            return False, f"This shop is closed right now. Opening hours: {shop_hours_text()}."
    return True, ""


def shop_printer_for(color, fallback):
    """Color file -> color printer, B&W -> B&W printer (shop settings mein chuna ho to)."""
    names, default, _, _ = pb.list_printers()
    want = CFG["shop"].get("color_printer" if color == "color" else "bw_printer") or ""
    if want in names:
        return want
    return fallback if fallback in names else (CFG.get("printer") if CFG.get("printer") in names else default)


def shop_link():
    b = server_base()
    return f"{b}/s/{CFG['device_id'].replace('PB-', '').replace('-', '')}" if b else ""


def shop_print(order_id, printer=None):
    names, default, _, _ = pb.list_printers()
    printer = printer if printer in names else (CFG.get("printer") if CFG.get("printer") in names else default)
    o = SHOP.get(order_id)
    if not o:
        return False, "Order not found"
    emit(f"SHOP: printing order #{o['token']} ({o['customer']})")

    def one(path, prn, copies, opts, stats, f, order):
        prn = shop_printer_for((opts or {}).get("color"), prn)
        with PRINT_LOCK:
            msg = pb.print_file(path, prn, copies, title=f"Shop #{order['token']} - {f['name']}", opts=opts, stats=stats)
        sheets = stats.get("sheets")
        amount = round(sheets * copies * SHOP.rate(stats.get("color")), 2) if sheets is not None else ""
        us.log("shop", "shop:" + order["customer"], f"#{order['token']} {order['customer']}", f["name"], sheets,
               copies, stats.get("color", ""), prn, "done", amount)
        return msg

    ok, err = SHOP.print_order(order_id, printer, one)
    emit(f"SHOP: order #{o['token']} " + ("ready" if ok else f"ERROR {err}"))
    return ok, err


def shop_done(order_id):
    """Customer ne print le liya. Rupaye badle gaye the to report mein farq jodo."""
    o = SHOP.get(order_id)
    if not o:
        return
    if o.get("amount_override") is not None and o["status"] in ("ready", "waiting"):
        diff = round(o["amount_override"] - o.get("calc_total", 0), 2)
        if diff:
            us.log("shop", "shop:" + o["customer"], f"#{o['token']} {o['customer']}", "(amount adjusted)", None, 1, "",
                   "", "done", diff)
    SHOP.set_status(order_id, "done", "Collected")


def valid_order_id(x):
    return isinstance(x, str) and 16 <= len(x) <= 40 and x.replace("-", "").replace("_", "").isalnum()


def rpc_shop_info(p):
    s = CFG["shop"]
    ok, why = shop_open_now()
    if not ok:
        return {"ok": False, "error": why}
    caps = {"duplex": False, "papers": []}
    try:
        _, _, _, allcaps = pb.list_printers()
        caps = allcaps.get(shop_printer_for("bw", None), caps)
    except Exception:
        pass
    return {"ok": True, "name": shop_name(), "bw_rate": s["bw_rate"], "color_rate": s["color_rate"],
            "message": s.get("message", ""), "allow_private": s.get("allow_private", True), "caps": caps,
            "hours": shop_hours_text(), "min_charge": s.get("min_charge", 0), "bulk_after": s.get("bulk_after", 0),
            "bulk_pct": s.get("bulk_pct", 0), "require_mobile": bool(s.get("require_mobile")),
            "max_files": s.get("max_files", 20)}


def rpc_shop_job(p):
    ok, why = shop_open_now()
    if not ok and not SHOP.get_open(p.get("order_id")):   # chalu order mein file jodna samay ke baad bhi theek
        return {"ok": False, "error": why}
    if CFG["shop"].get("require_mobile") and len("".join(ch for ch in str(p.get("mobile", "")) if ch.isdigit())) < 10:
        return {"ok": False, "error": "Please enter your mobile number"}
    if not valid_order_id(p.get("order_id")):
        return {"ok": False, "error": "Invalid order"}
    name = str(p.get("filename", "file"))
    if os.path.splitext(name)[1].lower() not in pb.ALLOWED_EXT:
        return {"ok": False, "error": f"'{name}' cannot be printed"}
    private = bool(p.get("private"))
    if private and not CFG["shop"].get("allow_private", True):
        return {"ok": False, "error": "This shop does not accept private (print-only) orders"}
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
            CLOUD.send({"type": "job_status", "job_id": p["job_id"], "status": "done", "message": "Received by the shop"})
            SHOP.file_ready(o["id"], f["fid"], True)
        except Exception as e:
            CLOUD.send({"type": "job_status", "job_id": p["job_id"], "status": "error", "message": str(e)[:200]})
            SHOP.file_ready(o["id"], f["fid"], False, str(e))
    threading.Thread(target=fetch, daemon=True).start()
    return {"ok": True, "token": o["token"]}


def rpc_shop_status(p):
    st = SHOP.status(p.get("order_id")) if valid_order_id(p.get("order_id")) else None
    if not st:
        return {"ok": False, "error": "Order not found (it may have expired)"}
    st.update(ok=True, shop=shop_name())
    return st


def rpc_shop_cancel(p):
    if not valid_order_id(p.get("order_id")):
        return {"ok": False, "error": "Invalid order"}
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


def app_dir():
    """Nayi exe yahan rakhte hain: %LOCALAPPDATA%/PrintBridge/app (OneDrive/Documents se bahar, koi lock nahi)."""
    base = os.environ.get("LOCALAPPDATA") or DATA_DIR
    d = os.path.join(base, "PrintBridge", "app")
    os.makedirs(d, exist_ok=True)
    return d


def install_update(ver, url):
    """Naya tareeka (v1.4): purani exe ko BADALTE NAHI. Nayi exe alag naam se rakhte hain,
    use chalate hain, aur Windows-startup ko nayi exe par set karte hain. Na kaala window, na file lock."""
    target = os.path.join(app_dir(), f"PrintBridge-{ver}.exe")
    tmp = target + ".download"
    emit(f"New version v{ver} found. Downloading...")
    req = urllib.request.Request(url, headers={"User-Agent": f"PrintBridge/{VERSION}"})
    with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    with open(tmp, "rb") as f:
        ok = f.read(2) == b"MZ"
    if not ok or os.path.getsize(tmp) < 1_000_000:
        os.remove(tmp)
        raise RuntimeError("incomplete download")
    os.replace(tmp, target)
    with CFG_LOCK:
        CFG["installed_exe"], CFG["installed_version"] = target, ver
        save_config()
    try:
        set_autostart(True, exe=target)   # agli baar Windows nayi exe chalaye
    except Exception:
        log.exception("autostart update")
    emit(f"Starting v{ver}...")
    log.info("update to %s -> %s", ver, target)
    with PRINT_LOCK:  # koi print chal raha ho to uske khatam hone ka intezaar
        subprocess.Popen([target, "--minimized", "--after-update"], close_fds=True,
                         creationflags=0x00000008 | 0x00000200)  # DETACHED | NEW_PROCESS_GROUP (exe windowed hai, koi console nahi)
        time.sleep(0.3)
        os._exit(0)


def desktop_shortcut(target, create=True):
    """Desktop par 'PrintBridge' shortcut: banao (create=True) ya pehle se ho to nayi exe par set karo."""
    if not (FROZEN and sys.platform == "win32"):
        return
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        ws = win32com.client.Dispatch("WScript.Shell")
        lnk = os.path.join(ws.SpecialFolders("Desktop"), "PrintBridge.lnk")
        if not create and not os.path.exists(lnk):
            return
        sc = ws.CreateShortcut(lnk)
        sc.TargetPath = target
        sc.WorkingDirectory = os.path.dirname(target)
        sc.IconLocation = target + ",0"
        sc.Description = "PrintBridge - mobile se print"
        sc.Save()
    except Exception:
        log.exception("desktop shortcut")


def sendto_shortcut(target):
    """Windows Explorer: file par right-click -> 'Send to' -> 'PrintBridge se print'."""
    if not (FROZEN and sys.platform == "win32"):
        return
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        ws = win32com.client.Dispatch("WScript.Shell")
        lnk = os.path.join(ws.SpecialFolders("SendTo"), "PrintBridge se print.lnk")
        sc = ws.CreateShortcut(lnk)
        if os.path.exists(lnk) and os.path.normcase(sc.TargetPath or "") == os.path.normcase(target):
            return
        sc.TargetPath = target
        sc.Arguments = "--send"
        sc.WorkingDirectory = os.path.dirname(target)
        sc.IconLocation = target + ",0"
        sc.Description = "Print with PrintBridge (on this or another computer)"
        sc.Save()
    except Exception:
        log.exception("sendto shortcut")


def install_self():
    """exe kahin se bhi chalayi (Downloads/Desktop): khud ko pakki jagah copy karo, startup wahin se."""
    if not (FROZEN and sys.platform == "win32"):
        return
    if os.path.normcase(os.path.dirname(sys.executable)) == os.path.normcase(app_dir()):
        return
    target = os.path.join(app_dir(), f"PrintBridge-{VERSION}.exe")
    try:
        if not os.path.exists(target):
            shutil.copy2(sys.executable, target)
        with CFG_LOCK:
            CFG["installed_exe"], CFG["installed_version"] = target, VERSION
            save_config()
        desktop_shortcut(target, create=True)
        log.info("installed copy: %s", target)
    except Exception:
        log.exception("install_self")


def redirect_to_newer():
    """Purani exe chalayi gayi (jaise purana shortcut) aur nayi install hai -> nayi chalao, purani band."""
    if not (FROZEN and sys.platform == "win32"):
        return False
    inst, iv = CFG.get("installed_exe"), CFG.get("installed_version")
    if not inst or not os.path.exists(inst) or os.path.normcase(inst) == os.path.normcase(sys.executable):
        return False
    if (parse_version(iv) or (0,)) <= parse_version(VERSION):
        return False
    try:
        args = [a for a in sys.argv[1:] if a != "--after-update"]
        subprocess.Popen([inst] + args, close_fds=True, creationflags=0x00000008 | 0x00000200)
        return True
    except Exception:
        log.exception("redirect")
        return False


def tidy_after_update():
    """Nayi exe chalne ke baad: apni jagah yaad rakho, purani PrintBridge-*.exe aur adhoore downloads hatao."""
    if not (FROZEN and sys.platform == "win32"):
        return
    me = sys.executable
    if os.path.normcase(os.path.dirname(me)) == os.path.normcase(app_dir()) and \
            (parse_version(CFG.get("installed_version")) or (0,)) <= parse_version(VERSION):
        with CFG_LOCK:
            CFG["installed_exe"], CFG["installed_version"] = me, VERSION
            save_config()
        desktop_shortcut(me, create=False)  # update ke baad shortcut nayi exe par
    d = app_dir()
    keep = {os.path.normcase(me), os.path.normcase(CFG.get("installed_exe") or "")}
    for n in os.listdir(d):
        fp = os.path.join(d, n)
        if os.path.normcase(fp) in keep:
            continue
        if n.endswith(".download") or (n.startswith("PrintBridge-") and n.endswith(".exe")):
            try:
                os.remove(fp)
            except OSError:
                pass
    # v1.3 tak ka purana tareeka: bachi hui ".new" file aur update.bat
    for fp in (me + ".new", os.path.join(DATA_DIR, "update.bat")):
        try:
            os.remove(fp)
        except OSError:
            pass


def update_now(manual=False):
    """manual=True: 'Update' button se. Sirf exe (Windows) par khud update hota hai."""
    if UPDATE_STATE["busy"]:
        return
    UPDATE_STATE["busy"] = True
    try:
        found = check_for_update()
        if not found:
            if manual:
                emit(f"You are using the latest version (v{VERSION})")
            return
        ver, url = found
        if not (FROZEN and sys.platform == "win32"):
            emit(f"Version v{ver} is available (automatic updates work only in PrintBridge.exe)")
            return
        # Ek hi version ka update baar-baar fail ho to apne aap koshish band (chakkar na chale)
        att = CFG.get("update_attempt") or {}
        if att.get("ver") == ver and time.time() - att.get("time", 0) < 24 * 3600:
            if att.get("count", 0) >= 2 and not manual:
                log.info("update %s already attempted twice, skipping for now", ver)
                return
            att["count"] = att.get("count", 0) + 1
        else:
            att = {"ver": ver, "time": time.time(), "count": 1}
        with CFG_LOCK:
            CFG["update_attempt"] = att
            save_config()
        install_update(ver, url)
    except Exception as e:
        log.info("update check fail: %r", e)
        if manual:
            emit(f"Could not check for updates: {e}")
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
        self.status = ("off", "Starting...")
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
                self.set_status("off", "Server URL is not set")
                await asyncio.sleep(2)
                continue
            ws_url = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/ws/agent"
            self.set_status("wait", "Connecting to server...")
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
                                self.set_status("on", "Online — ready to receive prints")
                                emit("Connected to server")
                            elif t == "rpc":
                                asyncio.create_task(self._rpc(ws, msg))
                    finally:
                        hb.cancel()
                        self.ws = None
                if ws.close_code == 4002:
                    self.set_status("off", "This Computer ID is already in use on another computer")
            except Exception as e:
                log.info("connect fail: %r", e)
                self.ws = None
            if self.status[0] == "on":
                emit("Connection to server lost. Reconnecting...")
            self.set_status("wait", f"Cannot reach the server. Retrying in {backoff} seconds...")
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


def autostart_command(exe=None):
    if exe:
        return f'"{exe}" --minimized'
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


def set_autostart(on, exe=None):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if on:
            winreg.SetValueEx(k, "PrintBridge", 0, winreg.REG_SZ, autostart_command(exe))
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
    exe = CFG.get("installed_exe") if CFG.get("installed_exe") and os.path.exists(CFG["installed_exe"]) else None
    if sys.platform == "win32" and FROZEN and not CFG.get("autostart_done"):
        try:
            set_autostart(True, exe=exe)
        except Exception:
            log.exception("autostart")
        with CFG_LOCK:
            CFG["autostart_done"] = True
            save_config()
    elif sys.platform == "win32" and autostart_enabled():
        try:
            set_autostart(True, exe=exe)  # hamesha pakki jagah wali exe
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

    import ui_kit as ui
    root = tk.Tk()
    root.title("PrintBridge")
    ui.setup_styles(root)
    sh = root.winfo_screenheight()
    compact = sh < 940            # chhoti screen (jaise 1366x768): QR aur activity chhote
    QR = 118 if compact else 150
    root.geometry(f"480x{min(860, sh - 70)}+20+10")
    root.minsize(440, 600)
    root.configure(bg=ui.C["bg"])
    icon_path = os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "icon.png")
    try:
        if os.path.exists(icon_path):
            root.iconphoto(True, ImageTk.PhotoImage(Image.open(icon_path)))
    except Exception:
        pass
    F = "Segoe UI"
    BLUE = ui.C["blue"]
    C = ui.C

    # ---------------------------------------------------- header (phone app jaisa)
    hdr, hright = ui.header(root, "PrintBridge", f"Version {VERSION}")
    status_lbl = tk.Label(hright, font=ui.font(9, True), padx=12, pady=4, bg="white", fg=C["mut"], text="● Starting…")
    status_lbl.pack()

    body = tk.Frame(root, bg=C["bg"], padx=14, pady=12)
    body.pack(fill="both", expand=True)

    # ---------------------------------------------------- 1. phone jodna
    c1 = ui.card(body)
    c1.pack(fill="x")
    ui.section(c1, "Connect a phone").grid(row=0, column=0, columnspan=2, sticky="w")
    qr_lbl = tk.Label(c1, bg="white")
    qr_lbl.grid(row=1, column=0, rowspan=5, sticky="nw", pady=(8, 0), padx=(0, 16))
    ui.label(c1, "Computer ID", 9, color=C["mut"]).grid(row=1, column=1, sticky="w", pady=(10, 0))
    id_lbl = tk.Label(c1, font=("Consolas", 19, "bold"), fg=BLUE, bg="white")
    id_lbl.grid(row=2, column=1, sticky="w")
    ui.label(c1, "PIN", 9, color=C["mut"]).grid(row=3, column=1, sticky="w", pady=(6, 0))
    pinrow = tk.Frame(c1, bg="white")
    pinrow.grid(row=4, column=1, sticky="w")
    pin_lbl = tk.Label(pinrow, font=("Consolas", 19, "bold"), fg=C["text"], bg="white")
    pin_lbl.pack(side="left")
    site_lbl = ui.label(c1, "", 8, color=C["mut"], justify="left")
    site_lbl.grid(row=5, column=1, sticky="sw", pady=(6, 0))
    c1.columnconfigure(1, weight=1)

    # ---------------------------------------------------- 2. printer + print
    c2 = ui.card(body)
    c2.pack(fill="x", pady=(10, 0))
    ui.section(c2, "Default printer").pack(anchor="w")
    printer_var = tk.StringVar(root)
    printer_box = ttk.Combobox(c2, textvariable=printer_var, state="readonly")
    printer_box.pack(fill="x", pady=(6, 10))

    def open_send_win():
        import gui_send
        gui_send.open_send(root, M)
    ui.Button(c2, "Print a file", open_send_win, size=11, pady=10).pack(fill="x")
    ui.label(c2, "Print here, or send to a printer on another computer", 8, color=C["mut"]).pack(pady=(6, 0))

    def load_printers():
        try:
            names, default, _, _ = pb.list_printers()
        except Exception as e:
            names, default = [], None
            emit(f"ERROR loading printers: {e}")
        printer_box["values"] = names
        cur = CFG.get("printer") if CFG.get("printer") in names else default
        printer_var.set(cur or "(no printer found)")

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
        n = len(CFG["phones"])
        site_lbl.configure(text=f"Phone app: {host}\n{n} connected {'device' if n == 1 else 'devices'}")
        url = pairing_url()
        img = ImageTk.PhotoImage(make_qr_image(url, QR) if url else Image.new("RGB", (QR, QR), "#eeeeee"))
        qr_lbl.configure(image=img)
        qr_lbl.image = img
        devices_btn.configure(text=f"Devices ({n})")

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
        emit("New PIN generated (connected devices keep working)")

    def remove_phones():
        if messagebox.askyesno("Remove all devices", "All connected devices will be removed and will need to connect again.\n"
                                                "The PIN will also change. Continue?"):
            with CFG_LOCK:
                CFG["phones"] = {}
                CFG["local_key"] = secrets.token_urlsafe(12)
                save_config()
            change_pin()
            refresh()
            emit("All devices removed and a new PIN generated")

    ui.Button(pinrow, "New PIN", lambda: new_pin_click(), kind="ghost", size=9, padx=8, pady=3).pack(side="left", padx=(10, 0))

    # ---------------------------------------------------- 3. tools
    tools = tk.Frame(body, bg=C["bg"])
    tools.pack(fill="x", pady=(10, 0))
    for i in range(3):
        tools.columnconfigure(i, weight=1, uniform="t")

    def offline_qr():
        win = tk.Toplevel(root)
        win.title("Offline printing")
        win.configure(bg="white")
        win.resizable(False, False)
        ui.header(win, "Offline printing", "Print over your local Wi-Fi, without internet", color="#0f766e")
        if LOCAL.httpd is None:
            tk.Label(win, text=f"Offline printing is not available.\n{LOCAL.error or ''}", fg="#dc2626",
                     bg="white", font=(F, 10)).pack(padx=20, pady=10)
            return
        tk.Label(win, text="Print even without an internet connection.\n"
                           "1. Connect the phone and this computer to the SAME Wi-Fi network\n"
                           "2. Scan this QR code with the phone camera",
                 font=(F, 10), bg="white", justify="left").pack(padx=20)
        img = ImageTk.PhotoImage(make_qr_image(LOCAL.url(), 230))
        q = tk.Label(win, image=img, bg="white")
        q.image = img
        q.pack(pady=8)
        tk.Label(win, text=LOCAL.url().split("#")[0], font=(F, 9), fg="#666", bg="white").pack()
        tk.Label(win, text="If the page does not open, allow PrintBridge on\n"
                           "'Private networks' in Windows Firewall.",
                 font=(F, 8), fg="#888", bg="white").pack(pady=(6, 14))

    def more_menu():
        mnu = tk.Menu(root, tearoff=0, font=ui.font(10))
        mnu.add_command(label="Refresh printers", command=lambda: (refresh(), load_printers()))
        mnu.add_command(label="Server address…", command=ask_server)
        mnu.add_separator()
        mnu.add_command(label="Remove all devices…", command=remove_phones)
        b = more_btn
        mnu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def tool(r, c, text, cmd):
        btn = ui.Button(tools, text, cmd, kind="secondary", size=9, bold=False, pady=9)
        btn.grid(row=r, column=c, sticky="ew", padx=(0 if c == 0 else 6, 0), pady=(0 if r == 0 else 6, 0))
        return btn
    tool(0, 0, "Print shop", lambda: gx.open_shop(root, M))
    devices_btn = tool(0, 1, "Devices", lambda: gx.open_phones(root, M, refresh))
    tool(0, 2, "Report", lambda: gx.open_report(root, M))
    tool(1, 0, "Offline QR", offline_qr)
    tool(1, 1, "Check for updates", lambda: threading.Thread(target=update_now, args=(True,), daemon=True).start())
    more_btn = tool(1, 2, "More…", more_menu)

    opts = tk.Frame(body, bg=C["bg"])
    opts.pack(side="bottom", fill="x", pady=(8, 0))   # hamesha dikhe (chhoti screen par bhi)

    # ---------------------------------------------------- 4. activity
    c3 = ui.card(body, pady=12)
    c3.pack(fill="both", expand=True, pady=(10, 0))
    ui.section(c3, "Activity").pack(anchor="w")
    log_box = ui.listbox(c3, height=3 if compact else 5)
    log_box.configure(highlightthickness=0)
    log_box.pack(fill="both", expand=True, pady=(6, 0))
    phones_lbl = tk.Label(c3)   # purane code ke liye (ab dikhaya nahi jaata)

    approve_var = tk.BooleanVar(value=CFG.get("ask_approval", True))

    def on_approve():
        with CFG_LOCK:
            CFG["ask_approval"] = approve_var.get()
            save_config()
    ttk.Checkbutton(opts, text="Ask for approval before a new device connects", variable=approve_var,
                    command=on_approve, style="Bg.TCheckbutton").pack(anchor="w")

    if sys.platform == "win32":
        auto_var = tk.BooleanVar(value=autostart_enabled())

        def on_auto():
            try:
                inst = CFG.get("installed_exe")
                set_autostart(auto_var.get(), exe=inst if inst and os.path.exists(inst) else None)
                emit("Start with Windows: " + ("ON" if auto_var.get() else "OFF"))
            except Exception as e:
                messagebox.showerror("PrintBridge", str(e))
        ttk.Checkbutton(opts, text="Start automatically with Windows", variable=auto_var,
                        command=on_auto, style="Bg.TCheckbutton").pack(anchor="w")

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
        ok = messagebox.askyesno("New device", f"'{phone}' wants to connect to this computer's printers.\n\n"
                                               "Do you recognise this device?\n"
                                               "Allow the connection?", parent=root)
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
        status_lbl.configure(text="● " + {"on": "Online", "wait": "Connecting…", "off": "Offline"}[kind], fg=colors[kind],
                             bg={"on": "#dcfce7", "wait": "#fef3c7", "off": "#fee2e2"}[kind])
        if hdr.sub_lbl is not None and hdr.sub_lbl.cget("text") != f"Version {VERSION}  •  {text}":
            hdr.sub_lbl.configure(text=f"Version {VERSION}  •  {text}")
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
                if CFG["shop"].get("sound", True):
                    root.bell()
                try:
                    if tray["icon"] is not None:
                        tray["icon"].notify(f"New order #{ev[1]}: {ev[2]}", "PrintBridge - Print shop")
                except Exception:
                    pass
                continue
            if isinstance(ev, tuple) and ev[0] == "send":
                show_window()
                import gui_send
                gui_send.open_send(root, M, ev[1])
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
                pystray.MenuItem("Open PrintBridge", lambda: UI_EVENTS.put("show"), default=True),
                pystray.MenuItem("Print a file...", lambda: UI_EVENTS.put(("send", []))),
                pystray.MenuItem("Exit PrintBridge", lambda: UI_EVENTS.put("quit")))
            icon = pystray.Icon("PrintBridge", img, "PrintBridge is running", menu)

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
                tray["icon"].notify("PrintBridge is still running in the background and will keep receiving prints.\n"
                                    "Double-click the PrintBridge icon in the system tray to open it.",
                                    "PrintBridge")
            except Exception:
                pass
            with CFG_LOCK:
                CFG["tray_told"] = True
                save_config()

    def quit_app():
        show_window()
        if messagebox.askyesno("Exit PrintBridge?",
                               "PrintBridge will close completely and will NOT receive any prints.\n\n"
                               "Exit now?", parent=root):
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
    print(f"Computer ID: {CFG['device_id']}")
    print(f"Server    : {server_base() or '(not set)'}")
    if pairing_url():
        print(f"QR link   : {pairing_url()}\n")
        qr = qrcode.QRCode(border=1)
        qr.add_data(pairing_url())
        qr.print_ascii(invert=True)
    if LOCAL.httpd is not None:
        print(f"Offline    : {LOCAL.url()}")
    print("\nPress Ctrl+C to exit.\n", flush=True)
    last = None
    try:
        while True:
            try:
                print(EVENTS.get(timeout=1), flush=True)
            except queue.Empty:
                pass
            if CLOUD.pin_changed:
                CLOUD.pin_changed = False
                print(f"[PIN changed] New PIN: {CFG['pin']}", flush=True)
            if CLOUD.status != last:
                last = CLOUD.status
                print(f"[status] {last[1]}", flush=True)
    except KeyboardInterrupt:
        pass


LOCK_PORT = int(os.environ.get("PRINTBRIDGE_LOCK_PORT", "47651"))


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
                    data = b""
                    while len(data) < 256 * 1024:
                        chunk = c.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                    if data.startswith(b"send\n"):   # doosri baar file ke saath khola (Send to / icon par file)
                        paths = [x for x in data[5:].decode("utf-8", "replace").split("\n") if x.strip()]
                        UI_EVENTS.put(("send", paths))
                    elif data.startswith(b"show"):
                        UI_EVENTS.put("show")
            except Exception:
                time.sleep(0.5)
    threading.Thread(target=accept_loop, daemon=True).start()
    return s


def show_running_instance(files=None):
    try:
        with socket.create_connection(("127.0.0.1", LOCK_PORT), timeout=2) as c:
            c.sendall(("send\n" + "\n".join(files)).encode("utf-8") if files else b"show")
        return True
    except OSError:
        return False


def files_from_args():
    """'Send to' ya exe ke icon par file chhodne se aayi files."""
    return [os.path.abspath(a) for a in sys.argv[1:] if not a.startswith("--") and os.path.isfile(a)]


def main():
    if "--server" in sys.argv:
        CFG["server_url"] = sys.argv[sys.argv.index("--server") + 1]
        save_config()
    save_config()
    if redirect_to_newer():
        return  # nayi exe chala di, ye purani band
    lock = single_instance()
    if not lock and "--after-update" in sys.argv:
        for _ in range(40):  # purana program band hone ka intezaar (max ~20 sec)
            time.sleep(0.5)
            lock = single_instance()
            if lock:
                break
    if not lock:
        if "--nogui" not in sys.argv and show_running_instance(files_from_args()):
            return  # pehle se chal raha hai: uski window saamne aa gayi
        msg = "PrintBridge is already running."
        if "--nogui" in sys.argv:
            print(msg)
        else:
            import tkinter as tk
            from tkinter import messagebox
            tk.Tk().withdraw()
            messagebox.showinfo("PrintBridge", msg)
        return
    log.info("start v%s id=%s", VERSION, CFG["device_id"])
    install_self()
    tidy_after_update()
    first_run_setup()
    if not LOCAL.start():
        log.info("local server: %s", LOCAL.error)
    CLOUD.start()
    threading.Thread(target=cleanup_loop, daemon=True).start()
    threading.Thread(target=update_loop, daemon=True).start()
    if CFG.get("installed_exe") and os.path.exists(CFG["installed_exe"]):
        threading.Thread(target=sendto_shortcut, args=(CFG["installed_exe"],), daemon=True).start()
    elif FROZEN:
        threading.Thread(target=sendto_shortcut, args=(sys.executable,), daemon=True).start()
    if files_from_args():
        UI_EVENTS.put(("send", files_from_args()))
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
