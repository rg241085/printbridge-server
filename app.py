"""
PrintBridge cloud server (v0.2)

Kaam:
  - Laptop program yahan WebSocket se juda rehta hai (laptop hamesha khud connect karta hai,
    isliye router/firewall ki koi setting nahi chahiye).
  - Phone app (isi server se khulti hai) Laptop ID + PIN se laptop ko jodti hai,
    aur file upload karti hai.
  - Server file ko kuch der rakhta hai, laptop ko batata hai, laptop file download karke print karta hai.

Server kisi ka password/token save nahi karta:
  - Laptop ID uske secret se banti hai (sha256), isliye koi aur us ID ka daawa nahi kar sakta.
  - Phone ka token sirf laptop check karta hai.
"""
import asyncio
import datetime
import hashlib
import hmac
import json
import os
import secrets
import shutil
import tempfile
import time
import uuid

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

VERSION = "2.7"
# Laptop program (PrintBridge.exe) ka download link. GitHub Actions ise har update par banata hai.
DOWNLOAD_URL = os.environ.get(
    "DOWNLOAD_URL", "https://github.com/rg241085/printbridge-server/releases/download/latest/PrintBridge.exe")
PAIR_FAIL_LIMIT = 10        # ek IP se 10 minute me itne galat PIN ke baad roko
PAIR_FAIL_WINDOW = 10 * 60
APP_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(APP_DIR, "web")
JOB_DIR = os.path.join(tempfile.gettempdir(), "printbridge_jobs")
os.makedirs(JOB_DIR, exist_ok=True)

MAX_MB = 50
JOB_TTL = 15 * 60  # 15 minute baad job aur file hata do
ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ALLOWED_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".txt", ".doc", ".docx",
               ".xls", ".xlsx", ".ppt", ".pptx", ".rtf", ".odt"}


def id_from_secret(secret: str) -> str:
    h = hashlib.sha256(("printbridge:" + secret).encode()).digest()
    c = "".join(ID_ALPHABET[b % 32] for b in h[:8])
    return f"PB-{c[:4]}-{c[4:]}"


def norm_id(s: str) -> str:
    s = "".join(ch for ch in (s or "").upper() if ch.isalnum())
    if s.startswith("PB"):
        s = s[2:]
    return f"PB-{s[:4]}-{s[4:8]}" if len(s) == 8 else ""


# ---------------------------------------------------------------- subscription (license)
# Har computer: pehle 6 mahine free (trial), phir Rs 30 / saal ka code.
# Server kuch save nahi karta: code aur trial-parchi dono gupt chabi (PB_LICENSE_SECRET) se sign hote hain,
# isliye server restart hone par bhi sab sahi rehta hai. Chabi GitHub par nahi, sirf Render ki settings mein.
LICENSE_SECRET = os.environ.get("PB_LICENSE_SECRET", "").encode()
ADMIN_KEY = os.environ.get("PB_ADMIN_KEY", "")
TRIAL_DAYS = int(os.environ.get("PB_TRIAL_DAYS", "183"))
PRICE_TEXT = os.environ.get("PB_PRICE", "Rs 30 / year")
PAY_UPI = os.environ.get("PB_PAY_UPI", "")              # jaise ravi@upi
PAY_WHATSAPP = os.environ.get("PB_PAY_WHATSAPP", "")    # jaise 919876543210
LEGACY_UNTIL = os.environ.get("PB_LEGACY_UNTIL", "")      # jaise 2027-01-31: uske baad purana exe band
LIC_EPOCH = datetime.date(2026, 1, 1)
LICENSING = bool(LICENSE_SECRET)                         # chabi nahi = sab free (purana tareeka)
ACTIVATE_FAILS: dict[str, list] = {}


def today_day() -> int:
    return (datetime.date.today() - LIC_EPOCH).days


def day_to_date(d: int) -> str:
    return (LIC_EPOCH + datetime.timedelta(days=d)).isoformat()


