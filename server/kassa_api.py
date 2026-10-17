#!/usr/bin/env python3
"""API кассы кофейни: принимает продажи и расходы с телефонов, отдаёт день.

Только stdlib. Слушает 127.0.0.1:5054, снаружи — через nginx /api/kassa/.
Доступ — по ключу в заголовке X-Kassa-Key (env KASSA_KEY).
"""
import hmac
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# Пустая переменная = не задана.
KEY = os.environ.get("KASSA_KEY") or ""
DB_PATH = os.environ.get("KASSA_DB") or os.path.join(os.path.dirname(__file__), "..", "data", "kassa.db")
PORT = int(os.environ.get("KASSA_PORT") or "5054")
ALMATY = timezone(timedelta(hours=5))
MAX_BODY = 1_000_000
ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

lock = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    with db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS sales (
              id TEXT PRIMARY KEY, ts INTEGER NOT NULL, day TEXT NOT NULL,
              method TEXT NOT NULL, total INTEGER NOT NULL, cash_in INTEGER,
              items TEXT NOT NULL, voided INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS sales_day ON sales(day);
            CREATE TABLE IF NOT EXISTS expenses (
              id TEXT PRIMARY KEY, ts INTEGER NOT NULL, day TEXT NOT NULL,
              amount INTEGER NOT NULL, note TEXT NOT NULL,
              voided INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS expenses_day ON expenses(day);
            """
        )
        # Смешанная оплата: kaspi_part — сколько из total прошло по Kaspi,
        # остальное наличными. У kaspi/cash чеков — NULL.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(sales)")}
        if "kaspi_part" not in cols:
            c.execute("ALTER TABLE sales ADD COLUMN kaspi_part INTEGER")
        # Правка чека = отмена старого + новый чек с edit_of = id старого.
        if "edit_of" not in cols:
            c.execute("ALTER TABLE sales ADD COLUMN edit_of TEXT")


def day_of(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, ALMATY).strftime("%Y-%m-%d")


def as_int(v, lo, hi):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError("bad int")
    return v


def clean_sale(s):
    if not ID_RE.match(str(s.get("id", ""))):
        raise ValueError("bad id")
    ts = as_int(s.get("ts"), 1_700_000_000_000, 4_000_000_000_000)
    method = s.get("method")
    if method not in ("kaspi", "cash", "mixed"):
        raise ValueError("bad method")
    total = as_int(s.get("total"), 0, 10_000_000)
    cash_in = s.get("cash_in")
    cash_in = as_int(cash_in, 0, 10_000_000) if cash_in is not None else None
    kaspi_part = None
    if method == "mixed":
        # Обе части ненулевые, иначе это обычный kaspi или cash.
        kaspi_part = as_int(s.get("kaspi_part"), 1, max(1, total - 1))
        if kaspi_part >= total:
            raise ValueError("bad kaspi_part")
        if cash_in is not None and cash_in < total - kaspi_part:
            raise ValueError("bad cash_in")
    edit_of = s.get("edit_of")
    if edit_of is not None and not ID_RE.match(str(edit_of)):
        raise ValueError("bad edit_of")
    items = s.get("items")
    if not isinstance(items, list) or not 0 < len(items) <= 200:
        raise ValueError("bad items")
    clean = []
    for it in items:
        name = str(it.get("n", ""))[:80]
        clean.append({"n": name, "p": as_int(it.get("p"), 0, 1_000_000), "q": as_int(it.get("q"), 1, 999)})
    if sum(i["p"] * i["q"] for i in clean) != total:
        raise ValueError("total mismatch")
    return (s["id"], ts, day_of(ts), method, total, cash_in,
            json.dumps(clean, ensure_ascii=False), 1 if s.get("voided") else 0, kaspi_part, edit_of)


def clean_expense(e):
    if not ID_RE.match(str(e.get("id", ""))):
        raise ValueError("bad id")
    ts = as_int(e.get("ts"), 1_700_000_000_000, 4_000_000_000_000)
    amount = as_int(e.get("amount"), 1, 10_000_000)
    note = str(e.get("note", ""))[:120]
    return (e["id"], ts, day_of(ts), amount, note, 1 if e.get("voided") else 0)


class Handler(BaseHTTPRequestHandler):
    server_version = "kassa"

    def log_message(self, fmt, *args):
        pass

    def send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        got = self.headers.get("X-Kassa-Key", "")
        if KEY and hmac.compare_digest(got.encode(), KEY.encode()):
            return True
        self.send(401, {"error": "key"})
        return False

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/kassa/ping":
            return self.send(200, {"ok": True})
        if url.path != "/api/kassa/day":
            return self.send(404, {"error": "not found"})
        if not self.authed():
            return
        d = parse_qs(url.query).get("d", [""])[0]
        if not DAY_RE.match(d):
            return self.send(400, {"error": "day"})
        with db() as c:
            sales = [dict(r) for r in c.execute("SELECT * FROM sales WHERE day=? ORDER BY ts", (d,))]
            exps = [dict(r) for r in c.execute("SELECT * FROM expenses WHERE day=? ORDER BY ts", (d,))]
        for s in sales:
            s["items"] = json.loads(s["items"])
        self.send(200, {"day": d, "sales": sales, "expenses": exps})

    def do_POST(self):
        if urlparse(self.path).path != "/api/kassa/sync":
            return self.send(404, {"error": "not found"})
        if not self.authed():
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not 0 < n <= MAX_BODY:
                raise ValueError("size")
            data = json.loads(self.rfile.read(n))
            sales = [clean_sale(s) for s in data.get("sales", [])[:500]]
            exps = [clean_expense(e) for e in data.get("expenses", [])[:500]]
        except (ValueError, TypeError, AttributeError, json.JSONDecodeError) as e:
            return self.send(400, {"error": str(e)})
        rejected = []
        with lock, db() as c:
            # Записи неизменяемы, кроме отмены: повторная отправка того же чека
            # ничего не дублирует, а voided=1 только ставится, но не снимается.
            for s in sales:
                # Один чек правят дважды с разных телефонов — выигрывает первая
                # правка, вторая ложится отменённой, телефону сообщаем.
                if s[9] is not None and not s[7] and c.execute(
                        "SELECT 1 FROM sales WHERE edit_of=? AND id<>? AND voided=0",
                        (s[9], s[0])).fetchone():
                    s = s[:7] + (1,) + s[8:]
                    rejected.append(s[0])
                c.execute(
                    "INSERT INTO sales (id, ts, day, method, total, cash_in, items, voided, kaspi_part, edit_of) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE "
                    "SET voided=MAX(voided, excluded.voided)", s)
            c.executemany(
                "INSERT INTO expenses VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE "
                "SET voided=MAX(voided, excluded.voided)", exps)
        self.send(200, {"ok": True, "sales": [s[0] for s in sales], "expenses": [e[0] for e in exps],
                        "rejected": rejected})


if __name__ == "__main__":
    if not KEY:
        raise SystemExit("KASSA_KEY не задан")
    init_db()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
