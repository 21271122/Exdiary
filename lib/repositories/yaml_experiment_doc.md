# lib/repositories/yaml_experiment.py — 说明文档

## 文件作用摘要

实验记录的 YAML 文件系统仓储实现。每个实验以 `EXP-YYYY-NNN.yaml` 格式存储在 `experiments/` 目录下。实现 `AbstractExperimentRepository` 接口。被 `app.py` 创建并注入到 `flask.g`，供所有路由和服务使用。

---

## 代码块详细说明

### 类

#### `YamlExperimentRepository` (AbstractExperimentRepository)
- **作用**: 基于 YAML 文件系统的实验记录持久化
- **构造参数**: `path: str` — 实验数据目录路径（如 `experiments/`），自动创建目录
- **实例属性**: `self.path: Path`

##### 方法

- `next_id() -> str`:
  - **作用**: 扫描目录中已存在的 `EXP-{year}-*.yaml` 文件，生成下一个增量 ID
  - **实现**: 正则匹配当年编号 → 取 max N → 返回 `EXP-{year}-{max_n+1:03d}`
  - **被调用**:
    - `app.py` 路由中: `experiment.py:view_experiment` 等不直接调用此方法
    - `ExperimentService.save_with_log()` (未直接调用，由其内部的 `self.exp_repo.save()` 自动处理 ID)
    - `_fallback_preview()` (lib/agent_v2.py:121) — `"id": loop.store.next_id()` 生成预览 ID
    - `routes/api_agent.py:55` — `preview["id"] = g.exp_repo.next_id()` Agent 保存前设置 ID
    - `routes/api_experiment.py:40` — `result["id"] = g.exp_repo.next_id()` 解析结果设置 ID
    - `routes/api_experiment.py:55` — `exp_id = data.get("id", g.exp_repo.next_id())` confirm 时设置 ID

- `save(experiment: dict) -> str`:
  - **作用**: 将实验 dict 全量写入 `{exp_id}.yaml` 文件（覆盖写入）。自动设置 `experiment["id"]`
  - **输入**: `experiment` — 完整的实验数据 dict
  - **输出**: 实验 ID 字符串
  - **被调用**:
    - `ExperimentService.save_with_log()` (lib/services/experiment.py:38) — 新建实验时
    - `ExperimentService.update_referenced_by()` (lib/services/experiment.py:86,93) — 更新双向引用时重新保存被引用实验
    - `ToolExecutor._generate_analysis()` (lib/agent_v2.py:363) — 分析生成后更新实验的 `analyzed_in` 字段
    - `ToolExecutor._modify_analysis()` (lib/agent_v2.py:446) — 修改分析后更新实验关联
    - `routes/api_experiment.py:43` — `api_parse()` 传统解析后直接保存
    - `routes/api_experiment.py:59` — `api_parse_confirm()` 确认后保存
    - `routes/api_agent.py:58` — Agent 消息处理中自动保存提取结果

- `load(exp_id: str) -> dict | None`:
  - **作用**: 从 YAML 文件加载单个实验的完整数据（`yaml.safe_load`）
  - **输入**: `exp_id` — 如 "EXP-2026-001"
  - **输出**: 实验数据 dict，文件不存在返回 None
  - **被调用**: 几乎所有业务代码 — 视图路由 (`routes/experiment.py`), Agent 工具 (`ToolExecutor._load_reference`, `_query_experiment`, `_modify_experiment`, `_fuzzy_search`, `_summarize_exp`), 分析服务 (`AnalysisService.run_analysis`), API 路由 (`api_search`, `api_child`), `ExperimentService` 的各个方法等

- `list_all() -> list[dict]`:
  - **作用**: 列出全部实验的摘要信息（id/title/date/experimenter/status/tags），按文件名倒序
  - **被调用**: `ExperimentService.summarize_all()` (内部), 主页路由 `dashboard.py:index()` 和 `experiment_list()`

- `list_all_full() -> list[dict]`:
  - **作用**: 列出全部实验的完整 dict 数据（遍历所有 EXP-*.yaml 并 safe_load）
  - **被调用**: `ToolExecutor._fuzzy_search()` 和 `_llm_semantic_search()`, `GlobalContextStore.build_global_summary()`, `UserProfileStore.recalc_tag_counts()`, `routes/api_search.py:api_experiments_search()` 和 `api_resolve_reference()`, 主页 recent_snippets 构造

- `summarize_all(exp_ids: list[str] | None = None) -> str`:
  - **作用**: 生成实验摘要文本（含 id/title/date/status/tags/purpose/conclusion/results/observations），用于 LLM 分析
  - **输入**: `exp_ids` — 可选，限制摘要范围到指定实验
  - **输出**: `"### EXP-ID: Title\nDate: ... \n---\n"` 格式的 Markdown 文本
  - **被调用**: `AnalysisService.run_analysis()` (lib/services/analysis.py:18), `ToolExecutor._generate_analysis()` 回退路径 (lib/agent_v2.py:346), `ToolExecutor._modify_analysis()` 回退路径 (lib/agent_v2.py:435)

- `update(exp_id: str, experiment: dict) -> bool`:
  - **作用**: 全量覆盖更新实验文件，保留原 ID
  - **被调用**: `ExperimentService.save_with_log()` (修改路径), `routes/experiment.py:edit_experiment()` POST, `routes/experiment.py:regenerate_experiment()`

- `delete(exp_id: str) -> bool`:
  - **作用**: 删除实验的 YAML 文件 (`filepath.unlink()`)
  - **被调用**: `ExperimentService.delete_with_log()` (lib/services/experiment.py:57)

- `count() -> int`:
  - **作用**: 返回 `EXP-*.yaml` 文件的总数
  - **被调用**: `GlobalContextStore.build_global_summary()` (lib/repositories/yaml_thread.py:307) — L0 摘要中显示 "当前实验库共 N 条实验"
