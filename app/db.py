"""SQLite 数据访问层：建表、默认设置、演示数据（仅空库时写入）。"""
import os
import sqlite3
from datetime import datetime, timezone, timedelta

DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "app.db")),
)

CST = timezone(timedelta(hours=8))


def now_str() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def rows(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def row(conn: sqlite3.Connection, sql: str, params=()) -> dict | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    wheelchair_width REAL NOT NULL DEFAULT 60,
    max_slope REAL NOT NULL DEFAULT 8
);
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    floor TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'room'
);
CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    to_node INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'corridor',
    door_width REAL,
    slope REAL,
    bidirectional INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node INTEGER NOT NULL,
    to_node INTEGER NOT NULL,
    wheelchair_width REAL NOT NULL,
    max_slope REAL NOT NULL,
    feasible INTEGER NOT NULL,
    floors TEXT NOT NULL DEFAULT '[]',
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO settings (id, wheelchair_width, max_slope) VALUES (1, 60, 8)"
        )
        conn.commit()
        if row(conn, "SELECT COUNT(*) AS c FROM nodes")["c"] == 0:
            _seed_demo(conn)
    finally:
        conn.close()


def _seed_demo(conn: sqlite3.Connection) -> None:
    """写入一组两层楼演示数据，便于启动后立即体验核验流程。"""
    nodes = [
        ("大厅", "1F", "lobby"),
        ("服务台", "1F", "reception"),
        ("电梯厅", "1F", "elevator_lobby"),
        ("电梯厅", "2F", "elevator_lobby"),
        ("走廊A", "2F", "corridor"),
        ("会议室", "2F", "room"),
        ("无障碍卫生间", "2F", "toilet"),
    ]
    ids = []
    for name, floor, kind in nodes:
        cur = conn.execute(
            "INSERT INTO nodes (name, floor, kind) VALUES (?, ?, ?)", (name, floor, kind)
        )
        ids.append(cur.lastrowid)
    lobby, desk, elev1, elev2, corr, meeting, toilet = ids
    edges = [
        (lobby, desk, "corridor", None, None),
        (lobby, elev1, "door", 90, None),
        (elev1, elev2, "elevator", 80, None),
        (elev2, corr, "corridor", None, None),
        (corr, meeting, "door", 55, None),      # 门宽不足（默认轮椅 60cm）
        (corr, toilet, "door", 80, None),
        (lobby, meeting, "ramp", None, 10),     # 坡度过大（默认上限 8%）
    ]
    for a, b, kind, dw, sl in edges:
        conn.execute(
            "INSERT INTO edges (from_node, to_node, kind, door_width, slope, bidirectional)"
            " VALUES (?, ?, ?, ?, ?, 1)",
            (a, b, kind, dw, sl),
        )
    conn.commit()
