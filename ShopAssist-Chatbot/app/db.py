"""Data layer (Stage 1): branch-aware catalogue + safe order writes.

SQLite for now -> SWAP to Postgres in production (same functions, new driver).

Rules this file enforces for the whole app:
  * MONEY is stored and computed as integer KOBO. No floats touch money.
  * A product has ONE global record (products) but a PER-BRANCH price and
    stock (branch_inventory). Every read/write below takes a branch_id, so a
    caller cannot accidentally see or sell another branch's shelf.
  * Order creation is one atomic transaction: stock decrement + order row
    succeed together or not at all.
"""
import hashlib
import json
import re
import secrets
import sqlite3
import threading

from app.config import (DATABASE_PATH, DELIVERY_FEE_KOBO, RESERVATION_HOURS,
                        TAX_RATE)

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")   # enforce REFERENCES (off by default!)
        conn.execute("PRAGMA journal_mode = WAL")  # readers don't block the writer
        _local.conn = conn
    return conn


# ---------- demo seed data (REPLACE with your real branches/products) ----------

SEED_BRANCHES = [
    # (branch_id, name, city, address)
    ("BR-LKI", "Lekki Branch", "Lagos", "Demo address, Lekki Phase 1"),
    ("BR-IKJ", "Ikeja Branch", "Lagos", "Demo address, Allen Avenue, Ikeja"),
    ("BR-WUS", "Wuse Branch", "Abuja", "Demo address, Wuse Zone 4"),
]

SEED_PRODUCTS = [
    # (product_id, name, description, category)
    ("P-1001", "Royal Basmati Rice 5kg", "Premium aged basmati rice.", "Rice"),
    ("P-1002", "Long Grain Rice 2kg", "Everyday long grain rice.", "Rice"),
    ("P-1003", "Jasmine Rice 5kg", "Fragrant jasmine rice.", "Rice"),
    ("P-2001", "Whole Milk 1L", "Full cream pasteurized milk.", "Dairy"),
    ("P-3001", "Free-range Eggs (12)", "Dozen large free-range eggs.", "Dairy"),
    ("P-4001", "Palm Oil 2L", "100% pure palm oil.", "Oil"),
]

