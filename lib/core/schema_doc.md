# lib/core/schema.py — 说明文档

## 文件作用摘要

实验数据结构的 Schema 定义模块。包含 JSON Schema（用于 OpenAI function calling 的 structured output）、16 字段默认上下文（用于 Agent record 模式的初始状态）、以及受控标签词汇表。纯数据常量，零依赖。被 `lib/agent_v2.py`、`lib/parser.py`、`lib/services/extraction.py` 导入使用。

---

## 代码块详细说明

### `EXPERIMENT_SCHEMA: dict[str, Any]`
- **作用**: OpenAI function calling 格式的 JSON Schema 定义，描述实验记录的完整结构（17 个顶层字段）。用于 `LLMClient.structured_extract()` 中的 `output_schema` 参数
- **字段定义** (完整):
  - `id` (string), `title` (string), `date` (string), `experimenter` (string)
  - `status` (string, enum: planned/running/done/failed/repeated)
  - `tags` (array of string)
  - `purpose` (string)
  - `materials` (array of object → `{name(required), purity, vendor, amount, notes}`)
  - `equipment` (array of object → `{device(required), model, location}`)
  - `experimental_plan` (array of object → `{group, condition, expected}`)
  - `sop` (array of string)
  - `process_parameters` (array of object → `{step, parameter, setpoint, actual, deviation}`)
  - `observations` (object → `{no_anomalies(required), items}`)
  - `characterization` (array of object → `{method, sample_id, preparation, submission_date, data_path}`)
  - `results` (object → `{qualitative, key_data: [{metric, value, comparison, change}], figures: [{figure, path, conclusion}]}`)
  - `conclusion` (string), `next_steps` (array of string)
- **required**: `["id", "title", "purpose", "sop", "observations", "conclusion"]`
- **被调用情况**:
  - `lib/services/extraction.py:8` — `from lib.core.schema import EXPERIMENT_SCHEMA`, 在 `ExtractionService.parse_notes()` 中传给 `self.extract_llm.structured_extract(..., output_schema=EXPERIMENT_SCHEMA)`
  - `lib/parser.py:5` — `from lib.core.schema import EXPERIMENT_SCHEMA`, 在 `parse_notes()` 中传给 `llm_client.structured_extract(..., output_schema=EXPERIMENT_SCHEMA)`

### `DEFAULT_CONTEXT: dict[str, Any]`
- **作用**: Agent record 模式下的初始 Schema 上下文（16 字段全为空/默认值，不含 `id`——id 由 Repository 自动分配）。`AgentLoop._enter_record_mode()` 时深拷贝此字典
- **默认值**:
  - `title/date/experimenter/purpose/conclusion` = ""
  - `status` = "planned"
  - `tags/materials/equipment/experimental_plan/sop/process_parameters/characterization/next_steps` = []
  - `observations` = `{"no_anomalies": True, "items": []}`
  - `results` = `{"qualitative": "", "key_data": [], "figures": []}`
- **被调用情况**:
  - `lib/agent_v2.py:23` — `from lib.core.schema import DEFAULT_CONTEXT`, 在 `AgentLoop._enter_record_mode()` (line 1040) 中 `self._schema_context = deepcopy(DEFAULT_CONTEXT)`
  - `tests/test_agent_v2.py:20` — `from lib.core.schema import DEFAULT_CONTEXT`, 在多个测试类中作为测试数据基础模板
  - `tests/conftest.py:13` — `from lib.core.schema import DEFAULT_CONTEXT`（导入但未被本文件实际使用，为死导入）
  - **注意**: `EXPERIMENT_SCHEMA` 和 `DEFAULT_CONTEXT` 的字段结构高度一致，但 EXPERIMENT_SCHEMA 多一个 `id` 字段（17 vs 16 字段），且两者的 `required` 列表不同（Schema 要求 id/title/purpose/sop/observations/conclusion，DEFAULT_CONTEXT 无 required 概念）

### `TAG_VOCABULARY: list[str]`
- **作用**: 受控标签词汇表（27 个材料科学标准英文标签），为 LLM 提取时的标签建议列表
- **取值**: `synthesis, characterization, photocatalysis, electrochemistry, sintering, ball-milling, thin-film, XRD, SEM, TEM, mechanical-testing, thermal-analysis, DFT, sol-gel, hydrothermal, co-precipitation, calcination, doping, coating, corrosion, battery, ceramic, polymer, composite, nano, perovskite-solar, spin-coating`
- **被调用情况**: **无任何模块通过 Python import 使用此常量**。`EXTRACTION_SYSTEM_PROMPT` (lib/services/extraction.py:47) 的规则 4 中硬编码了一份类似的 25 标签列表（缺少 `perovskite-solar` 和 `spin-coating`），为独立维护的文本副本，与 TAG_VOCABULARY (27项) 存在差异。此常量可能为统一标签管理的预留接口。