def _mac(msg: str, n: int) -> bytes:
    return hmac.new(LICENSE_SECRET, msg.encode(), hashlib.sha256).digest()[:n]


def _b32(data: bytes, nchars: int) -> str:
    v, out = int.from_bytes(data, "big"), []
    v <<= nchars * 5 - len(data) * 8
    for _ in range(nchars):
        out.append(ID_ALPHABET[v & 31]); v >>= 5
    return "".join(reversed(out))


def _unb32(s: str):
    v = 0
    for ch in s:
        i = ID_ALPHABET.find(ch)
        if i < 0:
            return None
        v = v * 32 + i
    return v


def make_code(device_id: str, until_day: int) -> str:
    """Code = khatam hone ki tareekh (2 byte) + sign (5 byte) -> 12 akshar, jaise 7KQ2-M9XA-4TRB"""
    raw = until_day.to_bytes(2, "big") + _mac(f"code|{device_id}|{until_day}", 5)
    c = _b32(raw, 12)
    return f"{c[:4]}-{c[4:8]}-{c[8:]}"


def check_code(device_id: str, code: str):
    """Sahi code ho to uski aakhri tareekh (din), warna None"""
    s = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    if len(s) != 12 or not LICENSING:
        return None
    v = _unb32(s)
    if v is None:
        return None
    v >>= 4
    raw = v.to_bytes(7, "big")
    until = int.from_bytes(raw[:2], "big")
    return until if hmac.compare_digest(raw[2:], _mac(f"code|{device_id}|{until}", 5)) else None


def trial_sig(device_id: str, start: int) -> str:
    return _mac(f"trial|{device_id}|{start}", 8).hex()


def license_state(agent) -> dict:
    """Is computer ki subscription ka haal"""
    if not LICENSING:
        return {"state": "free", "licensing": False}
    t = today_day()
    if agent.legacy:   # purana laptop program (license wala hissa nahi): PB_LEGACY_UNTIL tak chalne do
        if LEGACY_UNTIL and datetime.date.today().isoformat() > LEGACY_UNTIL:
            return {"state": "expired", "licensing": True, "days_left": 0,
                    "message": "Please update PrintBridge on this computer (Check for updates)."}
        return {"state": "legacy", "licensing": True, "message": "Please update PrintBridge on this computer."}
    until_code = agent.code_until or -1
    until_trial = agent.trial_start + TRIAL_DAYS - 1 if agent.trial_start is not None else -1
    if until_code >= t:
        st, until = "pro", until_code
    elif until_trial >= t:
        st, until = "trial", until_trial
    else:
        st, until = "expired", max(until_code, until_trial)
    return {"state": st, "licensing": True, "until": day_to_date(until) if until >= 0 else None,
            "days_left": max(0, until - t + 1) if until >= 0 else 0, "price": PRICE_TEXT,
            "pay_upi": PAY_UPI, "pay_whatsapp": PAY_WHATSAPP, "trial_days": TRIAL_DAYS}


def require_license(agent, shop=False):
    if license_state(agent)["state"] == "expired":
        raise HTTPException(402, "This print shop's PrintBridge subscription has expired. Please let the shop owner know."
                            if shop else
                            f"The PrintBridge subscription on \"{agent.name}\" has expired. "
                            f"Renew it on that computer ({PRICE_TEXT}) to continue printing.")


# ---------------------------------------------------------------- laptops (agents)
class Agent:
    def __init__(self, ws: WebSocket, device_id: str, name: str, version: str):
        self.ws, self.id, self.name, self.version = ws, device_id, name, version
        self.pending: dict[str, asyncio.Future] = {}
        self.legacy, self.code_until, self.trial_start = True, None, None
        self.send_lock = asyncio.Lock()

    async def send(self, msg: dict):
        async with self.send_lock:
            await self.ws.send_text(json.dumps(msg))

    async def rpc(self, method: str, params: dict, timeout: float = 20):
        rid = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        try:
            await self.send({"type": "rpc", "rid": rid, "method": method, "params": params})
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise HTTPException(504, "The computer did not respond")
        finally:
            self.pending.pop(rid, None)


