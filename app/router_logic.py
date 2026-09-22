"""图编译与路线计算：把可通行条件编译为带权图，求最少违规路线并给出替代方案。"""
import heapq

INF = float("inf")

KIND_LABELS = {
    "corridor": "走廊/通道",
    "door": "门",
    "ramp": "坡道",
    "elevator": "电梯",
    "stairs": "楼梯",
}


def edge_violations(edge: dict, wheelchair_width: float, max_slope: float) -> list[str]:
    """返回一条边在当前设置下的不合规原因列表（空列表 = 合规）。"""
    reasons = []
    dw = edge.get("door_width")
    if dw is not None and dw < wheelchair_width:
        reasons.append(f"门宽 {dw:g}cm < 轮椅宽度 {wheelchair_width:g}cm")
    sl = edge.get("slope")
    if sl is not None and sl > max_slope:
        reasons.append(f"坡度 {sl:g}% > 最大允许坡度 {max_slope:g}%")
    return reasons


def build_adjacency(edges: list[dict]) -> dict[int, list[dict]]:
    """把边编译为邻接表；双向边展开为两条同 id 的有向边。"""
    adj: dict[int, list[dict]] = {}
    for e in edges:
        adj.setdefault(e["from_node"], []).append(e)
        if e.get("bidirectional"):
            adj.setdefault(e["to_node"], []).append(
                {**e, "from_node": e["to_node"], "to_node": e["from_node"]}
            )
    return adj


def best_path(
    adj: dict[int, list[dict]],
    source: int,
    target: int,
    wheelchair_width: float,
    max_slope: float,
    banned_edge_ids: frozenset[int] = frozenset(),
    fixed_edge_ids: frozenset[int] = frozenset(),
) -> list[dict] | None:
    """Dijkstra，代价为 (违规边数, 经过边数) 的字典序。

    banned_edge_ids: 从图中剔除的边（用于绕行方案）。
    fixed_edge_ids:  视为已修复、按合规处理的边（用于修复模拟）。
    返回边列表表示的路径，不可达返回 None。
    """
    start = (0, 0)
    best: dict[int, tuple[int, int]] = {source: start}
    pq: list[tuple[int, int, int, list[dict]]] = [(0, 0, source, [])]
    while pq:
        viol, hops, node, path = heapq.heappop(pq)
        if (viol, hops) > best.get(node, (INF, INF)):
            continue
        if node == target:
            return path
        for e in adj.get(node, []):
            if e["id"] in banned_edge_ids:
                continue
            bad = 0 if e["id"] in fixed_edge_ids else (
                1 if edge_violations(e, wheelchair_width, max_slope) else 0
            )
            cost = (viol + bad, hops + 1)
            nxt = e["to_node"]
            if cost < best.get(nxt, (INF, INF)):
                best[nxt] = cost
                heapq.heappush(pq, (cost[0], cost[1], nxt, path + [e]))
    return None


def describe_path(
    path: list[dict],
    nodes: dict[int, dict],
    ww: float,
    ms: float,
    fixed_edge_ids: frozenset[int] = frozenset(),
) -> list[dict]:
    """把边列表渲染为带名称、合规标记的途经步骤；fixed_edge_ids 按已修复模拟。"""
    steps = []
    for e in path:
        simulated = e["id"] in fixed_edge_ids
        reasons = [] if simulated else edge_violations(e, ww, ms)
        steps.append(
            {
                "edge_id": e["id"],
                "from_node": e["from_node"],
                "from_name": nodes.get(e["from_node"], {}).get("name", str(e["from_node"])),
                "from_floor": nodes.get(e["from_node"], {}).get("floor"),
                "to_node": e["to_node"],
                "to_name": nodes.get(e["to_node"], {}).get("name", str(e["to_node"])),
                "to_floor": nodes.get(e["to_node"], {}).get("floor"),
                "kind": e["kind"],
                "kind_label": KIND_LABELS.get(e["kind"], e["kind"]),
                "door_width": e.get("door_width"),
                "slope": e.get("slope"),
                "compliant": not reasons,
                "simulated_fix": simulated,
                "reasons": reasons,
            }
        )
    return steps


