# lib/analyzer.py — 说明文档

## 文件作用摘要

跨实验分析的纯函数模块。提供了直接调用 LLM 执行分析的最简路径（不含持久化、不含关联回写）。在当前架构中定位为 **运行时兜底**：当 `AnalysisService.run_analysis()` 因 `analyze_llm` 不可用（如 API Key 未配置）而失败时，由 `ToolExecutor._generate_analysis()` 的 except 块中的 fallback 逻辑调用本模块，使用 Agent 自身的 `loop.llm` 完成分析。

被 `lib/agent_v2.py` 在两处 runtime import 使用。

---

## 代码块详细说明

### 模块级导入

- `from lib.core.prompts import ANALYSIS_SYSTEM_PROMPT` — 导入分析专用的三区域输出格式 system prompt

### 模块级函数

#### `analyze_experiments(summary_text: str, question: str, llm_client) -> str`
- **作用**: 对实验摘要数据执行 LLM 分析（最简路径：不负责持久化和关联回写，这些由调用方处理）
- **输入**:
  - `summary_text: str` — 由 `YamlExperimentRepository.summarize_all()` 生成的实验摘要文本
  - `question: str` — 用户的自然语言分析问题
  - `llm_client` — 必须有 `analyze()` 方法的 LLM 实例（通常为 Agent 的 `loop.llm`，即 `deepseek-v4-flash`）
- **输出**: LLM 生成的三区域分析报告（事实呈现/发现提示/值得思考的问题），Markdown 格式
- **实现**: 构建 EXPERIMENT RECORDS + RESEARCHER'S QUESTION 的 user prompt → `llm_client.analyze(ANALYSIS_SYSTEM_PROMPT, user_prompt)`
- **定位**: 正常运行时不使用——分析优先走 `AnalysisService.run_analysis()`（使用专门的推理模型 `deepseek-v4-pro`）。本函数是 `AnalysisService` 不可用时的兜底路径

- **被调用情况** (全部在 `lib/agent_v2.py` 的 `ToolExecutor` 类中，runtime import):
  - `lib/agent_v2.py:347` — `ToolExecutor._generate_analysis()` 的 else 分支（当 `loop.analysis_svc` 为 None 时触发，如未配置 API Key）
  - `lib/agent_v2.py:436` — `ToolExecutor._modify_analysis()` 的 additional_refs 分支的 else 路径（同上场景）
