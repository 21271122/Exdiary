# lib/core/experiment_types.py — 说明文档

## 文件作用摘要

实验类型配置模块。定义 8 种材料科学实验类型 × 3 级优先级的追问字段清单 `PRIORITY_MAP` + 1 个兜底的 `other` 类型。被 `lib/core/prompts.py` 导入用于动态生成 SYSTEM_PROMPT 中的追问优先级段落。从旧版 `lib/services/agent.py` 迁出。

---

## 代码块详细说明

### `PRIORITY_MAP: dict[str, dict[str, list[str]]]`
- **作用**: 定义每种实验类型的关键参数优先级，指导 Agent 在 record 模式下优先追问 P1 字段
- **结构**: `{实验类型key: {"priority_1": [3个高优字段], "priority_2": [2-3个中优字段], "priority_3": [2-3个低优字段]}}`
- **类型清单** (8 种 + 1 兜底):

| 类型 key | P1 追问项 | P2 追问项 | P3 追问项 |
|----------|----------|----------|----------|
| `photocatalysis` | 催化剂名称和纯度、目标污染物和浓度、光源类型和功率 | 催化剂负载量、降解时间、表征手段 | 基板类型、煅烧条件、溶液pH |
| `hydrothermal` | 前驱体名称和用量、反应温度、反应时间 | 溶剂类型和用量、目标产物、填充度 | 升温速率、pH值、表面活性剂 |
| `sol-gel` | 前驱体名称、溶剂、水解抑制剂 | 陈化温度和时间、干燥条件、煅烧温度 | 滴加速率、催化剂用量、研磨条件 |
| `spin-coating` | 薄膜材料名称、基底类型、旋涂转速 | 前驱体浓度和溶剂、退火温度和时间 | 旋涂层数、预处理方式、气氛 |
| `ball-milling` | 原料名称和用量、球料比、球磨时间 | 转速、球磨罐材质、磨球尺寸 | 过程控制剂、气氛保护、停机间隔 |
| `electrochemistry` | 活性材料名称、电解液体系、测试类型 | 电压窗口、对电极/参比电极、活性物负载量 | 导电剂和粘结剂配比、测试温度、扫速 |
| `xrd` | 样品名称和形态、扫描范围、靶材类型 | 管电压/管电流、扫描步长、物相检索数据库 | 仪器型号、制样方式、晶粒尺寸计算 |
| `perovskite-solar` | 钙钛矿组分和配比、ETL/HTL材料、退火温度和时间 | 旋涂参数、反溶剂、电极材料和厚度 | 有效面积、测试光源条件、器件结构 |
| `other` | 实验目的是什么、使用了哪些关键材料 | 核心操作步骤、主要参数有哪些 | 表征手段、预期结果 |

- **被调用情况**:
  - `lib/core/prompts.py:7` — `from lib.core.experiment_types import PRIORITY_MAP`, 在 `build_system_prompt()` 间接调用 `_build_priority_prompt(PRIORITY_MAP)` — 生成 SYSTEM_PROMPT 中的动态清单
  - `lib/agent_v2.py` line 1403 — `AgentLoop._core_fields_filled()` 中硬编码了 `CORE_BY_TYPE` 字典（8 种类型 → 必须填充的字段列表），与 PRIORITY_MAP 的 P1 字段有关联但并非直接 import 使用

> **注意**: `AgentLoop._core_fields_filled()` 中的 `CORE_BY_TYPE` 常量是独立维护的（非从 PRIORITY_MAP 派生），两者需保持大致一致，目前存在细微差异（如 hydrothermal 的 P1 不含 sop 但 CORE_BY_TYPE 包含 sop）。