AGENTS: dict[str, Agent] = {}
JOBS: dict[str, dict] = {}
SCANS: dict[str, dict] = {}        # laptop ke scanner se phone tak scan
PAIR_FAILS: dict[str, list] = {}   # IP -> galat koshishon ka samay


def valid_pin(p) -> bool:
    return isinstance(p, str) and len(p) == 6 and p.isdigit()


def client_ip(req: Request) -> str:
    fwd = req.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (req.client.host if req.client else "?")

app = FastAPI(title="PrintBridge", docs_url=None, redoc_url=None)


def get_agent(device_id: str) -> Agent:
    a = AGENTS.get(norm_id(device_id))
    if not a:
        raise HTTPException(404, "The computer is offline. Make sure PrintBridge is running on it.")
    return a


@app.exception_handler(HTTPException)
async def _http_err(_req, exc: HTTPException):
    return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)


@app.websocket("/ws/agent")
async def agent_ws(ws: WebSocket):
    await ws.accept()
    try:
        hello = json.loads(await asyncio.wait_for(ws.receive_text(), 20))
        secret = str(hello.get("secret", ""))
        if hello.get("type") != "hello" or len(secret) < 20:
            await ws.close(code=4001)
            return
    except Exception:
        await ws.close(code=4001)
        return

    device_id = id_from_secret(secret)
    agent = Agent(ws, device_id, str(hello.get("name", "Computer"))[:60], str(hello.get("version", "")))
    old = AGENTS.get(device_id)
    AGENTS[device_id] = agent
    if old:
        try:
            await old.ws.close(code=4002)
        except Exception:
            pass
    await agent.send({"type": "welcome", "id": device_id, "server_version": VERSION})
    lic = hello.get("lic")
    if isinstance(lic, dict) and LICENSING:
        agent.legacy = False
        agent.code_until = check_code(device_id, str(lic.get("code", "")))
        try:
            st = int(lic.get("trial_start"))
        except (TypeError, ValueError):
            st = None
        if st is not None and hmac.compare_digest(str(lic.get("trial_sig", "")), trial_sig(device_id, st)):
            agent.trial_start = st
        elif st is None:          # pehli baar: aaj se trial shuru
            agent.trial_start = today_day()
        else:                     # parchi se chhed-chhaad: trial khatam
            agent.trial_start = -10 ** 6
    if isinstance(lic, dict):
        await send_license(agent)

    try:
        while True:
            msg = json.loads(await ws.receive_text())
            t = msg.get("type")
            if t == "rpc_result":
                fut = agent.pending.get(msg.get("rid"))
                if fut and not fut.done():
                    fut.set_result(msg.get("result") or {})
            elif t == "job_status":
                job = JOBS.get(msg.get("job_id"))
                if job and job["device_id"] == device_id:
                    job["status"] = msg.get("status", job["status"])
                    job["message"] = str(msg.get("message", ""))[:300]
                    if job["status"] in ("done", "error", "cancelled"):
                        _drop_file(job)
            elif t == "scan_status":
                sc = SCANS.get(msg.get("scan_id"))
                if sc and sc["device_id"] == device_id and sc["status"] != "done":
                    sc["status"] = msg.get("status", sc["status"])
                    sc["message"] = str(msg.get("message", ""))[:300]
            elif t == "rename":
                nm = str(msg.get("name", "")).strip()[:60]
                if nm:
                    agent.name = nm
            elif t == "activate":
                await activate(agent, str(msg.get("code", ""))[:40])
            elif t == "hb":
                await agent.send({"type": "hb_ack"})
    except (WebSocketDisconnect, RuntimeError, json.JSONDecodeError):
        pass
    finally:
        if AGENTS.get(device_id) is agent:
            del AGENTS[device_id]
        for fut in agent.pending.values():
            if not fut.done():
                fut.cancel()


