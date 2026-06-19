# routes/api_sync.py — 说明文档

## 文件作用摘要

云端同步 API 蓝图 `api_sync_bp`，URL 前缀 `/api/sync`。处理实验数据的增量推送和拉取。需要 `@require_auth` 认证。

---

## 代码块详细说明

### 蓝图

- `api_sync_bp = Blueprint("api_sync", __name__)` — 注册在 `/api/sync` 下

### 路由函数

- `sync_push()` — PUT `/api/sync`
  - 请求体: `{records: [{id, data, updated_at}, ...], last_sync: str}`
  - 逻辑: 逐条与本地比较 `updated_at`，云端更新则覆盖本地，本地更新则忽略（LWW 冲突解决）→ 返回 `{ok: true, accepted: N, conflicts: M}`

- `sync_pull()` — GET `/api/sync?since=2026-06-18T08:00:00`
  - 返回自 `since` 时间戳以来被修改的所有实验记录: `{records: [...], server_time: "..."}`
  - 由 `sync/sync_service.py` 的启动拉取逻辑调用
