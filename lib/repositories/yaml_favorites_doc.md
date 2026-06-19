# lib/repositories/yaml_favorites.py — 说明文档

## 文件作用摘要

收藏和置顶功能的 YAML 文件存储实现。所有收藏数据存储在 `experiments/_favorites.yaml` 单一文件中，带内存缓存 `self._data`（加载后缓存，`_save()` 写回）。实现 `AbstractFavoritesRepository` 接口。被 `app.py` 创建并注入到 `flask.g`。

---

## 代码块详细说明

### 类

#### `YamlFavoritesRepository` (AbstractFavoritesRepository)
- **构造参数**: `path: str` — `_favorites.yaml` 文件完整路径
- **实例属性**: `self.path: Path`, `self._data: dict | None`（内存缓存，仅在 `_load()` 后非空，`_save()` 后保持缓存）

##### 内部方法

- `_load() -> dict`: 加载 YAML 数据。优先使用 `self._data` 缓存；文件不存在时初始化默认结构 `{"pinned": [], "collections": {"默认收藏夹": []}}`
- `_save() -> None`: 将 `self._data` 写回 YAML 文件（仅在缓存非空时执行）

##### 公开方法

- `is_pinned(exp_id: str) -> bool`: 检查实验是否在 pinned 列表中
  - **被调用**: 无直接外部调用（代码库中未找到调用点；主要由前端通过 API 间接判断）
- `is_favorited(exp_id: str, collection: str = "默认收藏夹") -> bool`: 检查实验是否在某收藏夹中
  - **被调用**: 无直接外部调用（同 is_pinned）
- `toggle_pin(exp_id: str) -> dict`:
  - **作用**: 切换置顶状态。已置顶→取消；未置顶→追加（最多 3 个，超过返回 error）
  - **返回**: `{"ok": True/False, "pinned": True/False}` 或 `{"ok": False, "error": "最多只能置顶 3 个实验"}`
  - **被调用**:
    - `routes/api_favorites.py:8` — `api_toggle_pin()` POST `/api/experiments/<exp_id>/pin`
    - `ToolExecutor._manage_collection()` (lib/agent_v2.py:601-604) — Agent 调用 manage_collection 工具时，action=pin/unpin
- `toggle_favorite(exp_id: str, collection: str = "默认收藏夹") -> dict`:
  - **作用**: 切换收藏状态。收藏夹不存在时自动创建。已收藏→移除；未收藏→追加
  - **返回**: `{"ok": True, "favorited": True/False}`
  - **被调用**:
    - `routes/api_favorites.py:13` — `api_toggle_favorite()` POST `/api/experiments/<exp_id>/favorite`
    - `ToolExecutor._manage_collection()` (lib/agent_v2.py:606-609) — action=favorite/unfavorite
- `get_pinned() -> list[str]`: 返回置顶实验 ID 列表
  - **被调用**: `routes/dashboard.py:10,49` — 主页 `index()` 和 `experiment_list()` 获取置顶排序
- `get_collections() -> dict`: 返回全部收藏夹及其包含的实验 ID 列表 `{收藏夹名: [exp_id, ...]}`
  - **被调用**: `routes/dashboard.py:114` — `favorites_page()` 渲染收藏页；`routes/api_favorites.py:18` — `api_list_collections()`
- `create_collection(name: str) -> dict`: 创建新收藏夹。重名返回 `{"ok": False}`
  - **被调用**: `routes/api_favorites.py:27` — `api_create_collection()`
- `delete_collection(name: str) -> dict`: 删除收藏夹。不能删除"默认收藏夹"
  - **被调用**: `routes/api_favorites.py:31` — `api_delete_collection()`
- `add_to_collection(exp_id: str, collection: str) -> dict`: 将实验添加到指定收藏夹
  - **被调用**: 无直接外部调用（Agent 通过 manage_collection 工具用 toggle_favorite 实现）
- `remove_from_collection(exp_id: str, collection: str = "默认收藏夹") -> dict`: 从收藏夹移除实验
  - **被调用**: 无直接外部调用（Agent 通过 manage_collection 工具用 toggle_favorite 实现）