async def send_license(agent, extra=None):
    info = license_state(agent)
    if LICENSING and not agent.legacy and agent.trial_start is not None and agent.trial_start > -10 ** 5:
        info["trial_start"], info["trial_sig"] = agent.trial_start, trial_sig(agent.id, agent.trial_start)
    info.update(extra or {})
    await agent.send({"type": "license", **info})


async def activate(agent, code: str):
    now = time.time()
    fails = [t for t in ACTIVATE_FAILS.get(agent.id, []) if now - t < 3600]
    ACTIVATE_FAILS[agent.id] = fails
    if not LICENSING:
        return await send_license(agent, {"result": "error", "error": "Codes are not needed right now."})
    if len(fails) >= 10:
        return await send_license(agent, {"result": "error", "error": "Too many wrong codes. Try again in an hour."})
    until = check_code(agent.id, code)
    if until is None:
        fails.append(now)
        return await send_license(agent, {"result": "error", "error": "This code is not valid for this computer. Check it and try again."})
    if until < today_day():
        return await send_license(agent, {"result": "error", "error": f"This code expired on {day_to_date(until)}."})
    agent.legacy = False
    agent.code_until = max(agent.code_until or -1, until)
    await send_license(agent, {"result": "ok", "code": code.strip().upper()})


# ---------------------------------------------------------------- phone API
@app.get("/api/health")
async def health():
    return {"ok": True, "version": VERSION, "laptops_online": len(AGENTS)}


@app.get("/api/devices/{device_id}")
async def device_status(device_id: str):
    a = AGENTS.get(norm_id(device_id))
    lic = license_state(a) if a else None
    return {"ok": True, "online": bool(a), "name": a.name if a else None,
            "license": {"state": lic["state"], "days_left": lic.get("days_left")} if lic else None}


@app.get("/api/config")
async def config():
    return {"ok": True, "download_url": DOWNLOAD_URL, "version": VERSION}


@app.post("/api/pair")
async def pair(request: Request):
    """Phone Laptop ID + PIN bhejta hai (haath se ya QR scan karke). Laptop PIN check karta hai
    aur (setting ON ho to) 'Allow?' poochta hai."""
    body = await request.json()
    pin = "".join(ch for ch in str(body.get("pin", "")) if ch.isdigit())
    device_id = norm_id(str(body.get("id", "")))
    ip = client_ip(request)
    now = time.time()
    fails = [t for t in PAIR_FAILS.get(ip, []) if now - t < PAIR_FAIL_WINDOW]
    PAIR_FAILS[ip] = fails
    if len(fails) >= PAIR_FAIL_LIMIT:
        raise HTTPException(429, "Too many incorrect PIN attempts. Please try again in 10 minutes.")

    if not device_id:
        raise HTTPException(400, "Invalid Computer ID (e.g. PB-7K3M-9QX2)")
    if not valid_pin(pin):
        raise HTTPException(400, "The PIN must be 6 digits")
    agent = AGENTS.get(device_id)
    if not agent:
        fails.append(now)
        raise HTTPException(404, "No online computer found with this ID. Check the ID and make sure PrintBridge is running on the computer.")

    res = await agent.rpc("pair", {"pin": pin, "phone": str(body.get("phone", "Phone"))[:40]}, timeout=80)
    if not res.get("ok"):
        if res.get("denied"):
            raise HTTPException(403, res.get("error", "The request was declined on the computer"))
        fails.append(now)
        raise HTTPException(403, res.get("error", "Incorrect PIN"))
    return {"ok": True, "id": agent.id, "name": agent.name, "token": res["token"]}


