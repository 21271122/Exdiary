# lib/services/analysis.py — 说明文档

## 文件作用摘要

跨实验分析服务。吸收旧版 `lib/analyzer.py` 的 `analyze_experiments()` 功能。提供一站式分析流程：实验摘要 → LLM 分析（三区域报告）→ 保存到 AnalysisStore → 更新实验的双向关联。被 `app.py` 创建并注入到 `flask.g` 和 `AgentLoop`。使用 `deepseek-v4-pro`（推理增强模型）执行分析，与 Agent 对话所用的 `deepseek-v4-flash`（快速模型）分离职责。

---

## 代码块详细说明

### 类

#### `AnalysisService`
- **作用**: 跨实验 LLM 分析服务。封装完整的分析工作流：摘要生成 → 调用推理模型 → 持久化报告 → 回写实验关联
- **构造参数**:
  - `exp_repo: Any` — 实验仓储（YamlExperimentRepository），用于生成实验摘要
  - `analysis_repo: Any` — 分析报告仓储（YamlAnalysisRepository），用于持久化分析报告
  - `analyze_llm: Any` — 分析专用 LLM 客户端。在 `app.py:157` 创建时传入 `get_analyze_llm()`，使用 `deepseek-v4-pro`（推理增强，不支持 function calling）。若 API Key 未配置则传入 None，此时 `run_analysis()` 会因无法调用 LLM 而失败，由调用方的 `try-except` 兜底，触发 `lib/analyzer.analyze_experiments()` fallback（使用 Agent 的 `loop.llm`）
- **被注入**: `app.py:157` → `g.analysis_svc`；同时注入到 `AgentLoop.analysis_svc`

##### 公开方法

- `run_analysis(query: str, refs: list[str]) -> dict[str, Any]`:
  - **作用**: 执行完整分析流程（一站式：摘要 → LLM → 存储 → 关联）
  - **输入**: `query` — 用户的分析问题, `refs` — 参与分析的 EXP ID 列表（至少 2 个）
  - **输出**: `{anal_id: str, title: str, refs: list[str], analysis: str}`
  - **流程**:
    1. `self.exp_repo.summarize_all(exp_ids=refs)` → 生成实验摘要文本
    2. `self._analyze_experiments(summary, query)` → 调用推理模型执行分析
    3. `self.analysis_repo.save({timestamp, question, selected_ids, analysis})` → 写入分析报告文件
    4. 遍历 refs → 更新每个实验的 `analyzed_in` 字段 + `self.exp_repo.save(exp)` 保存双向关联
    5. 从 query/analysis 文本中推断 title（取前 40-60 字符）
  - **被调用**:
    - `ToolExecutor._generate_analysis()` main path (lib/agent_v2.py:340-344) — Agent 在 analyze 线程中调用 `generate_analysis` 工具时，优先通过本服务执行分析
    - `ToolExecutor._modify_analysis()` additional_refs path (lib/agent_v2.py:429-433) — 修改分析报告时追加实验、重新分析
    - 若 `analyze_llm` 为 None（未配置 API Key）导致本方法失败，调用方 `try-except` 捕获异常后触发 fallback 路径：`lib/analyzer.analyze_experiments()` 使用 Agent 的 `loop.llm` 直接执行分析

##### 私有方法

- `_analyze_experiments(summary_text: str, question: str) -> str`:
  - **作用**: 执行 LLM 分析调用（构建 prompt + 调推理模型 LLM.analyze）
  - **实现**: `self.analyze_llm.analyze(ANALYSIS_SYSTEM_PROMPT, user_prompt)`
  - **被调用**: 仅在 `self.run_analysis()` 内部调用
