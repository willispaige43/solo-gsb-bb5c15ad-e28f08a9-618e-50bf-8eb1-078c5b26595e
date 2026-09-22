"""FastAPI 入口：设置、节点、边、核验与 JSON 导出。"""
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .db import get_conn, init_db
from .graph import EDGE_KIND_LABELS, edge_violations, run_check

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(BASE_DIR, "static", "index.html")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # 依赖与初始化随启动完成：建表、默认设置、空库时的演示数据
    yield


app = FastAPI(title="无障碍路线核验台", lifespan=lifespan)


# ---------- 请求模型 ----------

class SettingsIn(BaseModel):
    wheelchair_width_mm: int = Field(ge=300, le=1500, description="轮椅宽度 mm")
    max_slope_percent: float = Field(gt=0, le=45, description="最大允许坡度 %")


class NodeIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    floor: str = Field(min_length=1, max_length=20)
    kind: str = Field(default="room", max_length=20)


class EdgeIn(BaseModel):
    from_node: int
    to_node: int
    kind: str = "corridor"
    door_width_mm: Optional[int] = Field(default=None, ge=1, le=5000)
    slope_percent: Optional[float] = Field(default=None, ge=0, le=90)
    bidirectional: bool = True


class CheckIn(BaseModel):
    from_node: int
    to_node: int


# ---------- 工具 ----------

def get_settings(conn) -> dict:
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    return {
        "wheelchair_width_mm": row["wheelchair_width_mm"],
        "max_slope_percent": row["max_slope_percent"],
    }


def node_or_404(conn, node_id: int):
    row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"节点 {node_id} 不存在")
    return row


def check_out(row, conn) -> dict:
    """把 checks 表行解码为可复核的完整记录（含设置快照与核验结果）。"""
    result = json.loads(row["result_json"])
    names = dict(
        conn.execute("SELECT id, name || '（' || floor || '）' AS label FROM nodes")
        .fetchall()
    )
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "from_node": row["from_node"],
        "to_node": row["to_node"],
        "from_label": names.get(row["from_node"], f"#{row['from_node']}（已删除）"),
        "to_label": names.get(row["to_node"], f"#{row['to_node']}（已删除）"),
        "status": row["status"],
        "status_label": result["status_label"],
        "settings": json.loads(row["settings_json"]),
        "result": result,
    }


# ---------- 页面 ----------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(INDEX_HTML)


@app.get("/api/health")
def health():
    return {"ok": True}


# ---------- 设置 ----------

@app.get("/api/settings")
def read_settings():
    with get_conn() as conn:
        return get_settings(conn)


@app.put("/api/settings")
def update_settings(payload: SettingsIn):
    with get_conn() as conn:
        conn.execute(
            "UPDATE settings SET wheelchair_width_mm = ?, max_slope_percent = ? WHERE id = 1",
            (payload.wheelchair_width_mm, payload.max_slope_percent),
        )
        return get_settings(conn)


# ---------- 节点 ----------

@app.get("/api/nodes")
def list_nodes(floor: Optional[str] = Query(default=None)):
    sql = "SELECT * FROM nodes"
    args = ()
    if floor:
        sql += " WHERE floor = ?"
        args = (floor,)
    sql += " ORDER BY floor, id"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


@app.post("/api/nodes", status_code=201)
def create_node(payload: NodeIn):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO nodes (name, floor, kind) VALUES (?, ?, ?)",
            (payload.name.strip(), payload.floor.strip(), payload.kind),
        )
        return dict(conn.execute("SELECT * FROM nodes WHERE id = ?", (cur.lastrowid,)).fetchone())


@app.delete("/api/nodes/{node_id}")
def delete_node(node_id: int):
    with get_conn() as conn:
        node_or_404(conn, node_id)
        conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        return {"deleted": node_id}


# ---------- 边（连接） ----------

@app.get("/api/edges")
def list_edges(floor: Optional[str] = Query(default=None)):
    """返回边列表，附带端点信息与按当前设置计算的合规状态。"""
    with get_conn() as conn:
        settings = get_settings(conn)
        nodes = {n["id"]: n for n in conn.execute("SELECT * FROM nodes").fetchall()}
        rows = conn.execute("SELECT * FROM edges ORDER BY id").fetchall()
        out = []
        for e in rows:
            a, b = nodes.get(e["from_node"]), nodes.get(e["to_node"])
            if a is None or b is None:
                continue
            if floor and a["floor"] != floor and b["floor"] != floor:
                continue
            d = dict(e)
            d.update(
                from_name=a["name"], from_floor=a["floor"],
                to_name=b["name"], to_floor=b["floor"],
                kind_label=EDGE_KIND_LABELS.get(e["kind"], e["kind"]),
                violations=edge_violations(e, settings),
            )
            out.append(d)
        return out


