"""Customer records kept in SQLite."""

import sqlite3

CUSTOMERS = [
    (1, "alice", "alice@example.com", "gold"),
    (2, "bob", "bob@example.com", "standard"),
    (3, "carol", "carol@example.com", "standard"),
]


def connect(path=":memory:"):
    """Open the database and make sure the customers table exists with the sample rows."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS customers (id INTEGER PRIMARY KEY, username TEXT, email TEXT, tier TEXT)")
    if not conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]:
        conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?)", CUSTOMERS)
    return conn


def find_customer(conn, username):
    """Rows for the customer with this username (empty list if there is none)."""
    sql = f"SELECT id, username, email, tier FROM customers WHERE username = '{username}'"
    return conn.execute(sql).fetchall()
