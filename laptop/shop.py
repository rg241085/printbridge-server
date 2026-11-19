"""
PrintBridge - Print shop mode

Customer shop ka QR scan karke files bhejta hai. Files apne aap print NAHI hoti (jab tak shop wala
"apne aap print" ON na kare). Shop wale ki screen par order ki line lagti hai: token, naam, page, rupaye.
Shop wala Print -> (customer ko "taiyaar" dikhta hai) -> "De diya" dabata hai.

Orders aur files laptop par hi rehti hain (DATA_DIR/shop), aur kaam ke baad delete ho jaati hain.
"""
import json
import os
import shutil
import threading
import time
from datetime import datetime

import printer_backend as pb

ACTIVE = ("receiving", "waiting", "printing", "ready")
MAX_FILES_PER_ORDER = 20


class ShopQueue:
    def __init__(self, data_dir, get_cfg, on_change=None):
        self.dir = os.path.join(data_dir, "shop")
        self.files_dir = os.path.join(self.dir, "files")
        os.makedirs(self.files_dir, exist_ok=True)
        self.path = os.path.join(self.dir, "queue.json")
        self.get_cfg = get_cfg            # -> {"name", "bw_rate", "color_rate", "auto_print"}
        self.on_change = on_change or (lambda kind, order: None)
        self.lock = threading.RLock()
        self.orders = {}
        self.day = ""
        self.next_token = 1
        self._load()

    # ------------------------------------------------------------ save / load
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            self.orders, self.day, self.next_token = d.get("orders", {}), d.get("day", ""), d.get("next", 1)
        except Exception:
            pass
        for o in self.orders.values():   # program band hua tha to adhoore kaam
            if o["status"] in ("receiving", "printing"):
                o["status"] = "waiting"

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"orders": self.orders, "day": self.day, "next": self.next_token}, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------ hisaab
    def rate(self, color):
        c = self.get_cfg()
        try:
            return float(c["color_rate"] if color == "color" else c["bw_rate"])
        except (TypeError, ValueError, KeyError):
            return 0.0

    def _recalc(self, o):
        total, sheets = 0.0, 0
        for f in o["files"]:
            if f.get("sheets") is None or f["status"] == "error":
                f["price"] = None
            else:
                f["price"] = round(f["sheets"] * f["copies"] * self.rate(f["color"]), 2)
                total += f["price"]
                sheets += f["sheets"] * f["copies"]
        o["total_known"] = all(f.get("sheets") is not None or f["status"] == "error" for f in o["files"])
        c = self.get_cfg()
        o["bulk_discount"], o["min_applied"] = 0, False
        try:   # thok (bulk) chhoot: N se zyada kaagaz par X% kam
            after, pct = int(c.get("bulk_after") or 0), float(c.get("bulk_pct") or 0)
            if after > 0 and 0 < pct < 100 and sheets > after:
                o["bulk_discount"] = round(total * pct / 100, 2)
                total -= o["bulk_discount"]
        except (TypeError, ValueError):
            pass
        try:   # kam se kam rupaye har order
            mn = float(c.get("min_charge") or 0)
            if mn > 0 and 0 < total < mn and o["total_known"]:
                total, o["min_applied"] = mn, True
        except (TypeError, ValueError):
            pass
        o["calc_total"] = round(total, 2)
        o["total"] = o["amount_override"] if o.get("amount_override") is not None else o["calc_total"]
        if o.get("amount_override") is not None:
            o["total_known"] = True

    def _new_token(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if self.day != today:  # har din token 1 se shuru
            self.day, self.next_token = today, 1
        t = self.next_token
        self.next_token += 1
        return t

    # ------------------------------------------------------------ customer ki taraf se
    def start_file(self, order_id, customer, mobile, filename, copies, opts, note="", private=False):
        """File aane wali hai: order banao (pehli file par token) aur file ki jagah rakho."""
        with self.lock:
            o = self.orders.get(order_id)
            if o is None:
                o = self.orders[order_id] = {
                    "id": order_id, "token": self._new_token(), "customer": customer[:40] or "Customer",
                    "mobile": (mobile or "")[:15], "created": time.time(), "status": "receiving",
                    "files": [], "total": 0, "total_known": True, "message": "",
                    "seq": time.time(), "note": (note or "")[:200], "private": bool(private),
                    "amount_override": None}
                new = True
            else:
                new = False
                if o["status"] not in ("receiving", "waiting"):
                    raise pb.PrintError("Printing of this order has already started, so files can no longer be added. "
                                        "Please start a new order.")
                mx = int(self.get_cfg().get("max_files") or MAX_FILES_PER_ORDER)
                if len(o["files"]) >= mx:
                    raise pb.PrintError(f"An order cannot contain more than {mx} files")
                o["status"] = "receiving"
            opts = pb.clean_opts(opts)
            fid = f"{order_id[:6]}_{len(o['files'])}"
            ext = os.path.splitext(filename)[1].lower()
            f = {"fid": fid, "name": filename[:120], "path": os.path.join(self.files_dir, fid + ext),
                 "copies": max(1, min(int(copies or 1), 50)), "color": opts["color"], "opts": opts,
                 "sheets": None, "price": None, "status": "receiving", "message": ""}
            o["files"].append(f)
            self._save()
        self.on_change("new" if new else "update", o)
        return o, f

    def file_ready(self, order_id, fid, ok=True, error=""):
        """File download ho gayi: page gino aur order ko line mein daalo."""
        with self.lock:
            o = self.orders.get(order_id)
            f = next((x for x in o["files"] if x["fid"] == fid), None) if o else None
            if not f:
                return
        if ok:
            try:
                f["sheets"] = pb.count_sheets(f["path"], f["opts"])
                f["status"] = "waiting"
            except Exception as e:
                f["status"], f["message"] = "error", str(e)[:200]
        else:
            f["status"], f["message"] = "error", error[:200]
        with self.lock:
            if all(x["status"] != "receiving" for x in o["files"]) and o["status"] == "receiving":
                o["status"] = "waiting"
            self._recalc(o)
            self._save()
        self.on_change("update", o)
        c = self.get_cfg()
        mode = c.get("auto_mode") or ("all" if c.get("auto_print") else "off")
        if o["status"] == "waiting" and (mode == "all" or (mode == "bw" and all(f["color"] != "color" for f in o["files"]))):
            self.on_change("auto_print", o)

    def status(self, order_id):
        with self.lock:
            o = self.orders.get(order_id)
            if not o:
                return None
            ahead = sum(1 for x in self.orders.values()
                        if x["status"] in ("waiting", "receiving", "printing") and self._key(x) < self._key(o))
            return {"token": o["token"], "status": o["status"], "ahead": ahead, "customer": o["customer"],
                    "total": o["total"], "total_known": o["total_known"], "message": o["message"],
                    "private": o.get("private", False), "discount": o.get("amount_override") is not None,
                    "bulk_discount": o.get("bulk_discount", 0), "min_applied": o.get("min_applied", False),
                    "files": [{"name": f["name"], "sheets": f["sheets"], "copies": f["copies"], "color": f["color"],
                               "price": f["price"], "status": f["status"], "message": f["message"]} for f in o["files"]]}

    def cancel(self, order_id):
        with self.lock:
            o = self.orders.get(order_id)
            if not o:
                return False, "Order not found"
            if o["status"] not in ("receiving", "waiting"):
                return False, "This order can no longer be cancelled because printing has started. Please ask at the counter."
            o["status"], o["message"] = "cancelled", "Cancelled by customer"
            self._drop_files(o)
            self._save()
        self.on_change("update", o)
        return True, ""

    # ------------------------------------------------------------ shop wale ki taraf se
    def list(self):
        with self.lock:
            return sorted((o for o in self.orders.values() if o["status"] in ACTIVE), key=self._key)

    @staticmethod
    def _key(o):
        return o.get("seq", o["created"])

    def move(self, order_id, how):
        """Line mein kram badlo: how = 'up' / 'down' / 'top'."""
        with self.lock:
            lst = [o for o in self.list() if o["status"] in ("receiving", "waiting")]
            idx = next((i for i, o in enumerate(lst) if o["id"] == order_id), None)
            if idx is None:
                return False
            o = lst[idx]
            if how == "top" and idx > 0:
                o["seq"] = self._key(lst[0]) - 1
            elif how == "up" and idx > 0:
                o["seq"], lst[idx - 1]["seq"] = self._key(lst[idx - 1]), self._key(o)
            elif how == "down" and idx < len(lst) - 1:
                o["seq"], lst[idx + 1]["seq"] = self._key(lst[idx + 1]), self._key(o)
            else:
                return False
            self._save()
        self.on_change("update", o)
        return True

    def set_amount(self, order_id, amount):
        """Shop wala rupaye badle (discount / round-off). None = apne aap hisaab."""
        with self.lock:
            o = self.orders.get(order_id)
            if not o:
                return
            o["amount_override"] = None if amount is None else round(float(amount), 2)
            self._recalc(o)
            self._save()
        self.on_change("update", o)

    def get_open(self, order_id):
        """Chalu (abhi tak na chhapa) order - samay khatam hone ke baad bhi usme file jod sakte hain."""
        o = self.orders.get(order_id) if isinstance(order_id, str) else None
        return o if o and o["status"] in ("receiving", "waiting") else None

    def get(self, order_id):
        return self.orders.get(order_id)

    def set_status(self, order_id, status, message=""):
        with self.lock:
            o = self.orders.get(order_id)
            if not o:
                return
            o["status"], o["message"] = status, message
            if status in ("done", "rejected", "cancelled"):
                self._drop_files(o)
            self._save()
        self.on_change("update", o)

    def print_order(self, order_id, printer, print_fn):
        """print_fn(path, printer, copies, opts, stats) -> message. Har file chhaapo."""
        o = self.orders.get(order_id)
        if not o or o["status"] not in ("waiting", "ready"):
            return False, "This order is not ready to print yet"
        self.set_status(order_id, "printing")
        errors = []
        for f in o["files"]:
            if f["status"] == "error" or not os.path.exists(f["path"]):
                continue
            stats = {}
            try:
                print_fn(f["path"], printer, f["copies"], f["opts"], stats, f, o)
                if stats.get("sheets") is not None:
                    f["sheets"] = stats["sheets"]
                f["status"] = "printed"
            except Exception as e:
                f["status"], f["message"] = "error", str(e)[:200]
                errors.append(f"{f['name']}: {e}")
        with self.lock:
            self._recalc(o)
            self._save()
        if errors and all(f["status"] == "error" for f in o["files"]):
            self.set_status(order_id, "waiting", "Printing failed: " + "; ".join(errors)[:200])
            return False, "; ".join(errors)
        self.set_status(order_id, "ready", "Printed. Please collect it at the counter.")
        return True, "; ".join(errors)

    def _drop_files(self, o):
        for f in o["files"]:
            try:
                os.remove(f["path"])
            except OSError:
                pass

    def cleanup(self):
        """Purane orders hatao: khatam hue 2 ghante baad, taiyaar/ruke hue 24 ghante baad."""
        now = time.time()
        with self.lock:
            for oid, o in list(self.orders.items()):
                age = now - o["created"]
                if (o["status"] not in ACTIVE and age > 2 * 3600) or age > 24 * 3600:
                    self._drop_files(o)
                    del self.orders[oid]
            self._save()
        # bina order wali bachi files
        keep = {f["path"] for o in self.orders.values() for f in o["files"]}
        for n in os.listdir(self.files_dir):
            p = os.path.join(self.files_dir, n)
            if p not in keep and now - os.path.getmtime(p) > 3600:
                try:
                    os.remove(p)
                except OSError:
                    pass

    def reset_all(self):
        with self.lock:
            for o in self.orders.values():
                self._drop_files(o)
            self.orders = {}
            self._save()
        shutil.rmtree(self.files_dir, ignore_errors=True)
        os.makedirs(self.files_dir, exist_ok=True)
