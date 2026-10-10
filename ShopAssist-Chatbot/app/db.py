"""Data layer. SQLite for the skeleton -> SWAP to Postgres in production.

Orders are written transactionally and carry an idempotency key so a retried
tool call can never double-charge a customer.
"""
import hashlib
import json
import sqlite3
import threading

from app.config import DATABASE_PATH

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


SEED_PRODUCTS = [
    ("P-1001", "Royal Basmati Rice 5kg", "Premium aged basmati rice, great for biryani.", 8.50, 12, "Rice"),
    ("P-1002", "Long Grain Rice 2kg", "Everyday long grain rice.", 3.20, 45, "Rice"),
    ("P-1003", "Jasmine Rice 5kg", "Fragrant Thai jasmine rice.", 7.90, 0, "Rice"),
    ("P-2001", "Whole Milk 1L", "Full cream pasteurized milk.", 1.15, 60, "Dairy"),
    ("P-3001", "Free-range Eggs (12)", "Dozen large free-range eggs.", 2.80, 30, "Dairy"),
    ("P-4001", "Palm Oil 2L", "100% pure palm oil.", 5.40, 20, "Oil"),
]


def init_db() -> None:
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS products (
        product_id  TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        description TEXT,
        unit_price  REAL NOT NULL,
        stock_qty   INTEGER NOT NULL DEFAULT 0,
        category    TEXT
    );
    CREATE TABLE IF NOT EXISTS orders (
        order_id        TEXT PRIMARY KEY,
        idempotency_key TEXT UNIQUE,
        customer_phone  TEXT NOT NULL,
        items_json      TEXT NOT NULL,
        subtotal        REAL, tax REAL, delivery REAL, total REAL,
        status          TEXT NOT NULL DEFAULT 'PLACED',
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """)
    if conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"] == 0:
        conn.executemany(
            "INSERT INTO products VALUES (?,?,?,?,?,?)", SEED_PRODUCTS
        )
        conn.commit()


# ---------- product reads ----------

def search_products(keyword: str) -> list[sqlite3.Row]:
    like = f"%{keyword.strip()}%"
    return get_conn().execute(
        """SELECT * FROM products
           WHERE name LIKE ? OR category LIKE ? OR description LIKE ?
           ORDER BY name LIMIT 10""",
        (like, like, like),
    ).fetchall()


def get_product(product_id: str) -> sqlite3.Row | None:
    return get_conn().execute(
        "SELECT * FROM products WHERE product_id = ?", (product_id.strip().upper(),)
    ).fetchone()


# ---------- pricing (deterministic — NEVER LLM math) ----------

def compute_totals(items: list[dict]) -> dict:
    """items: [{"product": Row, "quantity": int}, ...]"""
    from app.config import TAX_RATE, DELIVERY_FEE
    subtotal = sum(r["unit_price"] * q["quantity"] for r, q in
                   [(i["product"], i) for i in items])
    subtotal = round(subtotal, 2)
    tax = round(subtotal * TAX_RATE, 2)
    delivery = round(DELIVERY_FEE, 2)
    return {"subtotal": subtotal, "tax": tax, "delivery": delivery,
            "total": round(subtotal + tax + delivery, 2)}


# ---------- order write (transactional + idempotent) ----------

def find_order_by_key(idempotency_key: str):
    return get_conn().execute(
        "SELECT * FROM orders WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()


def create_order_db(idempotency_key: str, phone: str,
                    items: list[dict], totals: dict) -> str:
    conn = get_conn()
    with conn:  # transaction
        existing = find_order_by_key(idempotency_key)
        if existing:
            return existing["order_id"]  # retry of an already-placed order
        for i in items:
            cur = conn.execute(
                "UPDATE products SET stock_qty = stock_qty - ? "
                "WHERE product_id = ? AND stock_qty >= ?",
                (i["quantity"], i["product"]["product_id"], i["quantity"]),
            )
            if cur.rowcount == 0:
                raise ValueError(
                    f"Insufficient stock for {i['product']['name']}"
                )
        order_id = "ORD-" + hashlib.sha1(
            idempotency_key.encode()).hexdigest()[:8].upper()
        conn.execute(
            "INSERT INTO orders (order_id, idempotency_key, customer_phone,"
            " items_json, subtotal, tax, delivery, total) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (order_id, idempotency_key, phone,
             json.dumps([{"product_id": i["product"]["product_id"],
                          "name": i["product"]["name"],
                          "quantity": i["quantity"],
                          "unit_price": i["product"]["unit_price"]}
                         for i in items]),
             totals["subtotal"], totals["tax"], totals["delivery"],
             totals["total"]),
        )
    return order_id


def orders_for_phone(phone: str) -> list[sqlite3.Row]:
    return get_conn().execute(
        "SELECT * FROM orders WHERE customer_phone = ? ORDER BY created_at DESC LIMIT 5",
        (phone,),
    ).fetchall()
