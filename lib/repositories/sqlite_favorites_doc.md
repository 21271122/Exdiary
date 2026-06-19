# lib/repositories/sqlite_favorites.py — 说明文档

## 文件作用摘要

收藏和置顶功能的 SQLite 仓储实现。替代 `YamlFavoritesRepository`，数据存入 `data.db` 的 `favorites` 表。实现 `AbstractFavoritesRepository` 接口。

---

## 代码块详细说明

### 类

#### `SqliteFavoritesRepository(AbstractFavoritesRepository)`

**构造参数**: `db_path: str`。自动建表。

**实例属性**: `self.db: sqlite3.Connection`

**方法**:

- `_create_tables() -> None` — 建 `favorites` 表（exp_id, pin_order, collection, created_at）。pin_order 用于置顶排序（1-3）。
- `is_pinned(exp_id: str) -> bool` — 检查是否置顶。
- `toggle_pin(exp_id: str) -> dict` — 切换置顶状态。最多 3 个。
- `toggle_favorite(exp_id: str, collection: str = "默认收藏夹") -> dict` — 切换收藏状态。
- `get_pinned() -> list[str]` — 返回置顶 ID 列表，按 pin_order 排序。
- `get_collections() -> dict` — 返回 `{收藏夹名: [exp_id, ...]}`。
- `create_collection(name: str) -> dict` — 新建收藏夹。
- `delete_collection(name: str) -> dict` — 删除收藏夹（不能删默认）。