@app.get("/api/devices/{device_id}/printers")
async def printers(device_id: str, x_pb_token: str = Header("")):
    res = await get_agent(device_id).rpc("printers", {"token": x_pb_token})
    if not res.get("ok"):
        raise HTTPException(401 if res.get("auth") is False else 500, res.get("error", "Error"))
    return res


@app.post("/api/jobs")
async def create_job(
    file: UploadFile = File(...),
    device_id: str = Form(...),
    copies: int = Form(1),
    printer: str = Form(""),
    phone: str = Form("Phone"),
    options: str = Form("{}"),
    x_pb_token: str = Header(""),
):
    agent = get_agent(device_id)
    require_license(agent)
    try:
        opts = json.loads(options[:2000]) if options else {}
        opts = opts if isinstance(opts, dict) else {}
    except json.JSONDecodeError:
        opts = {}
    name = os.path.basename(file.filename or "file")
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"'{ext or name}' files cannot be printed")

    job_id = uuid.uuid4().hex
    path = os.path.join(JOB_DIR, job_id + ext)
    size = 0
    with open(path, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_MB * 1024 * 1024:
                out.close()
                os.remove(path)
                raise HTTPException(413, f"File is too large (maximum {MAX_MB} MB)")
            out.write(chunk)

    job = {"id": job_id, "device_id": agent.id, "path": path, "filename": name, "size": size,
           "status": "waiting", "message": "Sending to the computer", "created": time.time(),
           "dl_key": secrets.token_urlsafe(16)}
    JOBS[job_id] = job
    try:
        res = await agent.rpc("job", {
            "job_id": job_id, "filename": name, "size": size,
            "copies": max(1, min(int(copies), 50)), "printer": printer[:200], "phone": phone[:40],
            "token": x_pb_token, "dl_key": job["dl_key"], "opts": opts,
        })
    except HTTPException:
        _drop_job(job_id)
        raise
    if not res.get("ok"):
        _drop_job(job_id)
        raise HTTPException(401 if res.get("auth") is False else 400, res.get("error", "The computer rejected the job"))
    job["status"] = "accepted"
    job["message"] = "The computer is receiving the file"
    return {"ok": True, "job_id": job_id}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, x_pb_token: str = Header("")):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] in ("done", "error", "cancelled"):
        return {"ok": True, "status": job["status"]}
    agent = get_agent(job["device_id"])
    res = await agent.rpc("cancel", {"job_id": job_id, "token": x_pb_token})
    if not res.get("ok"):
        raise HTTPException(401 if res.get("auth") is False else 400, res.get("error", "Could not cancel the job"))
    job["status"], job["message"] = "cancelled", "Print cancelled"
    _drop_file(job)
    return {"ok": True, "status": "cancelled"}


# ---------------------------------------------------------------- scan (laptop ka scanner -> phone)
@app.post("/api/devices/{device_id}/scan")
async def start_scan(device_id: str, request: Request, x_pb_token: str = Header("")):
    agent = get_agent(device_id)
    require_license(agent)
    body = await request.json()
    scan_id = uuid.uuid4().hex
    sc = {"id": scan_id, "device_id": agent.id, "status": "waiting", "message": "Request sent to the computer",
          "path": None, "created": time.time(), "up_key": secrets.token_urlsafe(16),
          "view_key": secrets.token_urlsafe(16)}
    SCANS[scan_id] = sc
    res = await agent.rpc("scan", {
        "scan_id": scan_id, "up_key": sc["up_key"], "token": x_pb_token,
        "color": body.get("color") if body.get("color") in ("color", "gray", "bw") else "color",
        "dpi": int(body.get("dpi") or 200), "phone": str(body.get("phone", "Phone"))[:40]})
    if not res.get("ok"):
        SCANS.pop(scan_id, None)
        raise HTTPException(401 if res.get("auth") is False else 400, res.get("error", "Could not start the scan"))
    return {"ok": True, "scan_id": scan_id, "key": sc["view_key"]}


