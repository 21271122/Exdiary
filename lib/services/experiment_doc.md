# lib/services/experiment.py — 说明文档

## 文件作用摘要

实验 CRUD 与引用管理 + 更新日志 + 图片迁移服务。从 `app.py` 私有函数迁出形成独立服务类。提供实验保存时自动 diff + 引用双向维护 + 图片迁移等一站式操作。被 `app.py` 创建并注入到 `flask.g`。

---

## 代码块详细说明

### 类

#### `ExperimentService`
- **构造参数**: `exp_repo` (实验仓储), `update_log_repo` (更新日志仓储), `favorites_repo` (收藏仓储), `base_dir: Path | None` (项目根目录，用于图片迁移)
- **被注入**: `app.py:155` → `g.experiment_svc`

##### 公开方法

- `save_with_log(exp_id, data, source, thread_id=None) -> None`: 保存实验 + 自动 diff + 写日志。自动判断新建(调 save)还是修改(调 update+append)
  - **被调用**: `routes/experiment.py:43` (YAML编辑器 POST), `routes/api_child.py:263` (子Agent confirm), `routes/experiment.py:63` (JSON保存, 通过 `save_and_update_refs` 间接)

- `delete_with_log(exp_id) -> None`: 删除实验 + 写系统删除日志
  - **被调用**: `routes/experiment.py:52` (DELETE 路由)

- `extract_references(text) -> list[str]`: 正则提取 `@EXP-YYYY-NNN` 引用
  - **被调用**: `self.save_and_update_refs()`, `routes/api_experiment.py:57`, `routes/experiment.py:92`, `routes/api_agent.py:56`, `routes/api_child.py:256`

- `update_referenced_by(exp_id, refs, old_refs=None) -> None`: 维护双向引用（新增引用 + 移除旧引用）
  - **被调用**: `self.save_and_update_refs()`, `routes/api_experiment.py:60`, `routes/experiment.py:97`, `routes/api_agent.py:59`, `routes/api_child.py:265`

- `save_and_update_refs(exp_id, data, source, old_refs=None, thread_id=None) -> None`: 一站式保存（extract_references → save_with_log → update_referenced_by）
  - **被调用**: `routes/experiment.py:63` (JSON 保存)

- `move_draft_images(exp_id) -> None`: 将 `uploads/_draft/` → `uploads/{exp_id}/`，完成后删除 _draft 目录
  - **被调用**: `routes/api_experiment.py:44` (api_parse), `routes/api_experiment.py:61` (api_parse_confirm), `routes/api_agent.py:60` (agent message 保存)

- `get_pinned_and_others() -> tuple[list[dict], list[str]]`: 获取置顶优先排序的实验列表
  - **被调用**: **无外部调用**。`routes/dashboard.py` 的 `index()` 和 `experiment_list()` 中已将置顶逻辑内联实现，未调用此方法。此方法为旧版 API 遗留。

##### 私有方法

- `_compute_diff(old, new) -> list[dict]`: 委托给模块级 `compute_experiment_diff()`

### 模块级函数

#### `compute_experiment_diff(old: dict | None, new: dict) -> list[dict]`
- **作用**: 比较两个实验 dict，返回差异列表
- **输出**: `[{path, field, old, new}]` 格式的差异条目列表
- **比较策略**: 简单字段值比较 / 数组字段 list 比较 / 复杂字段 JSON 序列化比较 / 嵌套字段 dict 比较
- **被调用**:
  - `ExperimentService._compute_diff()` (line 142) → `save_with_log()` 中自动 diff
  - `ToolExecutor._modify_experiment()` (lib/agent_v2.py:568-569) — runtime import:
    ```python
    from lib.services.experiment import compute_experiment_diff
    entries = compute_experiment_diff(old_exp, exp)
    ```
    Agent 修改实验时计算 diff 写更新日志