SEED_INVENTORY = [
    # (branch_id, product_id, price_kobo, stock_qty)   100 kobo = 1 naira
    ("BR-LKI", "P-1001", 1450000, 12), ("BR-LKI", "P-1002", 480000, 45),
    ("BR-LKI", "P-1003", 1390000, 5),  ("BR-LKI", "P-2001", 220000, 60),
    ("BR-LKI", "P-3001", 360000, 30),  ("BR-LKI", "P-4001", 650000, 20),
    ("BR-IKJ", "P-1001", 1420000, 8),  ("BR-IKJ", "P-1002", 470000, 40),
    ("BR-IKJ", "P-2001", 215000, 50),  ("BR-IKJ", "P-4001", 640000, 15),
    ("BR-WUS", "P-1001", 1520000, 6),  ("BR-WUS", "P-1002", 500000, 25),
    ("BR-WUS", "P-1003", 1450000, 0),  ("BR-WUS", "P-2001", 230000, 35),
    ("BR-WUS", "P-4001", 690000, 10),
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS branches (
    branch_id TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    city      TEXT NOT NULL,
    address   TEXT NOT NULL,
    active    INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS products (
    product_id  TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    category    TEXT
);
CREATE TABLE IF NOT EXISTS branch_inventory (
    branch_id  TEXT NOT NULL REFERENCES branches(branch_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    price_kobo INTEGER NOT NULL CHECK (price_kobo >= 0),
    stock_qty  INTEGER NOT NULL DEFAULT 0 CHECK (stock_qty >= 0),
    PRIMARY KEY (branch_id, product_id)
);
CREATE TABLE IF NOT EXISTS orders (
    order_id        TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    branch_id       TEXT NOT NULL REFERENCES branches(branch_id),
    customer_phone  TEXT NOT NULL,
    items_json      TEXT NOT NULL,
    subtotal_kobo   INTEGER NOT NULL,
    tax_kobo        INTEGER NOT NULL,
    delivery_kobo   INTEGER NOT NULL,
    total_kobo      INTEGER NOT NULL,
    pickup_code     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'RESERVED'
        CHECK (status IN ('RESERVED','READY','COLLECTED','CANCELLED','EXPIRED')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_branch_code
    ON orders(branch_id, pickup_code);
CREATE INDEX IF NOT EXISTS ix_orders_phone ON orders(customer_phone, created_at);
"""


def init_db() -> None:
    conn = get_conn()
    conn.executescript(_SCHEMA)
    if conn.execute("SELECT COUNT(*) AS c FROM branches").fetchone()["c"] == 0:
        with conn:  # one transaction: all seed tables or none
            conn.executemany(
                "INSERT INTO branches (branch_id, name, city, address) "
                "VALUES (?,?,?,?)", SEED_BRANCHES)
            conn.executemany("INSERT INTO products VALUES (?,?,?,?)", SEED_PRODUCTS)
            conn.executemany("INSERT INTO branch_inventory VALUES (?,?,?,?)",
                             SEED_INVENTORY)


# ---------- branch reads ----------

def find_branches(place: str) -> list[sqlite3.Row]:
    """Active branches whose city, name or address contains `place`
    (case-insensitive). 'Lagos', 'Lekki' and 'Wuse' all work."""
    like = f"%{place.strip().lower()}%"
    return get_conn().execute(
        """SELECT * FROM branches
           WHERE active = 1 AND (LOWER(city) LIKE ? OR LOWER(name) LIKE ?
                                 OR LOWER(address) LIKE ?)
           ORDER BY name""",
        (like, like, like),
    ).fetchall()


def served_cities() -> list[str]:
    rows = get_conn().execute(
        "SELECT DISTINCT city FROM branches WHERE active = 1 ORDER BY city"
    ).fetchall()
    return [r["city"] for r in rows]


def get_branch(branch_id: str) -> sqlite3.Row | None:
    return get_conn().execute(
        "SELECT * FROM branches WHERE branch_id = ?", (branch_id,)
    ).fetchone()


# ---------- product reads (ALWAYS scoped to one branch) ----------

def search_products(branch_id: str, keyword: str, limit: int = 10) -> list[sqlite3.Row]:
    """Every word must match somewhere (name/category/description), in any
    order, so 'rice 5kg basmati' finds 'Royal Basmati Rice 5kg'."""
    words = re.findall(r"\w+", keyword.lower())
    if not words:
        return []
    per_word = ("(LOWER(p.name) LIKE ? OR LOWER(p.category) LIKE ? "
                "OR LOWER(p.description) LIKE ?)")
    params: list = [branch_id]
    for w in words:
        params += [f"%{w}%"] * 3
    params.append(limit)
    return get_conn().execute(
        f"""SELECT p.product_id, p.name, p.description, p.category,
                   i.price_kobo, i.stock_qty
            FROM branch_inventory i JOIN products p ON p.product_id = i.product_id
            WHERE i.branch_id = ? AND {" AND ".join([per_word] * len(words))}
            ORDER BY p.name LIMIT ?""",
        params,
    ).fetchall()


def get_item(branch_id: str, product_id: str) -> sqlite3.Row | None:
    """One product as sold at one branch, or None if that branch doesn't stock it."""
    return get_conn().execute(
        """SELECT p.product_id, p.name, p.description, p.category,
                  i.price_kobo, i.stock_qty
           FROM branch_inventory i JOIN products p ON p.product_id = i.product_id
           WHERE i.branch_id = ? AND i.product_id = ?""",
        (branch_id, product_id.strip().upper()),
    ).fetchone()


# ---------- pricing (deterministic, integer kobo - NEVER LLM math) ----------

def compute_totals(items: list[dict]) -> dict:
    """items: [{"item": Row from get_item, "quantity": int}, ...]"""
    subtotal = sum(i["item"]["price_kobo"] * i["quantity"] for i in items)
    tax = round(subtotal * TAX_RATE)          # round() on a float returns an int
    delivery = DELIVERY_FEE_KOBO
    return {"subtotal": subtotal, "tax": tax, "delivery": delivery,
            "total": subtotal + tax + delivery}


# ---------- order write (atomic + idempotent) ----------

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I: read aloud at a counter


def _new_pickup_code(conn: sqlite3.Connection, branch_id: str) -> str:
    while True:   # collision is ~1 in a billion; the loop makes it impossible
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        if not conn.execute(
                "SELECT 1 FROM orders WHERE branch_id = ? AND pickup_code = ?",
                (branch_id, code)).fetchone():
            return code


def find_order_by_key(idempotency_key: str) -> sqlite3.Row | None:
    return get_conn().execute(
        "SELECT * FROM orders WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()


def create_order_db(idempotency_key: str, branch_id: str, phone: str,
                    items: list[dict], totals: dict) -> sqlite3.Row:
    """Reserve stock at `branch_id` and write the order, atomically.
    Same idempotency_key twice -> returns the first order, changes nothing.
    Raises ValueError if any line lacks stock (nothing is written)."""
    conn = get_conn()
    conn.execute("BEGIN IMMEDIATE")   # take the write lock BEFORE checking anything
    try:
        existing = find_order_by_key(idempotency_key)
        if existing:
            conn.rollback()
            return existing
        for i in items:
            cur = conn.execute(
                "UPDATE branch_inventory SET stock_qty = stock_qty - ? "
                "WHERE branch_id = ? AND product_id = ? AND stock_qty >= ?",
                (i["quantity"], branch_id, i["item"]["product_id"], i["quantity"]),
            )
            if cur.rowcount == 0:
                raise ValueError(f"Not enough stock for {i['item']['name']}")
        order_id = "ORD-" + hashlib.sha1(
            idempotency_key.encode()).hexdigest()[:8].upper()
        conn.execute(
            """INSERT INTO orders (order_id, idempotency_key, branch_id,
                   customer_phone, items_json, subtotal_kobo, tax_kobo,
                   delivery_kobo, total_kobo, pickup_code, expires_at)
               VALUES (?,?,?,?,?,?,?,?,?,?, datetime('now', ?))""",
            (order_id, idempotency_key, branch_id, phone,
             json.dumps([{"product_id": i["item"]["product_id"],
                          "name": i["item"]["name"],
                          "quantity": i["quantity"],
                          "unit_price_kobo": i["item"]["price_kobo"]}
                         for i in items]),
             totals["subtotal"], totals["tax"], totals["delivery"],
             totals["total"], _new_pickup_code(conn, branch_id),
             f"+{RESERVATION_HOURS} hours"),
        )
        conn.commit()
    except Exception:
        conn.rollback()   # stock decrements above are undone too
        raise
    return find_order_by_key(idempotency_key)


# ---------- order reads ----------

_ORDER_SELECT = """SELECT o.*, b.name AS branch_name FROM orders o
                   JOIN branches b ON b.branch_id = o.branch_id"""


def orders_for_phone(phone: str, limit: int = 5) -> list[sqlite3.Row]:
    return get_conn().execute(
        _ORDER_SELECT + " WHERE o.customer_phone = ? "
        "ORDER BY o.created_at DESC, o.rowid DESC LIMIT ?", (phone, limit),
    ).fetchall()


def get_order_for_phone(order_id: str, phone: str) -> sqlite3.Row | None:
    """Phone is part of the lookup, so nobody can read someone else's order."""
    return get_conn().execute(
        _ORDER_SELECT + " WHERE o.order_id = ? AND o.customer_phone = ?",
        (order_id.strip().upper(), phone),
    ).fetchone()
