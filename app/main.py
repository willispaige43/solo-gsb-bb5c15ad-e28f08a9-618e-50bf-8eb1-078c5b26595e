"""FastAPI 入口：设置、节点、边、路线核验、检查记录、楼层筛选与 JSON 导出。"""
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import db
from .router_logic import KIND_LABELS, edge_violations, inspect_route

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()  # 启动即完成建表、默认设置与空库演示数据
    yield


app = FastAPI(title="无障碍路线核验台", version="1.0.0", lifespan=lifespan)


# ---------- 请求模型 ----------

class SettingsIn(BaseModel):
    wheelchair_width: float = Field(gt=0, le=200, description="轮椅宽度 cm")
    max_slope: float = Field(ge=0, le=100, description="最大允许坡度 %")


class NodeIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    floor: str = Field(min_length=1, max_length=20)
    kind: str = Field(default="room", max_length=30)


class EdgeIn(BaseModel):
    from_node: int
    to_node: int
    kind: str = Field(default="corridor")
    door_width: float | None = Field(default=None, gt=0, le=500)
    slope: float | None = Field(default=None, ge=0, le=100)
    bidirectional: bool = True


class InspectIn(BaseModel):
    from_node: int
    to_node: int


# ---------- 页面 ----------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


# ---------- 设置 ----------

@app.get("/api/settings")
def get_settings():
    conn = db.get_conn()
    try:
        return db.row(conn, "SELECT wheelchair_width, max_slope FROM settings WHERE id = 1")
    finally:
        conn.close()


@app.put("/api/settings")
def put_settings(s: SettingsIn):
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE settings SET wheelchair_width = ?, max_slope = ? WHERE id = 1",
            (s.wheelchair_width, s.max_slope),
        )
        conn.commit()
        return {"ok": True, **s.model_dump()}
    finally:
        conn.close()


# ---------- 节点 ----------

@app.get("/api/nodes")
def list_nodes(floor: str | None = Query(default=None)):
    conn = db.get_conn()
    try:
        if floor:
            return db.rows(conn, "SELECT * FROM nodes WHERE floor = ? ORDER BY id", (floor,))
        return db.rows(conn, "SELECT * FROM nodes ORDER BY floor, id")
    finally:
        conn.close()


@app.post("/api/nodes", status_code=201)
def create_node(n: NodeIn):
    conn = db.get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO nodes (name, floor, kind) VALUES (?, ?, ?)",
            (n.name.strip(), n.floor.strip(), n.kind.strip() or "room"),
        )
        conn.commit()
        return db.row(conn, "SELECT * FROM nodes WHERE id = ?", (cur.lastrowid,))
    finally:
        conn.close()


@app.delete("/api/nodes/{node_id}")
def delete_node(node_id: int):
    conn = db.get_conn()
    try:
        cur = conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "节点不存在")
        return {"ok": True}
    finally:
        conn.close()


# ---------- 边（门 / 坡道 / 电梯连接） ----------

def _edges_with_status(conn, floor: str | None):
    settings = db.row(conn, "SELECT wheelchair_width, max_slope FROM settings WHERE id = 1")
    nodes = {n["id"]: n for n in db.rows(conn, "SELECT * FROM nodes")}
    out = []
    for e in db.rows(conn, "SELECT * FROM edges ORDER BY id"):
        a, b = nodes.get(e["from_node"]), nodes.get(e["to_node"])
        if a is None or b is None:
            continue
        if floor and floor not in (a["floor"], b["floor"]):
            continue
        reasons = edge_violations(e, settings["wheelchair_width"], settings["max_slope"])
        out.append(
            {
                **e,
                "kind_label": KIND_LABELS.get(e["kind"], e["kind"]),
                "from_name": a["name"],
                "from_floor": a["floor"],
                "to_name": b["name"],
                "to_floor": b["floor"],
                "compliant": not reasons,
                "reasons": reasons,
            }
        )
    return out


@app.get("/api/edges")
def list_edges(floor: str | None = Query(default=None)):
    conn = db.get_conn()
    try:
        return _edges_with_status(conn, floor)
    finally:
        conn.close()


@app.post("/api/edges", status_code=201)
def create_edge(e: EdgeIn):
    if e.from_node == e.to_node:
        raise HTTPException(400, "起点与终点不能是同一节点")
    conn = db.get_conn()
    try:
        for nid in (e.from_node, e.to_node):
            if not db.row(conn, "SELECT id FROM nodes WHERE id = ?", (nid,)):
                raise HTTPException(404, f"节点 {nid} 不存在")
        cur = conn.execute(
            "INSERT INTO edges (from_node, to_node, kind, door_width, slope, bidirectional)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (e.from_node, e.to_node, e.kind, e.door_width, e.slope, int(e.bidirectional)),
        )
        conn.commit()
        return {"id": cur.lastrowid, **e.model_dump()}
    finally:
        conn.close()


