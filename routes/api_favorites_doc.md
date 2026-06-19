# routes/api_favorites.py — 说明文档

## 文件作用摘要

收藏与置顶 API 蓝图 `api_favorites_bp`，URL 前缀 `/api`。提供置顶切换、收藏切换、收藏夹列表、创建/删除收藏夹的 RESTful 端点。

---

## 代码块详细说明

### 路由函数

- `api_toggle_pin(exp_id)` — POST `/api/experiments/<exp_id>/pin`: 切换置顶状态。返回 `g.favorites_repo.toggle_pin(exp_id)` — `{ok, pinned: bool}`
- `api_toggle_favorite(exp_id)` — POST `/api/experiments/<exp_id>/favorite`: 切换收藏状态。请求体可选 `{collection: "名称"}`。返回 `{ok, favorited: bool}`
- `api_list_collections()` — GET `/api/list-collections`: 返回全部收藏夹 dict — `{名称: [exp_id, ...]}`
- `api_create_collection()` — POST `/api/collections`: 创建新收藏夹。请求体 `{name: str}`。返回 `{ok}` 或错误
- `api_delete_collection(name)` — DELETE `/api/collections/<name>`: 删除收藏夹。不能删除"默认收藏夹"