@app.get("/api/scans/{scan_id}")
async def scan_status(scan_id: str):
    sc = SCANS.get(scan_id)
    if not sc:
        raise HTTPException(404, "Scan not found")
    return {"ok": True, "status": sc["status"], "message": sc["message"]}


@app.get("/api/scans/{scan_id}/file")
async def scan_file(scan_id: str, key: str = ""):
    sc = SCANS.get(scan_id)
    if not sc or not secrets.compare_digest(key, sc["view_key"]) or not sc["path"] or not os.path.exists(sc["path"]):
        raise HTTPException(404, "Scan not found")
    return FileResponse(sc["path"], media_type="image/jpeg", filename="scan.jpg")


@app.post("/api/agent/scans/{scan_id}")
async def scan_upload(scan_id: str, request: Request, x_up_key: str = Header("")):
    sc = SCANS.get(scan_id)
    if not sc or not secrets.compare_digest(x_up_key, sc["up_key"]):
        raise HTTPException(404, "Scan not found")
    path = os.path.join(JOB_DIR, f"scan_{scan_id}.jpg")
    size = 0
    with open(path, "wb") as out:
        async for chunk in request.stream():
            size += len(chunk)
            if size > 40 * 1024 * 1024:
                out.close()
                os.remove(path)
                raise HTTPException(413, "The scan is too large")
            out.write(chunk)
    sc.update(path=path, status="done", message="Scan received")
    return {"ok": True}


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {"ok": True, "status": job["status"], "message": job["message"]}


@app.get("/api/agent/jobs/{job_id}/file")
async def agent_download(job_id: str, x_dl_key: str = Header("")):
    job = JOBS.get(job_id)
    if not job or not secrets.compare_digest(x_dl_key, job["dl_key"]) or not os.path.exists(job["path"]):
        raise HTTPException(404, "File not found")
    job["status"] = "downloading"
    return FileResponse(job["path"], filename=job["filename"])


def _drop_file(job):
    try:
        os.remove(job["path"])
    except OSError:
        pass


def _drop_job(job_id):
    job = JOBS.pop(job_id, None)
    if job:
        _drop_file(job)


async def _cleanup():
    while True:
        cutoff = time.time() - JOB_TTL
        for jid in [j for j, v in JOBS.items() if v["created"] < cutoff]:
            _drop_job(jid)
        for sid in [k for k, v in SCANS.items() if v["created"] < cutoff]:
            sc = SCANS.pop(sid)
            if sc.get("path"):
                try:
                    os.remove(sc["path"])
                except OSError:
                    pass
        await asyncio.sleep(60)


@app.on_event("startup")
async def _startup():
    shutil.rmtree(JOB_DIR, ignore_errors=True)
    os.makedirs(JOB_DIR, exist_ok=True)
    asyncio.create_task(_cleanup())


# ---------------------------------------------------------------- Print shop (customer bina PIN ke)
SHOP_UPLOADS: dict[str, list] = {}  # IP -> upload ka samay (spam rokne ke liye)


def shop_agent(code: str) -> Agent:
    a = AGENTS.get(norm_id(code))
    if not a:
        raise HTTPException(404, "The shop's computer is currently offline. Please try again shortly or ask at the counter.")
    return a


@app.get("/s/{code}")
async def shop_page(code: str):
    return FileResponse(os.path.join(WEB_DIR, "shop.html"), headers={"Cache-Control": "no-cache"})


@app.get("/api/shop/{code}")
async def shop_info(code: str):
    agent = shop_agent(code)
    require_license(agent, shop=True)
    res = await agent.rpc("shop_info", {})
    if not res.get("ok"):
        raise HTTPException(403, res.get("error", "This shop is currently closed"))
    return res


