# sync/sync_service.py — 说明文档

## 文件作用摘要

同步调度服务。负责定时推送 dirty 记录到云端 + 启动时从云端拉取增量 + LWW 冲突解决。不直接操作 HTTP——通过 `requests` 调 `routes/api_sync.py` 的端点。

---

## 代码块详细说明

### 模块级常量

- `PUSH_INTERVAL: int = 30` — 定时推送间隔（秒）。
- `MAX_DIRTY_BATCH: int = 100` — 每次推送最多发送多少条 dirty 记录。

### 类

#### `SyncService`

**构造参数**: `repo: AbstractExperimentRepository`, `api_base_url: str`（云端 API 地址）, `user_id: str`。

**实例属性**:
- `self._dirty: set[str]` — 待推送的实验 ID 集合。每次 `save()` 后由调用方标记。线程不安全——适用于单线程 Flask 开发服务器。

**方法**:

- `mark_dirty(exp_id: str) -> None` — 标记一条实验需要同步推送。
- `pull_on_startup() -> None` — 启动时调用。GET `/sync?since={last_sync}` → 逐条与本地比较 `updated_at` → LWW 合并 → 更新 `last_sync` 时间戳。
- `push_dirty() -> None` — 定时任务。检查 `_dirty` 集合 → 批量读取实验数据 → PUT `/sync` → 清空已成功推送的 `_dirty` 条目。
- `conflict_resolve(local: dict, remote: dict) -> dict` — LWW 冲突解决。比较 `updated_at`，返回较新的版本。两者时间相同保留本地。
- `start_background() -> None` — 启动后台线程。定时调 `push_dirty()`。用 `threading.Thread(daemon=True)`。

### 模块级函数

- `init_sync_service(repo, api_base_url, user_id) -> SyncService` — 工厂函数。创建实例 → `pull_on_startup()` → `start_background()` → 返回实例。
