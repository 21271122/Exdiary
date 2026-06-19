# lib/repositories/base.py — 说明文档

## 文件作用摘要

仓储层抽象接口定义。使用 Python ABC（抽象基类）定义 5 个 Repository 的标准接口契约，隔离 YAML 实现细节。所有具体 Repository 类（如 `YamlExperimentRepository`）都继承对应 ABC。为未来切换到 SQLite 等替代存储提供接口保障。

## 代码块详细说明

### `AbstractExperimentRepository` (ABC)
- **作用**: 实验记录仓储的抽象接口
- **抽象方法** (9 个): `next_id()`, `save()`, `load()`, `update()`, `delete()`, `list_all()`, `list_all_full()`, `summarize_all()`, `count()`
- **实现类**: `YamlExperimentRepository` (lib/repositories/yaml_experiment.py)
- **被注入**: `app.py:148` — `exp_repo = YamlExperimentRepository(...)` → 注入到 `g.exp_repo`
- **被使用**: 所有路由 (`routes/`) 和 `AgentLoop` 的 `self.store` 属性

### `AbstractAnalysisRepository` (ABC)
- **作用**: 分析报告仓储的抽象接口
- **抽象方法** (5 个): `next_id()`, `save()`, `load()`, `list_all()`, `delete()`
- **实现类**: `YamlAnalysisRepository` (lib/repositories/yaml_analysis.py)
- **被注入**: `app.py:149` → `g.analysis_repo`

### `AbstractThreadRepository` (ABC)
- **作用**: 线程持久化仓储的抽象接口（21 个抽象方法）— 最复杂的 ABC
- **抽象方法** (21 个): 分为 5 组 — 线程 CRUD(4) / 索引(2) / 活跃线程+状态(8) / L0摘要+压缩(4) / 子Agent+用户画像(5)
- **实现类**: `ThreadRepository` (Facade, lib/repositories/yaml_thread.py)
- **被注入**: `app.py:150` → `g.thread_repo`

### `AbstractFavoritesRepository` (ABC)
- **作用**: 收藏和置顶仓储的抽象接口
- **抽象方法** (7 个): `is_pinned()`, `toggle_pin()`, `toggle_favorite()`, `get_pinned()`, `get_collections()`, `create_collection()`, `delete_collection()`
- **实现类**: `YamlFavoritesRepository` (lib/repositories/yaml_favorites.py)
- **被注入**: `app.py:151` → `g.favorites_repo`

### `AbstractUpdateLogRepository` (ABC)
- **作用**: 实验更新日志仓储的抽象接口
- **抽象方法** (4 个): `append()`, `list_recent()`, `list_all()`, `get_entry()`
- **实现类**: `YamlUpdateLogRepository` (lib/repositories/yaml_update_log.py)
- **被注入**: `app.py:152` → `g.update_log_repo`
