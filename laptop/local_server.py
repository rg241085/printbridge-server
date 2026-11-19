"""
PrintBridge - bina internet print (same WiFi)

Laptop par ek chhota server (port 8765) chalta hai. Internet band ho to phone isi WiFi par
seedha laptop se baat karta hai. Phone laptop window ke "Bina internet" QR ko scan karta hai.
QR mein ek alag local key hoti hai, isliye bina QR scan kiye koi print nahi bhej sakta.

Sirf Python ki apni library (http.server) use hoti hai, koi extra package nahi.
"""
import json
import os
import secrets
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from local_page import PAGE

PORT = 8765
MAX_MB = 50


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


class LocalServer:
    """ctx: dict with callables
         key()            -> local key (string)
         name()           -> laptop ka naam
         printers()       -> (names, default, status, caps)
         print_file(path, printer, copies, opts, phone, filename) -> message (ya exception)
         jobs_dir         -> temp folder
    """

    def __init__(self, ctx):
        self.ctx = ctx
        self.httpd = None
        self.error = None

    def url(self):
        return f"http://{lan_ip()}:{PORT}/#k={self.ctx['key']()}"

    def start(self):
        ctx = self.ctx

        class H(BaseHTTPRequestHandler):
            server_version = "PrintBridgeLocal"

            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _auth(self):
                if secrets.compare_digest(self.headers.get("X-PB-Key", ""), ctx["key"]()):
                    return True
                self._send(401, {"ok": False, "error": "This device is not recognised. Scan the 'Offline QR' code on the computer again."})
                return False

            def do_GET(self):
                path = self.path.split("?")[0]
                if path in ("/", "/index.html"):
                    return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
                if path == "/api/info":
                    return self._send(200, {"ok": True, "name": ctx["name"]()})
                if path == "/api/printers":
                    if not self._auth():
                        return
                    try:
                        names, default, status, _ = ctx["printers"]()
                        return self._send(200, {"ok": True, "printers": names, "default": default, "status": status})
                    except Exception as e:
                        return self._send(500, {"ok": False, "error": str(e)})
                self._send(404, {"ok": False, "error": "Not found"})

            def do_POST(self):
                if self.path.split("?")[0] != "/api/print":
                    return self._send(404, {"ok": False, "error": "Not found"})
                if not self._auth():
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    size = 0
                if size <= 0 or size > MAX_MB * 1024 * 1024:
                    return self._send(413, {"ok": False, "error": f"File is empty or too large (maximum {MAX_MB} MB)"})
                name = os.path.basename(unquote(self.headers.get("X-Filename", "file")))[:120] or "file"
                ext = os.path.splitext(name)[1].lower()
                path = os.path.join(ctx["jobs_dir"], f"local_{uuid.uuid4().hex[:8]}{ext}")
                left = size
                with open(path, "wb") as f:
                    while left > 0:
                        chunk = self.rfile.read(min(1 << 20, left))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
                try:
                    opts = json.loads(unquote(self.headers.get("X-Options", "%7B%7D")))
                except Exception:
                    opts = {}
                try:
                    copies = int(self.headers.get("X-Copies", "1"))
                except ValueError:
                    copies = 1
                try:
                    msg = ctx["print_file"](path, unquote(self.headers.get("X-Printer", "")), copies, opts,
                                            unquote(self.headers.get("X-Phone", "Phone"))[:40], name)
                    self._send(200, {"ok": True, "message": msg})
                except Exception as e:
                    self._send(400, {"ok": False, "error": str(e)[:300]})

        try:
            self.httpd = ThreadingHTTPServer(("0.0.0.0", PORT), H)
        except OSError as e:
            self.error = f"Port {PORT} is in use: {e}"
            return False
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return True


def wait_ready(timeout=3):
    t = time.time()
    while time.time() - t < timeout:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False