@app.post("/api/shop/{code}/files")
async def shop_file(
    request: Request, code: str,
    file: UploadFile = File(...), order_id: str = Form(...), customer: str = Form(""),
    mobile: str = Form(""), copies: int = Form(1), options: str = Form("{}"),
    note: str = Form(""), private: str = Form(""),
):
    ip, now = client_ip(request), time.time()
    recent = [t for t in SHOP_UPLOADS.get(ip, []) if now - t < 600]
    if len(recent) >= 40:
        raise HTTPException(429, "Too many files sent. Please try again shortly.")
    SHOP_UPLOADS[ip] = recent + [now]
    if not customer.strip():
        raise HTTPException(400, "Please enter your name")
    agent = shop_agent(code)
    require_license(agent, shop=True)
    try:
        opts = json.loads(options[:2000]) if options else {}
        opts = opts if isinstance(opts, dict) else {}
    except json.JSONDecodeError:
        opts = {}
    name = os.path.basename(file.filename or "file")
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"'{ext or name}' files cannot be printed")
    job_id = uuid.uuid4().hex
    path = os.path.join(JOB_DIR, job_id + ext)
    size = 0
    with open(path, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_MB * 1024 * 1024:
                out.close()
                os.remove(path)
                raise HTTPException(413, f"File is too large (maximum {MAX_MB} MB)")
            out.write(chunk)
    job = {"id": job_id, "device_id": agent.id, "path": path, "filename": name, "size": size,
           "status": "waiting", "message": "", "created": time.time(), "dl_key": secrets.token_urlsafe(16)}
    JOBS[job_id] = job
    try:
        res = await agent.rpc("shop_job", {
            "job_id": job_id, "dl_key": job["dl_key"], "filename": name, "size": size, "order_id": order_id[:40],
            "customer": customer.strip()[:40], "mobile": mobile.strip()[:15],
            "copies": max(1, min(int(copies), 50)), "opts": opts,
            "note": note.strip()[:200], "private": private in ("1", "true", "yes")})
    except HTTPException:
        _drop_job(job_id)
        raise
    if not res.get("ok"):
        _drop_job(job_id)
        raise HTTPException(400, res.get("error", "The shop could not accept the file"))
    return {"ok": True, "token": res.get("token")}


@app.get("/api/shop/{code}/orders/{order_id}")
async def shop_order(code: str, order_id: str):
    res = await shop_agent(code).rpc("shop_status", {"order_id": order_id[:40]})
    if not res.get("ok"):
        raise HTTPException(404, res.get("error", "Order not found"))
    return res


@app.post("/api/shop/{code}/orders/{order_id}/cancel")
async def shop_order_cancel(code: str, order_id: str):
    res = await shop_agent(code).rpc("shop_cancel", {"order_id": order_id[:40]})
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "Could not cancel the order"))
    return res


# ---------------------------------------------------------------- admin: subscription code banana
ADMIN_FAILS: dict[str, list] = {}


@app.get("/admin")
async def admin_page():
    return FileResponse(os.path.join(WEB_DIR, "admin.html"), headers={"Cache-Control": "no-store"})