@app.delete("/api/edges/{edge_id}")
def delete_edge(edge_id: int):
    conn = db.get_conn()
    try:
        cur = conn.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "边不存在")
        return {"ok": True}
    finally:
        conn.close()


# ---------- 路线核验 ----------

@app.post("/api/inspect", status_code=201)
def inspect(body: InspectIn):
    if body.from_node == body.to_node:
        raise HTTPException(400, "起点与终点不能相同")
    conn = db.get_conn()
    try:
        nodes = db.rows(conn, "SELECT * FROM nodes")
        ids = {n["id"] for n in nodes}
        for nid in (body.from_node, body.to_node):
            if nid not in ids:
                raise HTTPException(404, f"节点 {nid} 不存在")
        settings = db.row(conn, "SELECT wheelchair_width, max_slope FROM settings WHERE id = 1")
        edges = db.rows(conn, "SELECT * FROM edges")
        result = inspect_route(
            nodes, edges, body.from_node, body.to_node,
            settings["wheelchair_width"], settings["max_slope"],
        )
        cur = conn.execute(
            "INSERT INTO inspections"
            " (from_node, to_node, wheelchair_width, max_slope, feasible, floors, result_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body.from_node, body.to_node,
                settings["wheelchair_width"], settings["max_slope"],
                int(result["feasible"]), json.dumps(result["floors"], ensure_ascii=False),
                json.dumps(result, ensure_ascii=False), db.now_str(),
            ),
        )
        conn.commit()
        return {"id": cur.lastrowid, "created_at": db.now_str(), **result}
    finally:
        conn.close()


# ---------- 检查记录（可复核） ----------

def _inspection_summary(r: dict) -> dict:
    result = json.loads(r["result_json"])
    return {
        "id": r["id"],
        "created_at": r["created_at"],
        "from_node": r["from_node"],
        "from_name": result["from_name"],
        "to_node": r["to_node"],
        "to_name": result["to_name"],
        "wheelchair_width": r["wheelchair_width"],
        "max_slope": r["max_slope"],
        "feasible": bool(r["feasible"]),
        "reachable": result["reachable"],
        "violation_count": result["violation_count"],
        "floors": json.loads(r["floors"]),
    }


@app.get("/api/inspections")
def list_inspections(floor: str | None = Query(default=None)):
    conn = db.get_conn()
    try:
        items = [
            _inspection_summary(r)
            for r in db.rows(conn, "SELECT * FROM inspections ORDER BY id DESC")
        ]
        if floor:
            items = [i for i in items if floor in i["floors"]]
        return items
    finally:
        conn.close()


@app.get("/api/inspections/{inspection_id}")
def get_inspection(inspection_id: int):
    conn = db.get_conn()
    try:
        r = db.row(conn, "SELECT * FROM inspections WHERE id = ?", (inspection_id,))
        if not r:
            raise HTTPException(404, "检查记录不存在")
        return {
            "id": r["id"],
            "created_at": r["created_at"],
            **json.loads(r["result_json"]),
        }
    finally:
        conn.close()


@app.delete("/api/inspections/{inspection_id}")
def delete_inspection(inspection_id: int):
    conn = db.get_conn()
    try:
        cur = conn.execute("DELETE FROM inspections WHERE id = ?", (inspection_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "检查记录不存在")
        return {"ok": True}
    finally:
        conn.close()


# ---------- JSON 导出 ----------

@app.get("/api/export")
def export_json(floor: str | None = Query(default=None)):
    conn = db.get_conn()
    try:
        settings = db.row(conn, "SELECT wheelchair_width, max_slope FROM settings WHERE id = 1")
        nodes = db.rows(conn, "SELECT * FROM nodes ORDER BY id")
        edges = _edges_with_status(conn, floor)
        inspections = [
            _inspection_summary(r)
            for r in db.rows(conn, "SELECT * FROM inspections ORDER BY id")
        ]
        if floor:
            nodes = [n for n in nodes if n["floor"] == floor]
            inspections = [i for i in inspections if floor in i["floors"]]
        payload = {
            "exported_at": db.now_str(),
            "floor_filter": floor,
            "settings": settings,
            "nodes": nodes,
            "edges": edges,
            "inspections": inspections,
        }
        fname = f"accessibility_export_{floor or 'all'}.json"
        return JSONResponse(
            payload,
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
    finally:
        conn.close()