@app.post("/api/edges", status_code=201)
def create_edge(payload: EdgeIn):
    if payload.from_node == payload.to_node:
        raise HTTPException(status_code=400, detail="起点与终点不能是同一节点")
    if payload.kind not in EDGE_KIND_LABELS:
        raise HTTPException(status_code=400, detail=f"未知边类型：{payload.kind}")
    with get_conn() as conn:
        node_or_404(conn, payload.from_node)
        node_or_404(conn, payload.to_node)
        cur = conn.execute(
            "INSERT INTO edges (from_node, to_node, kind, door_width_mm, slope_percent, bidirectional)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                payload.from_node, payload.to_node, payload.kind,
                payload.door_width_mm, payload.slope_percent,
                1 if payload.bidirectional else 0,
            ),
        )
        return dict(conn.execute("SELECT * FROM edges WHERE id = ?", (cur.lastrowid,)).fetchone())


@app.delete("/api/edges/{edge_id}")
def delete_edge(edge_id: int):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"边 {edge_id} 不存在")
        return {"deleted": edge_id}


# ---------- 核验 ----------

@app.post("/api/checks", status_code=201)
def create_check(payload: CheckIn):
    """执行一次路线核验并保存结果（含设置快照），供事后复核。"""
    if payload.from_node == payload.to_node:
        raise HTTPException(status_code=400, detail="起点与终点不能相同")
    with get_conn() as conn:
        node_or_404(conn, payload.from_node)
        node_or_404(conn, payload.to_node)
        settings = get_settings(conn)
        nodes = conn.execute("SELECT * FROM nodes").fetchall()
        edges = conn.execute("SELECT * FROM edges").fetchall()
        result = run_check(nodes, edges, settings, payload.from_node, payload.to_node)
        cur = conn.execute(
            "INSERT INTO checks (from_node, to_node, status, settings_json, result_json)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                payload.from_node, payload.to_node, result["status"],
                json.dumps(settings, ensure_ascii=False),
                json.dumps(result, ensure_ascii=False),
            ),
        )
        row = conn.execute("SELECT * FROM checks WHERE id = ?", (cur.lastrowid,)).fetchone()
        return check_out(row, conn)


@app.get("/api/checks")
def list_checks(floor: Optional[str] = Query(default=None), limit: int = Query(default=100, ge=1, le=1000)):
    """核验记录列表；floor 过滤起点或终点位于该楼层的记录。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM checks ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        if floor:
            floors = dict(
                conn.execute("SELECT id, floor FROM nodes").fetchall()
            )
            rows = [
                r for r in rows
                if floors.get(r["from_node"]) == floor or floors.get(r["to_node"]) == floor
            ]
        return [check_out(r, conn) for r in rows]


@app.get("/api/checks/{check_id}")
def get_check(check_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM checks WHERE id = ?", (check_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"核验记录 {check_id} 不存在")
        return check_out(row, conn)


@app.delete("/api/checks/{check_id}")
def delete_check(check_id: int):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM checks WHERE id = ?", (check_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"核验记录 {check_id} 不存在")
        return {"deleted": check_id}


# ---------- JSON 导出 ----------

@app.get("/api/export")
def export_checks(floor: Optional[str] = Query(default=None)):
    """导出核验记录（含设置快照与完整结果）为 JSON 文件，支持按楼层过滤。"""
    with get_conn() as conn:
        settings = get_settings(conn)
        rows = conn.execute("SELECT * FROM checks ORDER BY id").fetchall()
        if floor:
            floors = dict(conn.execute("SELECT id, floor FROM nodes").fetchall())
            rows = [
                r for r in rows
                if floors.get(r["from_node"]) == floor or floors.get(r["to_node"]) == floor
            ]
        payload = {
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "floor_filter": floor,
            "current_settings": settings,
            "count": len(rows),
            "checks": [check_out(r, conn) for r in rows],
        }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{floor}" if floor else ""
    return JSONResponse(
        payload,
        headers={
            "Content-Disposition": f'attachment; filename="accessibility_checks{suffix}_{ts}.json"'
        },
    )