@app.post("/api/admin/code")
async def admin_code(request: Request):
    ip, now = client_ip(request), time.time()
    fails = [t for t in ADMIN_FAILS.get(ip, []) if now - t < 600]
    ADMIN_FAILS[ip] = fails
    if len(fails) >= 5:
        raise HTTPException(429, "Too many wrong admin passwords. Wait 10 minutes.")
    body = await request.json()
    if not ADMIN_KEY or not LICENSING:
        raise HTTPException(503, "Licensing is not set up yet: add PB_LICENSE_SECRET and PB_ADMIN_KEY in Render settings.")
    if not hmac.compare_digest(str(body.get("key", "")), ADMIN_KEY):
        fails.append(now)
        raise HTTPException(403, "Wrong admin password")
    device_id = norm_id(str(body.get("id", "")))
    if not device_id:
        raise HTTPException(400, "Enter a valid Computer ID (e.g. PB-7K3M-9QX2)")
    try:
        months = max(1, min(int(body.get("months", 12)), 60))
    except (TypeError, ValueError):
        months = 12
    t = today_day()
    a = AGENTS.get(device_id)
    cur = license_state(a) if a else None
    start = t
    if cur and cur["state"] in ("pro", "trial") and cur.get("until"):   # bacha hua samay jodo
        start = max(t, (datetime.date.fromisoformat(cur["until"]) - LIC_EPOCH).days + 1)
    until = start + round(months * 365 / 12) - 1
    if body.get("preview"):
        return {"ok": True, "id": device_id, "online": bool(a), "name": a.name if a else None,
                "current": cur, "until": day_to_date(until)}
    return {"ok": True, "id": device_id, "online": bool(a), "name": a.name if a else None, "current": cur,
            "code": make_code(device_id, until), "until": day_to_date(until)}


# ---------------------------------------------------------------- website pages
@app.get("/download")
async def download_page():
    return FileResponse(os.path.join(WEB_DIR, "download.html"))


@app.get("/qr.svg")
async def site_qr(request: Request):
    """Phone app ke link ka QR (download page par dikhta hai)."""
    import qrcode
    import qrcode.image.svg
    base = str(request.base_url).rstrip("/")
    if request.headers.get("x-forwarded-proto") == "https":
        base = base.replace("http://", "https://", 1)
    img = qrcode.make(base + "/?app=1", image_factory=qrcode.image.svg.SvgPathImage, box_size=12, border=2)
    return Response(img.to_string(), media_type="image/svg+xml")


# ---------------------------------------------------------------- phone app (PWA)
SHARED = {}   # token -> {"t": time, "files": [(name, type, bytes)], "text": str}
SHARE_MAX = 60 * 1024 * 1024


@app.post("/share")
async def share_fallback(request: Request):
    # Normally phone ka service worker ise pakad leta hai. Agar wo taiyaar nahi tha,
    # to server file ko 10 minute ke liye rakh leta hai aur app use le leti hai.
    now = time.time()
    for k in [k for k, v in SHARED.items() if now - v["t"] > 600]:
        SHARED.pop(k, None)
    files, text, total = [], [], 0
    try:
        form = await request.form()
        for key, val in form.multi_items():
            if hasattr(val, "read"):
                data = await val.read()
                total += len(data)
                if data and total <= SHARE_MAX:
                    files.append(((val.filename or "file")[:150], val.content_type or "", data))
            elif key in ("title", "text", "url") and str(val).strip():
                text.append(str(val).strip()[:500])
    except Exception:
        return RedirectResponse("/?shared=error", status_code=303)
    tok = secrets.token_urlsafe(18)
    SHARED[tok] = {"t": now, "files": files, "text": " ".join(text)}
    return RedirectResponse(f"/?shared=srv&t={tok}", status_code=303)


@app.get("/api/shared/{tok}")
async def shared_list(tok: str):
    it = SHARED.get(tok)
    if not it:
        raise HTTPException(404, "This shared file has expired")
    return {"files": [{"name": n, "type": t, "size": len(d)} for n, t, d in it["files"]], "text": it["text"]}


@app.get("/api/shared/{tok}/{i}")
async def shared_file(tok: str, i: int):
    it = SHARED.get(tok)
    if not it or not (0 <= i < len(it["files"])):
        raise HTTPException(404, "File not found")
    n, t, d = it["files"][i]
    if i == len(it["files"]) - 1:
        SHARED.pop(tok, None)   # aakhri file le li: server se mita do
    return Response(d, media_type=t or "application/octet-stream")


@app.middleware("http")
async def _headers(request: Request, call_next):
    resp = await call_next(request)
    p = request.url.path
    if p in ("/", "/download") or p.startswith("/api/") or p.endswith((".html", ".js", ".css", ".webmanifest")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
