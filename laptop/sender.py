"""PrintBridge - is laptop se KISI DOOSRE laptop ke printer par print bhejna.

Ye bilkul phone app jaisa kaam karta hai: doosre laptop ka ID + PIN daal kar ek baar jodo,
phir file server ke raaste us laptop ko jaati hai aur wahan ke printer se chhapti hai.
Sirf Python ki apni library (urllib) use hoti hai.
"""
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ID_RE = re.compile(r"^PB-[A-Z0-9]{4}-[A-Z0-9]{4}$")


class SendError(Exception):
    pass


def norm_id(text):
    t = re.sub(r"[^A-Z0-9]", "", str(text).upper())
    if t.startswith("PB"):
        t = t[2:]
    if len(t) != 8:
        return ""
    return f"PB-{t[:4]}-{t[4:]}"


def _call(base, path, method="GET", data=None, headers=None, timeout=60):
    h = {"User-Agent": "PrintBridge-Laptop"}
    h.update(headers or {})
    req = urllib.request.Request(base.rstrip("/") + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        msg = None
        try:
            d = json.loads(e.read() or b"{}")
            msg = d.get("detail") or d.get("error")
        except Exception:
            pass
        err = SendError(msg or f"The server rejected the request ({e.code})")
        err.code = e.code
        raise err
    except (urllib.error.URLError, TimeoutError, OSError):
        raise SendError("Cannot reach the server. Check your internet connection.")


def device(base, dev_id):
    """{'online': bool, 'name': str}"""
    return _call(base, "/api/devices/" + urllib.parse.quote(dev_id), timeout=20)


def pair(base, dev_id, pin, my_name):
    """Doosre laptop se judo. Wahan 'Allow?' aa sakta hai, isliye 90 sec tak intezaar."""
    body = json.dumps({"id": dev_id, "pin": re.sub(r"\D", "", str(pin)), "phone": my_name[:40]}).encode()
    res = _call(base, "/api/pair", "POST", body, {"Content-Type": "application/json"}, timeout=95)
    if not res.get("token"):
        raise SendError("Could not connect")
    return {"id": res.get("id", dev_id), "name": res.get("name") or dev_id, "token": res["token"], "added": time.time()}


def printers(base, target):
    """(printer naam list, default, status dict)"""
    res = _call(base, f"/api/devices/{urllib.parse.quote(target['id'])}/printers",
                headers={"X-PB-Token": target["token"]}, timeout=40)
    return res.get("printers") or [], res.get("default"), res.get("status") or {}


def send_file(base, target, path, copies, printer, opts, my_name):
    """File bhejo. Lautata hai job_id."""
    boundary = "----pb" + uuid.uuid4().hex
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    fields = {"device_id": target["id"], "copies": str(max(1, min(int(copies), 50))), "printer": printer or "",
              "phone": my_name[:40], "options": json.dumps(opts or {})}
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    with open(path, "rb") as f:
        data = f.read()
    safe = name.replace('"', "'")
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{safe}"\r\n'
                 f"Content-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    res = _call(base, "/api/jobs", "POST", b"".join(parts),
                {"Content-Type": f"multipart/form-data; boundary={boundary}", "X-PB-Token": target["token"]},
                timeout=300)
    return res["job_id"]


def job_status(base, job_id):
    return _call(base, "/api/jobs/" + urllib.parse.quote(job_id), timeout=20)


def wait_job(base, job_id, on_update=None, timeout=600):
    """Job khatam hone tak ruko. Lautata hai (status, message)."""
    end, last = time.time() + timeout, None
    while time.time() < end:
        try:
            st = job_status(base, job_id)
        except SendError as e:
            if getattr(e, "code", 0) == 404:
                return "done", "Printed"   # server ne job saaf kar diya = ho chuka
            time.sleep(2)
            continue
        cur = (st.get("status"), st.get("message"))
        if cur != last and on_update:
            on_update(*cur)
        last = cur
        if cur[0] in ("done", "error", "cancelled"):
            return cur
        time.sleep(1.5)
    return "error", "Timed out. Please check the other computer."