def inspect_route(
    nodes: list[dict],
    edges: list[dict],
    source: int,
    target: int,
    wheelchair_width: float,
    max_slope: float,
) -> dict:
    """核验 source -> target：最少违规路线、首条不合规边、绕行与修复模拟。"""
    nodes_by_id = {n["id"]: n for n in nodes}
    adj = build_adjacency(edges)
    ww, ms = wheelchair_width, max_slope

    path = best_path(adj, source, target, ww, ms)
    base = {
        "from_node": source,
        "from_name": nodes_by_id[source]["name"],
        "to_node": target,
        "to_name": nodes_by_id[target]["name"],
        "wheelchair_width": ww,
        "max_slope": ms,
    }
    if path is None:
        return {
            **base,
            "reachable": False,
            "feasible": False,
            "violation_count": None,
            "path": [],
            "first_bad_edge": None,
            "alternatives": [],
            "floors": sorted({nodes_by_id[source]["floor"], nodes_by_id[target]["floor"]}),
            "note": "起点与终点之间不存在任何连接（图不连通），请补充通道/电梯边。",
        }

    steps = describe_path(path, nodes_by_id, ww, ms)
    bad_steps = [s for s in steps if not s["compliant"]]
    feasible = not bad_steps
    first_bad = bad_steps[0] if bad_steps else None

    alternatives: list[dict] = []
    if first_bad is not None:
        bad_id = first_bad["edge_id"]
        # 方案一：绕行 —— 从图中剔除首条不合规边后重新寻路
        detour = best_path(adj, source, target, ww, ms, banned_edge_ids=frozenset({bad_id}))
        if detour is not None:
            dsteps = describe_path(detour, nodes_by_id, ww, ms)
            dbad = [s for s in dsteps if not s["compliant"]]
            alternatives.append(
                {
                    "type": "detour",
                    "label": f"绕行方案（避开边 #{bad_id}）",
                    "feasible": not dbad,
                    "violation_count": len(dbad),
                    "path": dsteps,
                    "note": "绕行后全程合规。"
                    if not dbad
                    else f"绕行后仍有 {len(dbad)} 处不合规，需一并整改。",
                }
            )
        # 方案二：修复模拟 —— 首条不合规边视为已整改后重新寻路
        fixed = best_path(adj, source, target, ww, ms, fixed_edge_ids=frozenset({bad_id}))
        if fixed is not None:
            fsteps = describe_path(fixed, nodes_by_id, ww, ms, fixed_edge_ids=frozenset({bad_id}))
            fbad = [s for s in fsteps if not s["compliant"]]
            alternatives.append(
                {
                    "type": "post_fix",
                    "label": f"修复模拟（边 #{bad_id} 整改达标后）",
                    "feasible": not fbad,
                    "violation_count": len(fbad),
                    "path": fsteps,
                    "note": "修复该边后路线即可贯通。"
                    if not fbad
                    else f"修复该边后仍有 {len(fbad)} 处不合规。",
                }
            )

    floors = sorted(
        {nodes_by_id[source]["floor"], nodes_by_id[target]["floor"]}
        | {s["from_floor"] for s in steps}
        | {s["to_floor"] for s in steps}
    )
    return {
        **base,
        "reachable": True,
        "feasible": feasible,
        "violation_count": len(bad_steps),
        "path": steps,
        "first_bad_edge": first_bad,
        "alternatives": alternatives,
        "floors": floors,
        "note": "路线全程合规，可以通行。"
        if feasible
        else f"路线存在 {len(bad_steps)} 处不合规，首处位于边 #{first_bad['edge_id']}。",
    }
