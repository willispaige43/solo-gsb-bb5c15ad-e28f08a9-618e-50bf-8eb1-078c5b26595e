"""图编译与路线核验：把通行条件编译为图，计算可行路线并定位不合规边。"""
import heapq
import itertools

EDGE_KIND_LABELS = {
    "corridor": "走廊/通道",
    "door": "门",
    "ramp": "坡道",
    "elevator": "电梯",
    "stairs": "楼梯",
}

STATUS_LABELS = {
    "pass": "可直接通行",
    "detour": "需绕行",
    "fail": "不可通行（需整改）",
    "unreachable": "不连通",
}

STATUS_MESSAGES = {
    "pass": "最短路线全部合规，轮椅可直接通行。",
    "detour": "最短路线存在不合规边，已给出可通行的替代路线。",
    "fail": "当前设置下不存在完全合规的路线，备选路线按整改代价从小到大排序。",
    "unreachable": "两节点之间不存在任何路径（图不连通）。",
}


def edge_violations(edge, settings):
    """按当前设置检查单条边，返回不合规原因列表（空列表 = 合规）。"""
    v = []
    kind = edge["kind"]
    dw = edge["door_width_mm"]
    sp = edge["slope_percent"]
    ww = settings["wheelchair_width_mm"]
    ms = settings["max_slope_percent"]
    if kind == "stairs":
        v.append("楼梯，轮椅不可通行")
    if kind == "door" and dw is None:
        v.append("门宽未填写，无法核验")
    if kind == "ramp" and sp is None:
        v.append("坡度未填写，无法核验")
    if dw is not None and dw < ww:
        v.append(f"门宽 {dw}mm 小于轮椅宽度 {ww}mm")
    if sp is not None and sp > ms:
        v.append(f"坡度 {sp}% 超过最大允许 {ms}%")
    return v


def build_adjacency(nodes, edges, violations=None):
    """编译为邻接表 {node_id: [(neighbor_id, edge_id), ...]}；
    传入 violations 时只保留合规边，即可通行子图。"""
    adj = {n["id"]: [] for n in nodes}
    for e in edges:
        if violations is not None and violations[e["id"]]:
            continue
        adj[e["from_node"]].append((e["to_node"], e["id"]))
        if e["bidirectional"]:
            adj[e["to_node"]].append((e["from_node"], e["id"]))
    return adj


def k_shortest_paths(adj, src, dst, k, max_hops=20, pop_cap=2000):
    """K 条最短简单路径（按边数），best-first 搜索，适合楼栋规模的小图。"""
    if src == dst:
        return [[src]]
    counter = itertools.count()
    heap = [(0, next(counter), src, [src])]
    out, pops = [], 0
    while heap and len(out) < k and pops < pop_cap:
        cost, _, node, path = heapq.heappop(heap)
        pops += 1
        if node == dst:
            out.append(path)
            continue
        if len(path) > max_hops:
            continue
        for nbr, eid in adj.get(node, []):
            if nbr in path:
                continue
            heapq.heappush(heap, (cost + 1, next(counter), nbr, path + [nbr]))
    return out


def run_check(nodes, edges, settings, from_id, to_id):
    """执行一次核验，返回可 JSON 序列化的完整结果。

    状态语义：
    - pass        最短路线全部合规
    - detour      最短路线不合规，但存在可通行的替代路线
    - fail        不存在完全合规路线（备选路线附各自不合规边，供整改参考）
    - unreachable 图不连通
    """
    node_by_id = {n["id"]: n for n in nodes}
    edge_by_id = {e["id"]: e for e in edges}
    violations = {e["id"]: edge_violations(e, settings) for e in edges}
    adj_all = build_adjacency(nodes, edges)
    adj_ok = build_adjacency(nodes, edges, violations)

    def path_edge_ids(path):
        eids = []
        for a, b in zip(path, path[1:]):
            for nbr, eid in adj_all[a]:
                if nbr == b:
                    eids.append(eid)
                    break
        return eids

    def edge_detail(eid):
        e = edge_by_id[eid]
        return {
            "id": eid,
            "kind": e["kind"],
            "kind_label": EDGE_KIND_LABELS.get(e["kind"], e["kind"]),
            "from_node": e["from_node"],
            "to_node": e["to_node"],
            "from_name": node_by_id[e["from_node"]]["name"],
            "to_name": node_by_id[e["to_node"]]["name"],
            "door_width_mm": e["door_width_mm"],
            "slope_percent": e["slope_percent"],
            "violations": violations[eid],
        }

    def describe(path):
        eds = [edge_detail(eid) for eid in path_edge_ids(path)]
        return {
            "nodes": [
                {"id": n, "name": node_by_id[n]["name"], "floor": node_by_id[n]["floor"]}
                for n in path
            ],
            "edges": eds,
            "hops": len(path) - 1,
            "violation_count": sum(1 for e in eds if e["violations"]),
        }

    naive = k_shortest_paths(adj_all, from_id, to_id, k=1)
    if not naive:
        status = "unreachable"
        return {
            "status": status,
            "status_label": STATUS_LABELS[status],
            "message": STATUS_MESSAGES[status],
            "path": None,
            "direct_path": None,
            "first_bad_edge": None,
            "alternatives": [],
        }

    p0 = naive[0]
    bad_on_p0 = [eid for eid in path_edge_ids(p0) if violations[eid]]
    feasible = k_shortest_paths(adj_ok, from_id, to_id, k=3)

    if not bad_on_p0:
        status = "pass"
        main_path = p0
        first_bad = None
        alternatives = [describe(p) for p in feasible if p != p0][:2]
    elif feasible:
        status = "detour"
        main_path = feasible[0]
        first_bad = edge_detail(bad_on_p0[0])
        alternatives = [describe(p) for p in feasible[1:]]
    else:
        status = "fail"
        main_path = p0
        first_bad = edge_detail(bad_on_p0[0])
        candidates = k_shortest_paths(adj_all, from_id, to_id, k=8)
        ranked = sorted(
            (p for p in candidates if p != p0),
            key=lambda p: (
                sum(1 for eid in path_edge_ids(p) if violations[eid]),
                len(p),
            ),
        )[:3]
        alternatives = [describe(p) for p in ranked]

    return {
        "status": status,
        "status_label": STATUS_LABELS[status],
        "message": STATUS_MESSAGES[status],
        "path": describe(main_path),
        "direct_path": describe(p0),
        "first_bad_edge": first_bad,
        "alternatives": alternatives,
    }
