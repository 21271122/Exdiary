# lib/repositories/yaml_analysis.py — 说明文档

## 文件作用摘要

跨实验分析报告的 YAML 文件系统仓储实现。每个分析以 `ANAL-YYYY-NNN.yaml` 格式存储在 `experiments/_analysis_history/` 目录下。实现 `AbstractAnalysisRepository` 接口。被 `app.py` 创建并注入到 `flask.g`。

---

## 代码块详细说明

### 类

#### `YamlAnalysisRepository` (AbstractAnalysisRepository)
- **作用**: 基于 YAML 文件系统的分析报告持久化
- **构造参数**: `path: str` — 分析数据目录路径（通常为 `experiments/_analysis_history/`），自动创建
- **实例属性**: `self.path: Path`

##### 方法

- `next_id() -> str`:
  - **作用**: 扫描目录中已存在的 `ANAL-{year}-*.yaml` 文件，生成下一个增量 ID
  - **输出**: `ANAL-{year}-{max_n+1:03d}`
  - **被调用**: `AnalysisService.run_analysis()` (lib/services/analysis.py:20) — 通过 `self.analysis_repo.save(...)` 前自动获取 ID（save 内部调用 `self.next_id()`）

- `save(analysis: dict) -> str`:
  - **作用**: 将分析 dict 写入 `{anal_id}.yaml` 文件。自动设置 `analysis["id"]`
  - **输入**: `analysis` — 分析数据 dict，含 timestamp/question/selected_ids/analysis
  - **输出**: 分析 ID 字符串
  - **被调用**:
    - `AnalysisService.run_analysis()` line 20: `self.analysis_repo.save({...})`
    - `ToolExecutor._generate_analysis()` 回退路径 line 351: `self.analysis_store.save({...})`
    - `ToolExecutor._modify_analysis()` lines 419,433,469: `self.analysis_store.save(a)` 修改后保存

- `load(aid: str) -> dict | None`:
  - **作用**: 从 YAML 文件加载单个分析报告
  - **被调用**:
    - `routes/api_analysis.py:12` — `api_analysis_detail()` 加载详情
    - `routes/pages.py:17` — `view_analysis()` 加载报告页面
    - `routes/api_child.py:74` — `api_analysis_chat()` 加载分析数据给子 Agent
    - `ToolExecutor._modify_analysis()` line 407: `self.analysis_store.load(anal_id)` 修改前加载

- `list_all() -> list[dict]`:
  - **作用**: 列出全部分析报告的完整数据，按文件名倒序
  - **被调用**: `routes/api_analysis.py:7` — `api_analysis_history()` 返回 JSON 列表

- `delete(aid: str) -> bool`:
  - **作用**: 删除分析报告的 YAML 文件
  - **被调用**: `routes/api_analysis.py:21` — `api_analysis_delete()`
