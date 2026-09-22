# 无障碍路线核验台

基于 **FastAPI + SQLite** 的本地 Web 工具：录入楼层节点、门宽、坡度与电梯连接，设置轮椅宽度与最大坡度后，系统将可通行条件编译为图，计算节点间可行路线，标出沿途**首个不合规边**并给出**替代路线**（绕行 / 修复模拟），核验结果自动保存、可按楼层筛选复核并导出 JSON。

## 一键启动（Docker Compose）

```bash
docker compose up --build
```

- 访问地址：**http://localhost:8000**
- 端口：**8000**（Web UI 与 API 同端口；改端口请调整 `docker-compose.yml` 的 `ports`）
- 依赖安装、SQLite 建表与初始化（含一组两层楼演示数据）均在启动时自动完成，无需手工操作
- 数据持久化在命名卷 `app_data` 中；`docker compose down -v` 可清空重来

## 本地开发（不用 Docker）

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## 使用流程

1. **通行设置**：填写轮椅宽度（cm）与最大允许坡度（%），作为全部合规判定基准。
2. **楼层节点**：录入房间 / 电梯厅 / 走廊等位置点及其楼层（支持 `1F`、`B1` 等文本）。
3. **通行连接（边）**：在两个节点间添加走廊、门、坡道或电梯连接，可填门宽（cm）与坡度（%），支持单向/双向；列表实时显示每条边在当前设置下是否合规及原因。
4. **路线核验**：选择起点、终点，系统以「违规边数最少、途经边数最少」求最优路线：
   - 全程合规 → 判定可行；
   - 存在违规 → 标出**首个不合规边**及原因，并给出替代方案：**绕行路线**（避开该边）与**修复模拟**（该边整改达标后的路线）；
   - 图不连通 → 提示补充通道/电梯边。
5. **检查记录**：每次核验自动存档（含当时的判定基准快照），支持按楼层筛选、逐条复核（含原始 JSON）、删除，以及一键**导出 JSON**（可按楼层过滤）。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET / PUT | `/api/settings` | 读取 / 更新轮椅宽度与最大坡度 |
| GET / POST | `/api/nodes` | 节点列表（`?floor=` 筛选）/ 新增节点 |
| DELETE | `/api/nodes/{id}` | 删除节点（级联删除相关边） |
| GET / POST | `/api/edges` | 边列表（含合规状态，`?floor=` 筛选）/ 新增边 |
| DELETE | `/api/edges/{id}` | 删除边 |
| POST | `/api/inspect` | 核验 `{from_node, to_node}`，结果自动保存 |
| GET | `/api/inspections?floor=` | 检查记录列表（按楼层筛选） |
| GET / DELETE | `/api/inspections/{id}` | 复核单条记录 / 删除 |
| GET | `/api/export?floor=` | 导出 JSON（设置 + 节点 + 边 + 记录，可按楼层过滤） |

交互式 API 文档：http://localhost:8000/docs

## 目录结构

```
├── Dockerfile             # 单容器镜像：依赖安装 + 启动
├── docker-compose.yml     # 一键启动，卷持久化 SQLite
├── requirements.txt
└── app/
    ├── main.py            # FastAPI 路由（设置/节点/边/核验/记录/导出）
    ├── db.py              # SQLite 建表、默认设置、空库演示数据
    ├── router_logic.py    # 图编译、最少违规寻路、首条不合规边与替代路线
    └── static/index.html  # 单页 Web 界面
```

环境变量：`DB_PATH`（SQLite 文件路径，容器内默认 `/app/data/app.db`）。
