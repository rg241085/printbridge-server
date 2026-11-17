"""
PrintBridge - print ka hisaab (Office mode / Shop ki report)

Har print ki ek line usage.csv mein likhi jaati hai:
  samay, kahan se (phone/shop/local), uid, naam, file, kaagaz (1 copy), copies, color/bw, printer, status, rupaye
Isi se report banti hai (kisne kitna chhapa) aur roz ki page-limit check hoti hai.
"""
import csv
import os
import threading
from datetime import datetime, timedelta

FIELDS = ["time", "source", "uid", "name", "file", "sheets", "copies", "color", "printer", "status", "amount"]
_lock = threading.Lock()
_path = None


def init(data_dir):
    global _path
    _path = os.path.join(data_dir, "usage.csv")


def log(source, uid, name, file, sheets, copies, color, printer, status="done", amount=""):
    if not _path:
        return
    row = {"time": datetime.now().isoformat(timespec="seconds"), "source": source, "uid": uid, "name": name,
           "file": file, "sheets": "" if sheets is None else int(sheets), "copies": int(copies or 1),
           "color": color, "printer": printer, "status": status, "amount": amount}
    with _lock:
        new = not os.path.exists(_path)
        with open(_path, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)


def rows(since=None):
    if not _path or not os.path.exists(_path):
        return []
    with _lock, open(_path, newline="", encoding="utf-8-sig") as f:
        out = list(csv.DictReader(f))
    if since:
        s = since.isoformat(timespec="seconds")
        out = [r for r in out if r.get("time", "") >= s]
    return out


def period_start(period):
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {"today": today, "week": today - timedelta(days=today.weekday()),
            "month": today.replace(day=1), "all": None}.get(period, today)


def pages(r):
    try:
        return int(r["sheets"]) * int(r["copies"] or 1)
    except (ValueError, TypeError):
        return None


def summary(period="today"):
    """Har insaan (uid) ka hisaab: prints, kul kaagaz, color, B&W, rupaye."""
    by = {}
    for r in rows(period_start(period)):
        if r.get("status") != "done":
            continue
        k = r.get("uid") or r.get("name")
        d = by.setdefault(k, {"name": r.get("name", ""), "source": r.get("source", ""), "prints": 0, "pages": 0,
                              "color": 0, "bw": 0, "unknown": 0, "amount": 0.0})
        d["name"] = r.get("name") or d["name"]
        if not r.get("file", "").startswith("("):  # "(rupaye badle)" jaisi line print nahi hai
            d["prints"] += 1
        n = pages(r)
        if n is None:
            if not r.get("file", "").startswith("("):
                d["unknown"] += 1
        else:
            d["pages"] += n
            d["color" if r.get("color") == "color" else "bw"] += n
        try:
            d["amount"] += float(r.get("amount") or 0)
        except ValueError:
            pass
    return sorted(by.values(), key=lambda d: -d["pages"])


def today_pages(uid):
    total = 0
    for r in rows(period_start("today")):
        if r.get("uid") == uid and r.get("status") == "done":
            total += pages(r) or 0
    return total


def export(path, period="month"):
    data = rows(period_start(period))
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: Excel mein Hindi/₹ sahi dikhe
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(data)
    return len(data)
