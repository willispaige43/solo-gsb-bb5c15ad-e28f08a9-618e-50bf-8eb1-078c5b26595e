"""SQLite 数据访问层：建表、初始化与演示数据。"""
import os
import sqlite3

DB_PATH = os.environ.get("DB_PATH", os.path.join("data", "app.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    wheelchair_width_mm INTEGER NOT NULL DEFAULT 750,
    max_slope_percent REAL NOT NULL DEFAULT 8.33
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
    door_width_mm INTEGER,
    slope_percent REAL,
    bidirectional INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    from_node INTEGER NOT NULL,
    to_node INTEGER NOT NULL,
    status TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    result_json TEXT NOT NULL
);
"""


def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """建表 + 写入默认设置；数据库为空时灌入一份演示数据，便于开箱体验。"""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) AS c FROM settings").fetchone()["c"] == 0:
            conn.execute("INSERT INTO settings (id) VALUES (1)")
        _seed_demo(conn)


def _seed_demo(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) AS c FROM nodes").fetchone()["c"] > 0:
        return
    demo_nodes = [
        ("大厅", "1F", "lobby"),
        ("电梯厅", "1F", "corridor"),
        ("无障碍卫生间", "1F", "room"),
        ("电梯厅", "2F", "corridor"),
        ("办公室A", "2F", "room"),
        ("办公室B", "2F", "room"),
        ("露天平台", "2F", "outdoor"),
    ]
    ids = {}
    for name, floor, kind in demo_nodes:
        cur = conn.execute(
            "INSERT INTO nodes (name, floor, kind) VALUES (?, ?, ?)", (name, floor, kind)
        )
        ids[(name, floor)] = cur.lastrowid

    def edge(a, b, kind="corridor", door_width_mm=None, slope_percent=None):
        conn.execute(
            "INSERT INTO edges (from_node, to_node, kind, door_width_mm, slope_percent)"
            " VALUES (?, ?, ?, ?, ?)",
            (ids[a], ids[b], kind, door_width_mm, slope_percent),
        )

    edge(("大厅", "1F"), ("电梯厅", "1F"))
    edge(("大厅", "1F"), ("无障碍卫生间", "1F"), kind="door", door_width_mm=900)
    edge(("电梯厅", "1F"), ("电梯厅", "2F"), kind="elevator", door_width_mm=900)
    # 门宽 700mm < 默认轮椅 750mm：不合规，系统应给出绕行 办公室B 的替代路线
    edge(("电梯厅", "2F"), ("办公室A", "2F"), kind="door", door_width_mm=700)
    edge(("电梯厅", "2F"), ("办公室B", "2F"), kind="door", door_width_mm=850)
    edge(("办公室B", "2F"), ("办公室A", "2F"), kind="door", door_width_mm=850)
    # 坡度 10% > 默认上限 8.33%：不合规
    edge(("电梯厅", "2F"), ("露天平台", "2F"), kind="ramp", slope_percent=10.0)
