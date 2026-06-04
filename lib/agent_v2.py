"""
Exdiary Agent v2 — 基于 Tool Calling 的对话式实验记录系统

LLM 自主决策流程，Python 仅执行工具和注入 Schema 状态。
"""

import json, os, re, sys, traceback
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from lib.logger import get_logger

# ============================================================================
# Step 1.1: Tool Definitions (OpenAI function calling format)
# ============================================================================

TOOL_LOAD_REFERENCE = {
    "type": "function",
    "function": {
        "name": "load_reference",
        "description": (
            "加载引用实验的完整数据（SOP、参数、结果、结论等）。"
            "仅接受 EXP ID 格式（如 EXP-2026-003）。"
            "用户说'跟003一样'时，请自行补全为 EXP-2026-003 后调用。"
            "模糊描述（如'上次的ZnO实验'）请用 search_experiments。"
            "结果写回messages，你可据此判断哪些字段可直接继承。"
            "已加载过的实验无需重复调用——数据已在 messages 中。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "实验编号（EXP-YYYY-NNN 格式）。不是模糊描述。",
                }
            },
            "required": ["refs"],
        },
    },
}

TOOL_SEARCH_EXPERIMENTS = {
    "type": "function",
    "function": {
        "name": "search_experiments",
        "description": (
            "在历史实验库中搜索。处理各种自然语言描述：\n"
            "- 时间指代：'上周的''最近的''上个月的'\n"
            "- 人员指代：'老张做的''我上次做的'\n"
            "- 状态指代：'失败的那个''成功的那个'\n"
            "- 材料指代：'做ZnO的那个''用了P25的'\n"
            "- 性能指代：'降解率最高的'\n"
            "返回候选列表。如用户确认候选，再调 load_reference 加载完整数据。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词或自然语言描述",
                }
            },
            "required": ["query"],
        },
    },
}

TOOL_UPDATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_schema",
        "description": (
            "将本轮确认的信息写入Schema。写入后系统自动更新messages中的Schema状态摘要。"
            "注意: messages中已有当前Schema状态和引用实验数据，写入前请自行比对——"
            "新值与已有数据矛盾时，先向用户求证再写入，不要写入矛盾值后又覆盖。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "date": {"type": "string"},
                        "experimenter": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["planned", "running", "done", "failed", "repeated"],
                        },
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "purpose": {"type": "string"},
                        "materials": {"type": "array"},
                        "equipment": {"type": "array"},
                        "experimental_plan": {"type": "array"},
                        "sop": {"type": "array", "items": {"type": "string"}},
                        "process_parameters": {"type": "array"},
                        "observations": {"type": "object"},
                        "characterization": {"type": "array"},
                        "results": {"type": "object"},
                        "conclusion": {"type": "string"},
                        "next_steps": {"type": "array", "items": {"type": "string"}},
                    },
                    "description": "要更新的字段。增量更新——只传变化的。空列表[]或空对象{}表示清空。",
                },
                "round_summary": {
                    "type": "string",
                    "description": "一句话描述本轮收集/确认了哪些信息（用于日志）",
                },
            },
            "required": ["fields"],
        },
    },
}

TOOL_ASK_USER = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "向用户提问。一次最多3个问题。问题应具体、可回答。"
            "依据: 看messages中Schema状态的缺失字段 + system prompt中的优先级清单，"
            "自己决定问什么、问几个。缺失的都是补充字段时可跳过直接结束。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 3,
                }
            },
            "required": ["questions"],
        },
    },
}

TOOL_GENERATE_RECORD = {
    "type": "function",
    "function": {
        "name": "generate_record",
        "description": (
            "生成实验记录草稿。当你判断实验信息已收集完毕、核心字段（目的、"
            "材料、步骤/参数、结果/结论）已填充时调用。调用后系统生成结构化"
            "记录并在前端展示预览面板，用户确认后保存。不要只输出纯文本等待——"
            "调用本工具是生成记录的唯一途径。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

TOOL_START_RECORD_THREAD = {
    "type": "function",
    "function": {
        "name": "start_record_thread",
        "description": (
            "开始一个实验记录线程。当用户明确表达要记录新实验时调用"
            "（如'记录新实验''帮我记一下''做了个...'等）。"
            "调用后标记对话进入记录模式，后续对话归属该线程。"
            "不要在查询、修改、闲聊时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

TOOL_END_THREAD = {
    "type": "function",
    "function": {
        "name": "end_thread",
        "description": (
            "结束当前对话线程（record 或 analyze）。"
            "用户明确表示取消、结束、不继续时调用"
            "（如'算了''不记了''取消''结束线程'等）。"
            "调用后系统归档线程，自动清理状态，对话回到自由模式。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

TOOL_START_ANALYZE_THREAD = {
    "type": "function",
    "function": {
        "name": "start_analyze_thread",
        "description": (
            "开始一个跨实验分析线程。当用户明确表达要分析实验数据时调用"
            "（如'分析一下''帮我看看这些实验''对比钙钛矿PCE'等）。"
            "调用后进入分析模式，后续对话归属该线程。"
            "不要在记录实验、查询、闲聊时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

TOOL_SELECT_EXPERIMENTS = {
    "type": "function",
    "function": {
        "name": "select_experiments",
        "description": (
            "向用户展示实验选择面板，让用户勾选参与分析的实验。"
            "当用户说'筛选''过滤''选实验''挑实验'等，或你需要让用户从多个实验中做选择时，"
            "必须调用本工具——不要用纯文本表格代替。"
            "传入 candidates 作为候选列表（通常来自 search_experiments 或 list_experiments）。"
            "可传入 preselected 预勾选已确定的实验。"
            "用户勾选确认后，选中的 EXP ID 列表作为 tool_result 回传。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array", "items": {"type": "object"},
                    "description": "候选实验列表，每项含 id/title/date/tags",
                },
                "preselected": {
                    "type": "array", "items": {"type": "string"},
                    "description": "预勾选的 EXP ID 列表",
                },
                "title": {
                    "type": "string",
                    "description": "面板标题，如'选择要分析的钙钛矿实验'",
                },
            },
            "required": ["candidates"],
        },
    },
}

TOOL_GENERATE_ANALYSIS = {
    "type": "function",
    "function": {
        "name": "generate_analysis",
        "description": (
            "执行跨实验分析并归档。当实验已选定、需求明确时调用。"
            "分析报告直接存储到本地分析历史，不在对话中显示全文。"
            "调用后自动结束分析线程，回到自由模式。"
            "这是生成分析报告的唯一途径。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "分析问题，如'对比钙钛矿PCE趋势'",
                },
                "refs": {
                    "type": "array", "items": {"type": "string"},
                    "description": "参与分析的 EXP ID 列表，至少2个",
                },
            },
            "required": ["query", "refs"],
        },
    },
}

TOOL_MODIFY_ANALYSIS = {
    "type": "function",
    "function": {
        "name": "modify_analysis",
        "description": (
            "修改分析报告。支持三种操作模式：\n"
            "1. 修改文字：changes 传入新的完整 Markdown 文本，直接覆盖\n"
            "2. 新增实验并重新分析：additional_refs 传入额外 EXP ID，"
            "合并原有实验后重新运行分析\n"
            "3. 新增分析维度：additional_query 传入补充问题，"
            "在原报告基础上追加分析\n"
            "所有修改自动保存到 AnalysisStore。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "changes": {"type": "string", "description": "修改后的完整 Markdown 报告"},
                "additional_refs": {"type": "array", "items": {"type": "string"},
                                   "description": "额外纳入分析的 EXP ID"},
                "additional_query": {"type": "string", "description": "补充的分析问题/维度"},
            },
        },
    },
}

TOOL_READ_UPDATE_LOG = {
    "type": "function",
    "function": {
        "name": "read_update_log",
        "description": "读取某个实验的更新日志。当需要确认字段是否被修改过时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "exp_id": {"type": "string", "description": "实验编号 EXP-YYYY-NNN"},
                "since": {"type": "string", "description": "可选，只返回此时间之后的更新"},
                "limit": {"type": "integer", "description": "最多返回几条，默认 5"},
            },
            "required": ["exp_id"],
        },
    },
}

TOOL_MODIFY_EXPERIMENT = {
    "type": "function",
    "function": {
        "name": "modify_experiment",
        "description": (
            "修改实验字段。changes 中未出现的字段保持磁盘现有值不变（增量语义）。"
            "嵌套数组字段的值是完整的数组替换——请先通过 load_reference 获取当前完整数组，"
            "修改目标条目后传回完整数组。所有修改自动写入更新日志。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "refs": {"type": "array", "items": {"type": "string"},
                         "description": "要修改的实验编号列表"},
                "changes": {
                    "type": "object",
                    "description": "扁平字段名→新值映射。简单字段覆盖，数组字段完整替换。",
                },
                "description": {
                    "type": "string",
                    "description": "自然语言修改描述。与 changes 二选一。",
                },
            },
            "required": ["refs"],
        },
    },
}

TOOL_MANAGE_COLLECTION = {
    "type": "function",
    "function": {
        "name": "manage_collection",
        "description": "管理实验的收藏和置顶。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["pin", "unpin", "favorite", "unfavorite"]},
                "refs": {"type": "array", "items": {"type": "string"}},
                "collection": {"type": "string", "description": "收藏夹名称，默认'默认收藏夹'"},
            },
            "required": ["action", "refs"],
        },
    },
}

TOOL_QUERY_EXPERIMENT = {
    "type": "function",
    "function": {
        "name": "query_experiment",
        "description": "回答实验参数查询。用户提问模糊时可能需要多轮确认。",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "用户的问题"},
                "refs": {"type": "array", "items": {"type": "string"},
                         "description": "要查询的实验编号列表"},
            },
            "required": ["question", "refs"],
        },
    },
}

TOOL_LIST_EXPERIMENTS = {
    "type": "function",
    "function": {
        "name": "list_experiments",
        "description": "按条件筛选实验列表。确定性执行，不调 LLM。",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string",
                           "enum": ["planned", "running", "done", "failed", "repeated"]},
                "tags": {"type": "array", "items": {"type": "string"}},
                "experimenter": {"type": "string"},
                "since": {"type": "string", "description": "起始日期 YYYY-MM-DD"},
            },
        },
    },
}

TOOLS_OPENAI_FORMAT = [
    TOOL_LOAD_REFERENCE,
    TOOL_SEARCH_EXPERIMENTS,
    TOOL_START_RECORD_THREAD,
    TOOL_UPDATE_SCHEMA,
    TOOL_ASK_USER,
    TOOL_GENERATE_RECORD,
    TOOL_READ_UPDATE_LOG,
    TOOL_MODIFY_EXPERIMENT,
    TOOL_MANAGE_COLLECTION,
    TOOL_QUERY_EXPERIMENT,
    TOOL_LIST_EXPERIMENTS,
    TOOL_END_THREAD,
    TOOL_START_ANALYZE_THREAD,
    TOOL_SELECT_EXPERIMENTS,
    TOOL_GENERATE_ANALYSIS,
    TOOL_MODIFY_ANALYSIS,
]

# ============================================================================
# Step 1.7: SYSTEM_PROMPT
# ============================================================================

SYSTEM_PROMPT = """\
你是 Exdiary 实验记录助手。你与用户对话，逐步收集实验信息，
最终生成完整的结构化实验记录。

## 对话模式

你有三种工作模式。**当前模式由每轮对话最末尾的 [系统状态] 消息严格确定——你必须以此消息为准，而非依赖对话记忆或用户陈述。**

### 自由模式（末尾消息 = "[系统状态] 自由模式"）
你可回答查询、管理收藏、闲聊。
要进入 record 模式 → 调用 start_record_thread。
要进入 analyze 模式 → 调用 start_analyze_thread。

### record 模式（末尾消息 = "[系统状态] record 线程进行中"）
你正在收集实验信息。可用 record 专用工具：start_record_thread、
update_schema、ask_user、generate_record。
目标：generate_record。

### analyze 模式（末尾消息 = "[系统状态] analyze 线程进行中"）
你正在进行跨实验分析。可用 analyze 专用工具：
- start_analyze_thread: 开启分析线程
- select_experiments: 展示实验选择面板（必须调用，不要用纯文本代替）
- ask_user: 了解分析需求
- generate_analysis: 执行分析并归档，报告自动包含 事实呈现/发现提示/值得思考的问题

工作方式：
1. search_experiments 或 list_experiments 缩小实验范围
2. 必须调用 select_experiments 展示选择面板
3. 用户勾选确认后，load_reference 加载数据
4. **与用户讨论分析角度。** ask_user 了解：
   - 用户最关心什么问题？
   - 有什么具体困惑或假设想验证？
   不要假设用户想要标准报告。根据需求定制分析框架。
5. 需求明确 → generate_analysis 执行分析并归档，调用后线程自动结束

注意：start_record_thread、update_schema、generate_record、modify_experiment
在此模式中不可用。

## 工作方式

你有多个工具（根据当前模式过滤可用工具）。在 record 模式下:

0. 如果用户明确表示要记录新实验（"记录新实验""帮我记""做了个..."等）
   → 先调用 start_record_thread 开启实验记录线程。

1. 如果用户引用了历史实验：
   - 用户给了明确编号（如"EXP-003""003"）→ 直接拼成 'EXP-2026-xxx' 调 load_reference
   - 用户用自然语言描述（如"上周的ZnO实验""老张做钙钛矿那次"）→ 调 search_experiments
   - 搜索结果不明确时，把候选展示给用户确认，不要盲猜直接加载
   - load_reference 只接受 EXP ID 格式，不接受自然语言。调用前请自行将缩写补全为完整编号
   - 加载过的实验无需重复调用 load_reference（数据已在 messages 中）

2. 因为每轮可能有多个对话来回，对于用户提供的信息，调用 update_schema 写入。
   如加载了引用且用户说"完全一样"，将引用实验的匹配字段整批写入。
   如用户说"xxx一样但改了yyy"，继承未改动的字段，改动字段等用户提供。

3. 写入后系统自动更新 Schema 状态到 messages 中。
   根据 Schema 状态判断: 如果关键字段还有缺失 → 调 ask_user 追问。
   追问看两点: Schema 状态中的缺失字段 + 各类实验的优先级(见下方)。
   自己决定问什么、问几个。不要一次问太多。

4. 如果 Schema 状态显示关键字段基本齐备 → **调用 generate_record 工具**
   来生成最终记录。调用这个工具是生成实验记录的唯一途径。
   不要只输出纯文本等待系统自动处理——你必须主动调用工具。

4a. 如果用户主动说"够了""直接生成""就这样"等 → 判断核心字段
   是否已填。已填则调用 generate_record 生成记录。未填则
   追问最后1-2个关键项，不要盲目生成残缺记录。

## 消息格式说明

以 "[系统内部]" 开头的系统消息是框架基础设施日志
（如线程起止标记），不反映你当前的行为模式。
你的当前模式由每轮对话最末尾的 "[系统状态]" 消息严格确定——必须以此为准。三种取值：
- "[系统状态] 自由模式"
- "[系统状态] record 线程进行中"
- "[系统状态] analyze 线程进行中"

## 工具清单

### 通用工具（所有模式可用）
- load_reference: 加载引用实验的完整数据。仅接受 EXP ID。加载过的不重复调用。
- search_experiments: 语义搜索历史实验（模糊描述如"上次的ZnO实验"）。
- query_experiment: 查询实验参数（如"003的退火温度是多少"）。
- list_experiments: 按条件筛选实验列表。
- modify_experiment: 修改已存在实验的字段。需先 load_reference 获取当前值。
- read_update_log: 查看实验的修改历史。
- manage_collection: 管理实验的收藏和置顶（最多置顶3个）。
- end_thread: 结束当前对话线程（record 或 analyze）。用户说"算了""取消""结束线程"时调用。

### record 专用工具（仅 "[系统状态] record 线程进行中" 时可用）
- start_record_thread: 开启实验记录线程。
- update_schema: 将确认的信息写入 Schema。增量更新，只传变化的字段。
- ask_user: 向用户提问，一次最多3个。
- generate_record: 生成结构化实验记录草稿。调用此工具是生成记录的唯一途径。

### analyze 专用工具
- start_analyze_thread: 开启跨实验分析线程。
- select_experiments: 向用户展示实验选择面板，让用户勾选参与分析的实验。
- generate_analysis: 执行分析并归档。分析报告存储到本地，返回标题和摘要。调用后自动结束线程。

## 实验 Schema（16 字段）

最终要填充的字段如下。record 模式下，Schema 状态会出现在每轮对话末尾，实时反映哪些已填、哪些缺失:

1.  title               — 实验标题
2.  date                — 日期 (YYYY-MM-DD)
3.  experimenter        — 实验者
4.  status              — planned|running|done|failed|repeated
5.  tags                — 受控词汇(英文): photocatalysis, hydrothermal, sol-gel,
                          spin-coating, ball-milling, electrochemistry, xrd,
                          perovskite-solar, thin-film, calcination, doping,
                          coating, battery, ceramic, polymer, composite, nano,
                          synthesis, characterization
6.  purpose             — 实验目的/科学问题
7.  materials           — [{name, purity, vendor, amount, notes}]
8.  equipment           — [{device, model, location}]
9.  experimental_plan   — [{group, condition, expected}]
10. sop                 — 操作步骤 [字符串数组]
11. process_parameters  — [{step, parameter, setpoint, actual, deviation}]
12. observations        — {no_anomalies: bool, items: [字符串]}
13. characterization    — [{method, sample_id, preparation, ...}]
14. results             — {qualitative: 字符串, key_data: [{metric, value, ...}]}
15. conclusion          — 结论
16. next_steps          — 下一步 [字符串数组]

## 各实验类型关键参数优先级

photocatalysis: P1 催化剂名称和纯度、目标污染物和浓度、光源类型和功率
                P2 催化剂负载量、降解时间、表征手段
                P3 基板类型、煅烧条件、溶液pH

hydrothermal: P1 前驱体名称和用量、反应温度、反应时间
              P2 溶剂类型和用量、目标产物、填充度
              P3 升温速率、pH值、表面活性剂

sol-gel: P1 前驱体名称、溶剂、水解抑制剂
         P2 陈化温度和时间、干燥条件、煅烧温度
         P3 滴加速率、催化剂用量、研磨条件

spin-coating: P1 薄膜材料名称、基底类型、旋涂转速
              P2 前驱体浓度和溶剂、退火温度和时间
              P3 旋涂层数、预处理方式、气氛

ball-milling: P1 原料名称和用量、球料比、球磨时间
              P2 转速、球磨罐材质、磨球尺寸
              P3 过程控制剂、气氛保护、停机间隔

electrochemistry: P1 活性材料名称、电解液体系、测试类型
                  P2 电压窗口、对电极/参比电极、活性物负载量
                  P3 导电剂和粘结剂配比、测试温度、扫速

xrd: P1 样品名称和形态、扫描范围、靶材类型
     P2 管电压/管电流、扫描步长、物相检索数据库
     P3 仪器型号、制样方式、晶粒尺寸计算

perovskite-solar: P1 钙钛矿组分和配比、ETL/HTL材料、退火温度和时间
                  P2 旋涂参数、反溶剂、电极材料和厚度
                  P3 有效面积、测试光源条件、器件结构

## 矛盾检测

写入 Schema 前，自行比对 messages 中的已有信息:
- Schema 状态中的已有值 vs 用户本轮提供的新值（是否自矛盾）
- 已加载引用实验(tool 返回的数据)中的记录 vs 用户本轮的说法（是否与引用矛盾）

检测到矛盾时，先通过 ask_user 或自然语言向用户求证，
确认后再调 update_schema 写入。不要写入矛盾值后又覆盖。
不要自行修正矛盾。

## 取消与结束线程

如果用户明确表示不想继续当前操作（"算了""不记了""取消""结束线程"等），
**调用 end_thread 工具**来结束当前线程，然后回复确认。
不要只输出纯文本——你必须主动调用 end_thread。

注意：generate_record 生成记录后也会自动结束 record 线程，无需额外调用 end_thread。

## 事实获取规则

对话历史中关于实验参数的陈述可能是过时的（实验可能被子 Agent 或手动编辑修改过）。
当回答关于某个实验的具体数据时，遵循以下优先级：

1. 如果对话中出现了 [EXP-xxx 已被修改] 的标记 → 必须调用 load_reference 重新加载
2. 如果你本轮刚通过 modify_experiment 自己修改了该实验 → 可以信任自己的操作
3. 其他情况 → 优先从 load_reference 的结果中获取，而非依赖对话记忆

回答数据性问题时注明来源：
  "EXP-015 当前退火温度是 200°C（已从文件确认）"

## 行为准则

- 中文回复，友好、具体
- 不要编造任何用户未提及的信息
- 用户说"跟EXP-xxx一样""完全一致"时，通过 update_schema 把引用实验数据写入
- 一次追问不超过3项，优先问高优先级的缺失字段"""

# ============================================================================
# 默认上下文
# ============================================================================

DEFAULT_CONTEXT: dict = {
    "title": "",
    "date": "",
    "experimenter": "",
    "status": "planned",
    "tags": [],
    "purpose": "",
    "materials": [],
    "equipment": [],
    "experimental_plan": [],
    "sop": [],
    "process_parameters": [],
    "observations": {"no_anomalies": True, "items": []},
    "characterization": [],
    "results": {"qualitative": "", "key_data": [], "figures": []},
    "conclusion": "",
    "next_steps": [],
}

# ============================================================================
# 辅助函数
# ============================================================================

def merge_context(context: dict, fields: dict) -> dict:
    """增量合并。简单字段覆盖；数组追加去重；嵌套对象递归合并。"""
    for key, value in fields.items():
        if key not in context:
            continue
        existing = context[key]
        if isinstance(existing, list) and isinstance(value, list):
            if not value:
                context[key] = []
            else:
                for item in value:
                    if isinstance(item, str) and item in existing:
                        continue
                    existing.append(item)
        elif isinstance(existing, dict) and isinstance(value, dict):
            if not value:
                context[key] = {}
            else:
                for sk, sv in value.items():
                    if isinstance(existing.get(sk), list) and isinstance(sv, list):
                        for i in sv:
                            if i not in existing[sk]:
                                existing[sk].append(i)
                    elif sv not in (None, ""):
                        existing[sk] = sv
        else:
            context[key] = value
    return context


def _is_filled(val) -> bool:
    """检查单个字段是否有值"""
    if val is None:
        return False
    if isinstance(val, list):
        return len(val) > 0
    if isinstance(val, dict):
        return any(v for v in val.values() if v)
    if isinstance(val, str):
        return val.strip() != ""
    return bool(val)


def _brief(val) -> str:
    """字段值的简短描述"""
    if isinstance(val, list):
        return f"{len(val)}项" if val else "空"
    if isinstance(val, dict):
        has = sum(1 for v in val.values() if v)
        return f"{has}子字段" if has else "空"
    if isinstance(val, str):
        return val[:15] + ("..." if len(val) > 15 else "")
    return "有" if val else "空"


def _fallback_preview(loop: "AgentLoop") -> dict:
    """确定性回退：从 context 直接构造预览数据，不调 LLM"""
    ctx = loop._schema_context or {}
    return {
        "id": loop.store.next_id(),
        "title": ctx.get("title", ""),
        "purpose": ctx.get("purpose", ""),
        "materials": ctx.get("materials", []),
        "sop": ctx.get("sop", []),
        "process_parameters": ctx.get("process_parameters", []),
        "observations": ctx.get("observations", {"no_anomalies": True, "items": []}),
        "results": ctx.get("results", {}),
        "characterization": ctx.get("characterization", []),
        "equipment": ctx.get("equipment", []),
        "conclusion": ctx.get("conclusion", ""),
        "next_steps": ctx.get("next_steps", []),
        "tags": ctx.get("tags", []),
        "status": ctx.get("status", "planned"),
        "date": ctx.get("date", ""),
        "experimenter": ctx.get("experimenter", ""),
        "original_notes": "",
        "references": list(loop.references),
    }


# ============================================================================
# 工具日志摘要
# ============================================================================

def _tool_log_summary(name: str, args: dict, result: dict) -> dict:
    """从工具名称、参数和结果中提取关键信息用于日志。"""
    kw = {}
    if name == "load_reference":
        kw["refs"] = args.get("refs", [])
        loaded = result.get("loaded", {}) if isinstance(result, dict) else {}
        kw["loaded_count"] = sum(1 for v in loaded.values() if isinstance(v, dict) and "error" not in v)
    elif name == "search_experiments":
        kw["query"] = args.get("query", "")
        kw["hits"] = len(result.get("candidates", [])) if isinstance(result, dict) else 0
    elif name == "update_schema":
        kw["fields"] = list((args.get("fields") or {}).keys())
    elif name == "ask_user":
        kw["questions"] = len(args.get("questions", []))
    elif name == "generate_record":
        kw["preview_id"] = result.get("id", "")
    elif name == "modify_experiment":
        kw["refs"] = args.get("refs", [])
        kw["fields"] = list((args.get("changes") or {}).keys())
    elif name == "manage_collection":
        kw["action"] = args.get("action", "")
        kw["refs"] = args.get("refs", [])
    elif name == "query_experiment":
        kw["question"] = args.get("question", "")[:100]
        kw["refs"] = args.get("refs", [])
    elif name == "analyze":
        kw["query"] = args.get("query", "")[:100]
    elif name == "list_experiments":
        kw.update({k: v for k, v in args.items() if v})
    elif name == "read_update_log":
        kw["exp_id"] = args.get("exp_id", "")
    if "error" in result:
        kw["error"] = str(result.get("message", result["error"]))[:200]
    return kw


# ============================================================================
# Step 1.2: ToolExecutor
# ============================================================================

class ToolExecutor:
    """注册、校验、执行 LLM 调用的工具"""

    def __init__(self, store, update_log_store=None, favorites_store=None, analysis_store=None):
        self.store = store
        self.update_log_store = update_log_store
        self.favorites_store = favorites_store
        self.analysis_store = analysis_store
        self.registry = {
            "load_reference": self._load_reference,
            "search_experiments": self._search_experiments,
            "start_record_thread": self._start_record_thread,
            "update_schema": self._update_schema,
            "ask_user": self._ask_user,
            "generate_record": self._generate_record,
            "read_update_log": self._read_update_log,
            "modify_experiment": self._modify_experiment,
            "manage_collection": self._manage_collection,
            "query_experiment": self._query_experiment,
            "list_experiments": self._list_experiments,
            "end_thread": self._end_thread,
            "start_analyze_thread": self._start_analyze_thread,
            "select_experiments": self._select_experiments,
            "generate_analysis": self._generate_analysis,
            "modify_analysis": self._modify_analysis,
        }

    # -- 参数校验入口 --

    def execute(self, name: str, args: dict, loop: "AgentLoop") -> dict:
        """校验参数 → 执行工具。错误以 dict 形式返回，不抛异常。"""
        if name not in self.registry:
            return {"error": "unknown_tool",
                    "message": f"未知工具 '{name}'，可用: {list(self.registry.keys())}"}
        schema = self._tool_schema(name)
        required = schema.get("required", [])
        for key in required:
            if key not in args:
                return {"error": "missing_required",
                        "message": f"缺少必要参数 '{key}'"}
        for key, val in args.items():
            expected = schema["properties"].get(key, {}).get("type")
            if expected == "array" and not isinstance(val, list):
                args[key] = [val]
            elif expected == "string" and isinstance(val, (int, float)):
                args[key] = str(val)
        try:
            return self.registry[name](args, loop)
        except Exception as e:
            return {"error": "execution_failed", "message": str(e)[:300]}

    def _tool_schema(self, name: str) -> dict:
        """获取工具的 parameters schema"""
        for t in TOOLS_OPENAI_FORMAT:
            if t["function"]["name"] == name:
                return t["function"]["parameters"]
        return {}

    # -- start_record_thread --

    def _start_record_thread(self, args: dict, loop: "AgentLoop") -> dict:
        """LLM 判断要开始记录时调用，在当前 user 消息之后插入线程开始标记。"""
        if not loop.thread_store:
            return {"error": "no_thread_store", "message": "线程存储未配置"}
        if loop.thread_id:
            if loop._thread_type == "analyze":
                loop.history.append({"role": "system",
                    "content": f"[系统内部] thread_end id={loop.thread_id}"})
                loop.thread_store.set_active_thread(None)
                loop.thread_id = None
                loop._thread_type = None
            else:
                return {"status": "already_started", "thread_id": loop.thread_id}
        thread_id = loop.thread_store.next_id()
        loop.thread_id = thread_id
        loop._thread_type = "record"
        loop.thread_store.set_active_thread(thread_id)
        loop._enter_record_mode()
        begin = {"role": "system", "content": f"[系统内部] thread_begin id={thread_id} type=record"}
        pos = loop._current_turn_user_idx + 1
        loop.history.insert(pos, begin)
        guidance = {"role": "system", "content": "你正在记录一条新实验。优先收集材料、步骤、参数、结果。追问缺失的关键字段。目标：generate_record。"}
        loop.history.insert(pos + 1, guidance)
        loop.thread_store.create("record", [begin, guidance])
        log = get_logger()
        if log:
            log.operation("thread_start", agent="parent", thread=thread_id, type="record")
        return {"status": "started", "thread_id": thread_id}

    # -- end_thread --

    def _end_thread(self, args: dict, loop: "AgentLoop") -> dict:
        """结束当前线程（record 或 analyze），归档并回到自由模式。"""
        if not loop.thread_id:
            return {"status": "no_active_thread",
                    "message": "当前没有活跃线程。"}
        tid = loop.thread_id
        loop._maybe_inject_thread_end("")
        return {"status": "ended", "thread_id": tid,
                "message": f"线程 {tid} 已结束，回到自由模式。"}

    # -- start_analyze_thread --

    def _start_analyze_thread(self, args: dict, loop: "AgentLoop") -> dict:
        """开启跨实验分析线程。与 start_record_thread 对称。"""
        if not loop.thread_store:
            return {"error": "no_thread_store", "message": "线程存储未配置"}
        if loop.thread_id:
            if loop._thread_type == "record":
                return {"error": "in_record_thread",
                        "message": "当前在 record 线程中。如需分析，请在 record 线程中使用 analyze 工具，或结束 record 线程后再开启 analyze 线程。"}
            if loop._thread_type == "analyze":
                return {"status": "already_started", "thread_id": loop.thread_id}
        thread_id = loop.thread_store.next_id()
        loop.thread_id = thread_id
        loop._thread_type = "analyze"
        loop.thread_store.set_active_thread(thread_id)
        begin = {"role": "system", "content": f"[系统内部] thread_begin id={thread_id} type=analyze"}
        pos = loop._current_turn_user_idx + 1
        loop.history.insert(pos, begin)
        guidance = loop._build_thread_guidance("analyze")
        loop.history.insert(pos + 1, guidance)
        loop.thread_store.create("analyze", [begin, guidance])
        log = get_logger()
        if log:
            log.operation("thread_start", agent="parent", thread=thread_id, type="analyze")
        return {"status": "started", "thread_id": thread_id}

    # -- select_experiments --

    def _select_experiments(self, args: dict, loop: "AgentLoop") -> dict:
        """返回选择面板数据，由前端渲染为实验勾选卡片。"""
        return {
            "display": "selector",
            "title": args.get("title", "选择实验"),
            "items": args.get("candidates", []),
            "preselected": args.get("preselected", []),
        }

    # -- generate_analysis --

    def _generate_analysis(self, args: dict, loop: "AgentLoop") -> dict:
        """执行分析 → 写 AnalysisStore → 自动结束线程 → 返回标题+摘要。"""
        query = args["query"]
        refs = args.get("refs", [])
        if len(refs) < 2:
            return {"error": "too_few_experiments",
                    "message": "至少需要2个实验才能分析。"}
        try:
            summary = self.store.summarize_all(exp_ids=refs)
            from lib.analyzer import analyze_experiments
            analysis = analyze_experiments(summary, query, loop.llm)
            # 写 AnalysisStore
            anal_id = ""
            if self.analysis_store:
                anal_id = self.analysis_store.save({
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "question": query,
                    "selected_ids": refs,
                    "analysis": analysis,
                })
                # 写入实验反向关联 analyzed_in
                for exp_id in refs:
                    exp = self.store.load(exp_id)
                    if exp:
                        analyzed = exp.get("analyzed_in", [])
                        if anal_id not in analyzed:
                            analyzed.append(anal_id)
                            exp["analyzed_in"] = analyzed
                            self.store.save(exp)
            # 提取标题：取第一段非空非标题行
            title = query[:40]
            for line in analysis.split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    title = line[:60]
                    break
            # 提取摘要：前200字非Markdown文本
            plain = re.sub(r'[#*>`\-\[\]\(\)]', '', analysis)
            plain = re.sub(r'\s+', ' ', plain).strip()
            excerpt = plain[:200]
            # 自动结束线程
            tid = loop.thread_id
            if tid and not loop._is_child_agent:
                loop._maybe_inject_thread_end(anal_id)
            return {
                "display": "analysis_done",
                "anal_id": anal_id,
                "title": title,
                "summary": excerpt,
                "refs": refs,
            }
        except Exception as e:
            return {"error": "analysis_failed", "message": str(e)[:300]}

    # -- modify_analysis --

    def _modify_analysis(self, args: dict, loop: "AgentLoop") -> dict:
        """修改分析报告。支持 changes / additional_refs / additional_query。"""
        if not self.analysis_store:
            return {"error": "no_analysis_store", "message": "分析存储未配置"}

        # 从 thread 或 analysis_store 推断当前 anal_id
        anal_id = None
        if loop._child_exp_id:
            # analysis child agent 通过 _child_exp_id 传递 anal_id
            anal_id = loop._child_exp_id
        elif loop.thread_store and loop.thread_id:
            thread = loop.thread_store.load(loop.thread_id)
            if thread:
                anal_id = thread.get("anal_generated", "")

        if not anal_id:
            return {"error": "no_anal_id", "message": "无法确定要修改的分析报告"}

        a = self.analysis_store.load(anal_id)
        if not a:
            return {"error": "not_found", "message": f"分析报告 {anal_id} 不存在"}

        changes = args.get("changes")
        additional_refs = args.get("additional_refs", [])
        additional_query = args.get("additional_query")

        try:
            if changes:
                # 模式 1：直接覆盖
                a["analysis"] = changes
                self.analysis_store.save(a)
                return {"status": "modified", "mode": "replace",
                        "message": f"分析报告 {anal_id} 已更新。"}

            if additional_refs:
                # 模式 2：合并实验重新分析
                merged_refs = list(set((a.get("selected_ids") or []) + additional_refs))
                if len(merged_refs) < 2:
                    return {"error": "too_few_experiments",
                            "message": "合并后实验仍不足2个，无法分析。"}
                summary = self.store.summarize_all(exp_ids=merged_refs)
                from lib.analyzer import analyze_experiments
                new_analysis = analyze_experiments(summary, a.get("question", ""), loop.llm)
                a["analysis"] = new_analysis
                a["selected_ids"] = merged_refs
                self.analysis_store.save(a)
                # 更新 analyzed_in 关联
                for exp_id in additional_refs:
                    exp = self.store.load(exp_id)
                    if exp:
                        analyzed = exp.get("analyzed_in", [])
                        if anal_id not in analyzed:
                            analyzed.append(anal_id)
                            exp["analyzed_in"] = analyzed
                            self.store.save(exp)
                return {"status": "modified", "mode": "expand_refs",
                        "message": f"分析报告 {anal_id} 已更新，纳入 {len(merged_refs)} 个实验。"}

            if additional_query:
                # 模式 3：在原报告基础上追加分析维度
                append_prompt = f"""以下是已有的分析报告：
{a.get('analysis', '')}

研究者希望补充以下分析维度：{additional_query}

请将新的分析维度融入报告，输出完整的更新后报告。
保持三区域结构（事实呈现/发现提示/值得思考的问题）。
不要只输出新增部分——输出完整报告。"""
                from lib.analyzer import analyze_experiments
                # 使用独立 LLM 调用，让 LLM 融合新旧内容
                expanded = loop.llm.analyze(
                    system_prompt="你是 Exdiary 分析助手。请将补充分析维度融入已有报告，输出完整的更新后报告。中文回复。",
                    user_prompt=append_prompt,
                    temperature=0.3
                )
                a["analysis"] = expanded
                self.analysis_store.save(a)
                return {"status": "modified", "mode": "expand_query",
                        "message": f"分析报告 {anal_id} 已追加新的分析维度。"}

            return {"error": "no_action",
                    "message": "请提供 changes / additional_refs / additional_query 之一。"}

        except Exception as e:
            return {"error": "modify_failed", "message": str(e)[:300]}

    # -- ask_user（占位，实际由前端处理）--

    def _ask_user(self, args: dict, loop: "AgentLoop") -> dict:
        return {"status": "asked"}

    # -- generate_record --

    def _generate_record(self, args: dict, loop: "AgentLoop") -> dict:
        # 子Agent 不允许 generate_record → 使用 modify_experiment 直接修改
        if loop._is_child_agent:
            return {"error": "use_modify_experiment",
                    "message": "子Agent请使用 modify_experiment 工具直接修改实验字段。修改会自动保存。"}
        if loop._schema_context is None:
            return {"error": "not_in_record_mode",
                    "message": "generate_record 只在记录实验时可用。"}
        notes = loop._build_notes_from_context()
        try:
            from lib.parser import parse_notes
            result = parse_notes(notes, loop.llm)
            result["original_notes"] = notes
            # 子 Agent: 使用现有 EXP ID（修改已有实验）
            if loop._is_child_agent and loop._child_exp_id:
                result["id"] = loop._child_exp_id
            else:
                result["id"] = loop.store.next_id()
            result["references"] = list(loop.references)
            loop._generated_preview = result
            loop._generated_notes = notes
            return {"status": "generated", "id": result["id"],
                    "title": result.get("title", ""),
                    "fields_count": sum(1 for v in result.values() if v)}
        except Exception:
            preview = _fallback_preview(loop)
            # 子 Agent 回退也使用现有 EXP ID
            if loop._is_child_agent and loop._child_exp_id:
                preview["id"] = loop._child_exp_id
            loop._generated_preview = preview
            loop._generated_notes = notes
            return {"status": "generated",
                    "id": preview["id"],
                    "title": preview.get("title", ""),
                    "note": "使用了确定性回退，部分字段可能需手动补全"}

    # -- read_update_log --

    def _read_update_log(self, args: dict, loop: "AgentLoop") -> dict:
        if not self.update_log_store:
            return {"error": "no_update_log_store", "message": "更新日志存储未配置"}
        exp_id = args["exp_id"]
        limit = args.get("limit", 5)
        entries = self.update_log_store.list_recent(exp_id, limit=limit)
        return {"status": "ok", "exp_id": exp_id, "entries": entries}

    # -- modify_experiment --

    def _modify_experiment(self, args: dict, loop: "AgentLoop") -> dict:
        refs = args.get("refs", [])
        changes = args.get("changes", {})
        if not refs:
            return {"error": "no_refs", "message": "请指定要修改的实验编号"}
        if not changes:
            return {"error": "no_changes", "message": "请指定要修改的字段"}

        results = {}
        for ref in refs:
            exp = self.store.load(ref)
            if not exp:
                results[ref] = {"error": "not_found", "message": f"实验 {ref} 不存在"}
                continue
            # 读磁盘旧值
            old_exp = dict(exp)
            # 应用 changes
            for key, value in changes.items():
                if key in ("materials", "equipment", "experimental_plan",
                          "process_parameters", "characterization"):
                    exp[key] = value  # 完整替换
                elif key in ("results", "observations"):
                    if isinstance(value, dict):
                        exp.setdefault(key, {}).update(value)
                elif key == "tags":
                    exp[key] = list(value)
                elif key == "sop" or key == "next_steps":
                    exp[key] = list(value)
                else:
                    exp[key] = value
            # 写更新日志
            from lib.storage import UpdateLogStore
            entries = []
            for key, value in changes.items():
                old_val = old_exp.get(key)
                new_val = value
                if str(old_val)[:200] != str(new_val)[:200]:
                    entries.append({
                        "path": key, "field": key,
                        "old": str(old_val)[:200] if old_val else "",
                        "new": str(new_val)[:200] if new_val else "",
                    })
            if entries and self.update_log_store:
                self.update_log_store.append(
                    exp_id=ref, source="parent_agent",
                    changes=entries,
                    thread_id=loop.thread_id,
                    context={"summary": f"修改了 {len(entries)} 个字段"},
                )
            # 保存
            self.store.save(exp)
            # 注入过期标记到 history
            loop.history.append({
                "role": "system",
                "content": f"{ref} 已被修改。此前关于 {ref} 的对话陈述可能已过时。获取当前数据请使用 load_reference。"
            })
            results[ref] = {
                "status": "modified",
                "display": "diff",
                "changes": entries,
            }
        return {"modified": results}

    # -- manage_collection --

    def _manage_collection(self, args: dict, loop: "AgentLoop") -> dict:
        if not self.favorites_store:
            return {"error": "no_favorites_store", "message": "收藏存储未配置"}
        action = args["action"]
        refs = args.get("refs", [])
        collection = args.get("collection", "默认收藏夹")
        results = {}
        for ref in refs:
            if action == "pin":
                results[ref] = self.favorites_store.toggle_pin(ref)
            elif action == "unpin":
                self.favorites_store.toggle_pin(ref)  # unpin via toggle
                results[ref] = {"ok": True, "pinned": False}
            elif action == "favorite":
                results[ref] = self.favorites_store.toggle_favorite(ref, collection)
            elif action == "unfavorite":
                self.favorites_store.toggle_favorite(ref, collection)
                results[ref] = {"ok": True, "favorited": False}
        return {"status": "ok", "display": "toast",
                "message": f"已完成 {action} 操作", "results": results}

    # -- query_experiment --

    def _query_experiment(self, args: dict, loop: "AgentLoop") -> dict:
        question = args["question"]
        refs = args.get("refs", [])
        answers = []
        for ref in refs:
            # 路径 1: 检查 messages 中是否已加载
            already_loaded = False
            for m in loop.history:
                if m.get("role") == "tool":
                    try:
                        content = json.loads(m.get("content", "{}"))
                        loaded = content.get("loaded", {})
                        if ref in loaded:
                            already_loaded = True
                            break
                    except (json.JSONDecodeError, AttributeError):
                        pass
            if already_loaded:
                answers.append({
                    "exp_id": ref,
                    "answer": f"{ref} 的数据已在对话中，请参考已加载的实验信息。",
                    "source": "memory",
                })
            else:
                # 路径 2: 从磁盘加载
                exp = self.store.load(ref)
                if exp:
                    answers.append({
                        "exp_id": ref,
                        "answer": (
                            f"标题: {exp.get('title','')}。"
                            f"状态: {exp.get('status','')}。"
                            f"目的: {(exp.get('purpose') or '')[:200]}。"
                            f"结论: {(exp.get('conclusion') or '')[:200]}。"
                        ),
                        "source": "file",
                    })
                else:
                    answers.append({
                        "exp_id": ref, "answer": f"实验 {ref} 不存在", "source": "error",
                    })
        return {
            "status": "ok",
            "display": "answer",
            "question": question,
            "answer": "\n\n".join(a["answer"] for a in answers),
            "exp_ids": refs,
            "source": answers[0]["source"] if answers else "file",
        }

    # -- list_experiments --

    def _list_experiments(self, args: dict, loop: "AgentLoop") -> dict:
        all_exps = self.store.list_all_full()
        filtered = []
        status = args.get("status")
        tags = args.get("tags", [])
        experimenter = args.get("experimenter")
        since = args.get("since")

        for exp in all_exps:
            if status and exp.get("status") != status:
                continue
            if tags:
                exp_tags = [t.lower() for t in exp.get("tags", [])]
                if not any(t.lower() in exp_tags for t in tags):
                    continue
            if experimenter and exp.get("experimenter") != experimenter:
                continue
            if since and exp.get("date", "") < since:
                continue
            filtered.append({
                "id": exp.get("id"),
                "title": exp.get("title", ""),
                "date": exp.get("date", ""),
                "status": exp.get("status", ""),
                "tags": exp.get("tags", []),
            })
        return {
            "display": "list",
            "experiments": filtered[:20],
            "count": len(filtered),
        }

    # -- Step 1.5: load_reference --

    def _load_reference(self, args: dict, loop: "AgentLoop") -> dict:
        """加载引用实验。仅处理明确的 EXP ID，模糊描述请用 search_experiments。"""
        results = {}
        for ref in args.get("refs", []):
            ref = str(ref).strip()
            if not ref:
                continue

            m = re.match(r"^(?:@)?(EXP-\d{4}-\d{3})$", ref, re.IGNORECASE)
            if not m:
                results[ref] = {"error": "不是实验编号格式",
                                "message": f"'{ref}'不是EXP编号。如用户给了模糊描述（如'上次的ZnO实验'），请使用 search_experiments 搜索。"}
                continue

            exp_id = m.group(1).upper()
            if exp_id in loop.references:
                results[exp_id] = {"status": "already_loaded",
                                   "note": "该实验数据已在对话中，无需重复加载"}
                continue
            exp = self.store.load(exp_id)
            if exp:
                loop.references.append(exp_id)
                results[exp_id] = self._summarize_exp(exp)
            else:
                results[exp_id] = {"error": "实验不存在",
                                   "message": f"未找到 {exp_id}，请检查编号或使用 search_experiments 搜索。"}

        # 从首个加载的实验推断 experiment_type
        if results and loop.experiment_type == "other":
            for key, val in results.items():
                if isinstance(val, dict) and "tags" in val and val.get("tags"):
                    for tag in val["tags"]:
                        if tag in ("photocatalysis", "hydrothermal", "sol-gel",
                                   "spin-coating", "ball-milling",
                                   "electrochemistry", "xrd", "perovskite-solar"):
                            loop.experiment_type = tag
                            break
                    if loop.experiment_type != "other":
                        break

        return {"loaded": results} if results else {"loaded": {}, "error": "未找到匹配实验"}

    def _summarize_exp(self, exp: dict) -> dict:
        """提取实验的关键信息摘要。返回完整字段数据，不截断数组——LLM 需要看到全量信息才能忠实继承。"""
        result = {
            "id": exp.get("id"),
            "title": exp.get("title"),
            "date": exp.get("date"),
            "status": exp.get("status"),
            "tags": exp.get("tags", []),
            "purpose": (exp.get("purpose") or "")[:200],
            "materials": exp.get("materials", []),
            "equipment": exp.get("equipment", []),
            "sop": exp.get("sop", []),
            "process_parameters": exp.get("process_parameters", []),
            "observations": exp.get("observations", {}),
            "characterization": exp.get("characterization", []),
            "results": {
                "qualitative": (
                    (exp.get("results") or {}).get("qualitative", "")
                )[:200],
                "key_data": (exp.get("results") or {}).get("key_data", []),
            },
            "conclusion": (exp.get("conclusion") or "")[:200],
            "next_steps": exp.get("next_steps", []),
        }
        # 追加最近更新日志摘要
        if self.update_log_store:
            try:
                recent = self.update_log_store.list_recent(exp.get("id", ""), limit=3)
                if recent:
                    result["_recent_updates"] = [
                        {"timestamp": r.get("timestamp", ""),
                         "source": r.get("source", ""),
                         "summary": r.get("context", {}).get("summary", ""),
                         "changed_fields": [c.get("field", "") for c in r.get("changes", [])]}
                        for r in recent
                    ]
            except Exception:
                pass
        return result

    # -- Step 1.6: search_experiments --

    def _search_experiments(self, args: dict, loop: "AgentLoop") -> dict:
        query = args.get("query", "").strip()
        if not query or len(query) < 2:
            return {"candidates": []}

        # 第一步：关键词粗筛
        keyword_results = self._fuzzy_search(query, loop)

        # 如果是纯 ID/编号 查询（"003", "EXP-2026-003"），关键词就够了
        if re.match(r'^[\w-]*\d[\w-]*$', query) or re.match(r'^(?:@)?EXP-', query, re.IGNORECASE):
            return {"candidates": keyword_results[:5]}

        # 第二步：自然语言查询 → LLM 语义搜索
        if not keyword_results or keyword_results[0]["score"] < 0.3:
            try:
                llm_results = self._llm_semantic_search(query, loop)
                if llm_results:
                    return {"candidates": llm_results[:5]}
            except Exception:
                pass

        return {"candidates": keyword_results[:5]}

    def _llm_semantic_search(self, query: str, loop: "AgentLoop") -> list[dict]:
        """LLM 语义搜索：独立 API 调用，不污染 Agent 上下文。处理自然语言如'上周一的''老张做的''失败的那个'。"""
        all_exps = loop.store.list_all_full()
        if not all_exps:
            return []

        # 构造极简摘要（每实验 1-2 行，控制 token 消耗）
        lines = []
        for e in all_exps:
            exp_id = e.get("id", "")
            title = (e.get("title") or "(无标题)")[:40]
            date = e.get("date") or ""
            experimenter = e.get("experimenter") or "佚名"
            status = e.get("status", "")
            status_cn = {"planned": "计划中", "running": "进行中", "done": "已完成",
                         "failed": "失败", "repeated": "重复"}.get(status, status)
            conclusion = (e.get("conclusion") or "")[:40]
            tags = ", ".join(e.get("tags", [])[:4])
            lines.append(
                f"{exp_id} | {title} | {date} | {experimenter} | {status_cn} | {tags} | {conclusion}"
            )

        exp_list_text = "\n".join(lines)
        system_prompt = (
            "你是实验记录搜索引擎。根据用户的自然语言描述，从实验列表中找出最匹配的实验。\n"
            "理解以下类型的查询：\n"
            "- 时间指代：'上周一'='最近一周'，'上个月'='30天前'，'最近'=按日期排序\n"
            "- 人员指代：'老张'='experimenter含张'，'我做的'=忽略\n"
            "- 状态指代：'失败的那个'='status=failed'，'成功的'='status=done且results有值'\n"
            "- 材料指代：'ZnO那个'='材料含ZnO'\n"
            "- 性能指代：'降解率最高的'='results中降解率数值最大的'\n\n"
            "严格返回 JSON 数组（不要包含在 markdown 代码块中）：\n"
            '[{"id": "EXP-2026-xxx", "score": 0.95, "reason": "原因"}, ...]\n'
            "按匹配度降序排列，最多返回5个。score 0-1，0.3以下不要返回。\n"
            "如果没有匹配的实验，返回空数组 []。"
        )
        user_prompt = f"实验列表：\n{exp_list_text}\n\n用户查询：{query}\n\n请返回最匹配的实验 ID 列表（JSON 数组）："

        raw = loop.llm.analyze(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.1)

        # 容错解析
        try:
            results = json.loads(raw.strip())
            if isinstance(results, list):
                return results[:5]
        except json.JSONDecodeError:
            m = re.search(r'\[[\s\S]*\]', raw)
            if m:
                try:
                    results = json.loads(m.group(0))
                    if isinstance(results, list):
                        return results[:5]
                except json.JSONDecodeError:
                    pass
        return []

    def _fuzzy_search(self, query: str, loop: "AgentLoop") -> list[dict]:
        """本地关键词搜索（含实验 ID）"""
        if not query or len(query) < 2:
            return []
        all_exps = loop.store.list_all_full()
        results = []
        text_lower = query.lower()
        has_cjk = any('一' <= c <= '鿿' for c in query)

        for exp in all_exps:
            score = 0.0
            exp_id = (exp.get("id") or "").lower()
            title = (exp.get("title") or "").lower()
            tags = " ".join(exp.get("tags") or []).lower()
            purpose = (exp.get("purpose") or "")[:200].lower()
            mat_names = " ".join(
                m.get("name", "") for m in (exp.get("materials") or [])
                if isinstance(m, dict)
            ).lower()
            searchable = f"{exp_id} {title} {tags} {purpose} {mat_names}"

            if has_cjk:
                tokens = [text_lower]
                for i in range(len(text_lower) - 1):
                    tokens.append(text_lower[i:i + 2])
            else:
                tokens = text_lower.split()

            for token in tokens:
                if len(token) >= 2 and token in searchable:
                    score += 0.25

            for tag in (exp.get("tags") or []):
                if tag.lower() in text_lower:
                    score += 0.3

            if score >= 0.2:
                results.append({
                    "id": exp.get("id"),
                    "title": exp.get("title", ""),
                    "date": exp.get("date", ""),
                    "tags": exp.get("tags", []),
                    "score": min(score, 0.99),
                })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:5]

    # -- Step 1.3: update_schema --

    def _update_schema(self, args: dict, loop: "AgentLoop") -> dict:
        """纯写入：合并 fields → 生成 Schema 状态 → 注入 messages"""
        if loop._schema_context is None:
            return {"error": "not_in_record_mode",
                    "message": "update_schema 只在记录实验时可用。"}
        fields = args.get("fields", {})

        # 追踪 modified_values：首次触及的字段记录旧值
        for key in fields:
            if key not in loop.modified_values:
                old_val = loop._schema_context.get(key)
                if isinstance(old_val, (list, dict)):
                    loop.modified_values[key] = deepcopy(old_val)
                else:
                    loop.modified_values[key] = old_val

        merge_context(loop._schema_context, fields)

        # 推断 experiment_type（从 tags 中）
        if loop.experiment_type == "other":
            tags = loop._schema_context.get("tags", [])
            for tag in tags:
                if tag in ("photocatalysis", "hydrothermal", "sol-gel",
                           "spin-coating", "ball-milling",
                           "electrochemistry", "xrd", "perovskite-solar"):
                    loop.experiment_type = tag
                    break

        # 如果当前在 analyze 线程中，先结束它再开始 record
        if loop.thread_id:
            for m in loop.history:
                if f"thread_begin id={loop.thread_id} type=analyze" in (m.get("content") or ""):
                    loop.history.append({"role": "system",
                        "content": f"[系统内部] thread_end id={loop.thread_id}"})
                    loop.thread_store.set_active_thread(None)
                    loop.thread_id = None
                    loop._thread_type = None
                    break

        # 生成 Schema 状态并注入 messages
        status_msg = loop._build_schema_status()
        loop.history.append({
            "role": "system",
            "content": status_msg,
        })

        return {
            "status": "ok",
            "updated_fields": list(fields.keys()),
        }


# ============================================================================
# Step 1.8 / 1.9 / 1.10: AgentLoop
# ============================================================================

class AgentLoop:
    """基于 tool calling 的对话循环"""

    def __init__(self, llm_client, experiment_store, debug_dir: str | Path | None = None,
                 thread_store=None, update_log_store=None,
                 favorites_store=None, analysis_store=None):
        self.llm = llm_client
        self.store = experiment_store
        self._schema_context = None  # 16-field dict — only non-None in record mode
        self.history = []           # [{role, content, tool_calls?, tool_call_id?}]
        self.references = []        # 已加载的引用实验 ID
        self.experiment_type = "other"
        self.turn_count = 0
        self.tools = ToolExecutor(experiment_store, update_log_store=update_log_store,
                                  favorites_store=favorites_store,
                                  analysis_store=analysis_store)
        self._generated_preview = None   # generate_record 工具产出
        self._generated_notes = None
        self._llm_call_seq = 0      # LLM 调用全局序号（跨 turn 递增）

        # 线程系统
        self.thread_store = thread_store
        self.update_log_store = update_log_store
        self.thread_id = None                    # 当前 active 线程 ID
        self._thread_type = None                 # "record" | "analyze" | None（前端颜色指示）
        self._pending_thread_start = None        # "record" 或 "analyze"，或 None
        self._current_turn_user_idx = -1         # 本轮 user 消息在 history 中的索引
        self.modified_values = {}                # {field: old_value_before_first_touch}
        self._last_ended_thread_id = None         # 刚结束的线程ID（压缩跳过用）
        self._last_summarized_idx = 0             # 上次摘要覆盖到的 history 索引
        self._l0_generated_at = None             # L0 最后生成时间
        self._is_child_agent = False             # 是否子 Agent 实例
        self._is_legacy = False                  # 是否旧实验（无线程）
        self._child_exp_id = None                # 子 Agent 修改的目标 EXP ID
        self._child_initial_history_len = 0      # 子 Agent 初始 history 长度（前端只渲染此后的消息）
        self._child_agent_role = None            # "exp_editor" | "analysis_reviewer" | None

        # 注入 L0 全局摘要（实验库概况、常用标签等）
        if thread_store:
            l0 = thread_store.build_global_summary(experiment_store, update_log_store)
            self._l0_generated_at = getattr(thread_store, 'l0_generated_at', None)
            self.history.append({
                "role": "system", "content": f"[全局上下文]\n{l0}"
            })

        # 调试目录：新会话创建新目录，恢复会话复用已有路径
        if debug_dir:
            self.debug_dir = Path(debug_dir)
        else:
            self.debug_dir = (
                Path(experiment_store.path) / "_debug" /
                datetime.now().strftime("%Y%m%d_%H%M%S")
            )
            os.makedirs(self.debug_dir, exist_ok=True)

    # -- 模式管理 --

    @property
    def mode(self) -> str:
        """当前对话模式: 'general' | 'record' | 'analyze'"""
        if not self.thread_id:
            return "general"
        return self._thread_type or "general"

    def _enter_record_mode(self) -> None:
        """初始化 Schema 上下文（仅 record 模式）。"""
        self._schema_context = deepcopy(DEFAULT_CONTEXT)

    def _exit_record_mode(self) -> None:
        """清理 Schema 上下文。"""
        self._schema_context = None

    def _get_active_tools(self) -> list[dict]:
        """返回当前模式或子 Agent 角色可用的工具列表。"""
        # 子 Agent 角色优先 —— 返回固定工具清单，不走 mode 推断
        if self._child_agent_role == "analysis_reviewer":
            return [
                TOOL_LOAD_REFERENCE, TOOL_SEARCH_EXPERIMENTS,
                TOOL_QUERY_EXPERIMENT, TOOL_LIST_EXPERIMENTS,
                TOOL_READ_UPDATE_LOG, TOOL_MODIFY_ANALYSIS, TOOL_END_THREAD,
            ]
        if self._child_agent_role == "exp_editor":
            return [
                TOOL_LOAD_REFERENCE, TOOL_SEARCH_EXPERIMENTS,
                TOOL_QUERY_EXPERIMENT, TOOL_LIST_EXPERIMENTS,
                TOOL_READ_UPDATE_LOG, TOOL_MODIFY_EXPERIMENT, TOOL_END_THREAD,
            ]

        # 父 Agent —— 按模式返回
        common = [
            TOOL_LOAD_REFERENCE, TOOL_SEARCH_EXPERIMENTS, TOOL_QUERY_EXPERIMENT,
            TOOL_LIST_EXPERIMENTS, TOOL_MANAGE_COLLECTION, TOOL_READ_UPDATE_LOG,
            TOOL_END_THREAD,
        ]
        if self.mode == "record":
            common.extend([TOOL_START_RECORD_THREAD, TOOL_UPDATE_SCHEMA,
                          TOOL_ASK_USER, TOOL_GENERATE_RECORD, TOOL_MODIFY_EXPERIMENT])
        elif self.mode == "general":
            common.extend([TOOL_START_RECORD_THREAD, TOOL_START_ANALYZE_THREAD,
                          TOOL_MODIFY_EXPERIMENT])
        elif self.mode == "analyze":
            # analyze 模式不包含 modify_experiment —— 分析者不应修改实验
            common.extend([TOOL_START_ANALYZE_THREAD, TOOL_SELECT_EXPERIMENTS,
                          TOOL_ASK_USER, TOOL_GENERATE_ANALYSIS])
        return common

    # -- 主循环 --

    def run(self, user_message: str = "") -> dict:
        """处理一条用户消息。返回 {type, message?, context}"""
        log = get_logger()
        if user_message:
            self._current_turn_user_idx = len(self.history)
            self.history.append({"role": "user", "content": user_message})
            self.turn_count += 1
            if log:
                agent = "child" if self._is_child_agent else "parent"
                log.agent(agent, "user", user_message, exp=self._child_exp_id)

        consecutive_errors = 0
        last_tool = None
        _no_progress_count = 0  # Track rounds without update_schema/analyze

        while True:
            self._maybe_inject_thread_start()   # 循环顶部检查 flag

            # 构建 LLM 消息：静态 Prompt → 持久层 history → 请求层状态
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
            ]
            split = self._last_summarized_idx or 0
            if split > 0 and self.thread_store:
                summary = self.thread_store.get_global_context()
                if summary:
                    messages.append({"role": "system", "content": f"[历史摘要]\n{summary}"})
                messages.extend(self.history[split:])
            else:
                messages.extend(self.history)
            # 请求层：record 模式下追加实时 Schema 状态
            if self.mode == "record" and self._schema_context is not None:
                messages.append({"role": "system",
                                "content": self._build_schema_status()})
            # 请求层：追加线程状态（始终在末尾）
            messages.append({"role": "system",
                            "content": self._build_thread_status()})
            self._llm_call_seq += 1
            seq = self._llm_call_seq

            # ---- 日志: LLM 请求 ----
            self._log_llm_request(seq, messages)

            response = self.llm.client.chat.completions.create(
                model=self.llm.model,
                messages=messages,
                tools=self._get_active_tools(),
                temperature=0.3,
                reasoning_effort="max",
            )
            msg = response.choices[0].message
            _reasoning = getattr(msg, "reasoning_content", None) or ""

            # ---- 日志: LLM 响应 ----
            self._log_llm_response(seq, msg, _reasoning)

            # 纯文本 → 不再调工具，直接返回
            if msg.content and not msg.tool_calls:
                entry = {"role": "assistant", "content": msg.content}
                if _reasoning:
                    entry["reasoning_content"] = _reasoning
                self.history.append(entry)
                if log:
                    agent = "child" if self._is_child_agent else "parent"
                    log.agent(agent, "assistant", msg.content, exp=self._child_exp_id)
                self._maybe_inject_thread_start()   # return 前检查
                self._check_thread_cancellation(_no_progress_count)
                self._save_runtime_state()
                return {"type": "reply", "message": msg.content,
                        "context": self._schema_context}

            # 调用了工具
            # 记录 assistant 文本（工具调用前的说明文字，只记一次）
            if log and msg.content:
                ag = "child" if self._is_child_agent else "parent"
                tc_names = [tc.function.name for tc in (msg.tool_calls or [])]
                log.agent(ag, "assistant", msg.content, tool_calls=tc_names, exp=self._child_exp_id)

            has_record_tool = False
            for tc in (msg.tool_calls or []):
                name = tc.function.name
                if name in ("update_schema", "analyze"):
                    has_record_tool = True
                raw_args_str = tc.function.arguments

                # ---- 日志: tool 调用入参 ----
                self._log_tool_call(seq, name, raw_args_str)

                args = json.loads(raw_args_str)
                result = self.tools.execute(name, args, self)

                # ---- 日志: tool 执行结果 ----
                self._log_tool_result(seq, name, result)

                # 统一日志系统：记录工具调用
                if log:
                    ag = "child" if self._is_child_agent else "parent"
                    ok = "error" not in result
                    kw = _tool_log_summary(name, args, result)
                    log.tool(ag, name, ok, exp=self._child_exp_id, **kw)

                # 将 API 工具调用对象转为可序列化的 dict
                tc_dict = {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": raw_args_str,
                    },
                }
                entry = {
                    "role": "assistant",
                    "content": msg.content or None,
                    "tool_calls": [tc_dict],
                }
                if _reasoning:
                    entry["reasoning_content"] = _reasoning
                self.history.append(entry)
                self.history.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })

                # 错误计数
                if "error" in result:
                    if name == last_tool:
                        consecutive_errors += 1
                    else:
                        consecutive_errors = 1
                        last_tool = name
                    if consecutive_errors >= 3:
                        self.history.append({
                            "role": "assistant",
                            "content": "抱歉，处理请求时遇到技术问题。请换个方式描述。",
                        })
                        self._maybe_inject_thread_start()
                        self._save_runtime_state()
                        return {"type": "reply",
                                "message": "抱歉，处理请求时遇到技术问题。请换个方式描述。",
                                "context": self._schema_context}
                else:
                    consecutive_errors = 0
                    last_tool = None

                # ask_user 暂停循环
                if name == "ask_user":
                    self._maybe_inject_thread_start()
                    if not has_record_tool and self.thread_id:
                        _no_progress_count += 1
                    self._check_thread_cancellation(_no_progress_count)
                    self._save_runtime_state()
                    questions = "\n".join(
                        f"{i+1}. {q}" for i, q in enumerate(args.get("questions", []))
                    )
                    if msg.content:
                        questions = msg.content + "\n\n" + questions
                    return {"type": "reply",
                            "message": questions,
                            "context": self._schema_context}

                # select_experiments 暂停循环，等用户勾选确认
                if name == "select_experiments":
                    self._maybe_inject_thread_start()
                    self._save_runtime_state()
                    return {"type": "reply",
                            "message": msg.content or "请在面板中选择实验。",
                            "context": self._schema_context}

                # generate_record → 生成实验记录，停止循环
                if name == "generate_record":
                    self._maybe_inject_thread_start()
                    # 子 Agent 不结束线程：线程由父 Agent 创建和结束，子 Agent 复用 thread_id
                    if self.thread_id and not self._is_child_agent:
                        exp_id = self._generated_preview.get("id", "") if self._generated_preview else ""
                        self._maybe_inject_thread_end(exp_id)
                        self._save_runtime_state()
                    if self._generated_preview is None:
                        return {"type": "reply",
                                "message": "生成失败，请重试或补充更多信息。",
                                "context": self._schema_context}
                    return {"type": "generate",
                            "message": "实验记录已生成，请在预览中确认。",
                            "state": self.state_to_dict(),
                            "preview": self._generated_preview,
                            "notes": self._generated_notes,
                            "context": self._schema_context}

            # 更新无进展计数
            if not has_record_tool and self.thread_id:
                _no_progress_count += 1
            else:
                _no_progress_count = 0

            # 其他工具执行完 → 继续循环

    # -- Step 1.4: Schema 状态摘要 --

    def _build_schema_status(self) -> str:
        """生成 Schema 状态摘要，注入 messages。LLM 直接读这个判断缺什么。"""
        schema_fields = [
            ("title", "标题"), ("date", "日期"), ("experimenter", "实验者"),
            ("status", "状态"), ("tags", "标签"), ("purpose", "目的"),
            ("materials", "材料"), ("equipment", "设备"),
            ("experimental_plan", "方案"), ("sop", "步骤"),
            ("process_parameters", "参数"), ("observations", "观察"),
            ("characterization", "表征"), ("results", "结果"),
            ("conclusion", "结论"), ("next_steps", "下一步"),
        ]

        filled = []
        missing = []
        for key, label in schema_fields:
            val = self._schema_context.get(key) if self._schema_context else None
            if _is_filled(val):
                filled.append(f"{label}({_brief(val)})")
            else:
                missing.append(label)

        lines = [
            f"[Schema状态] 已填充 {len(filled)}/{len(schema_fields)} 字段",
            f"已填: {', '.join(filled) if filled else '(无)'}",
            f"缺失: {', '.join(missing) if missing else '(无)'}",
        ]
        if missing and len(filled) / len(schema_fields) >= 0.7:
            lines.append("提示: 缺失项多为补充字段，可考虑结束收集。")

        return "\n".join(lines)

    # -- 核心字段检查 --

    def _build_notes_from_context(self) -> str:
        """从 context 生成自然语言实验描述（Python 模板，不调 LLM）"""
        ctx = self._schema_context or {}
        parts = []
        if ctx.get("title"):
            parts.append(f"实验标题: {ctx['title']}")
        if ctx.get("date"):
            parts.append(f"日期: {ctx['date']}")
        if ctx.get("experimenter"):
            parts.append(f"实验者: {ctx['experimenter']}")
        if ctx.get("purpose"):
            parts.append(f"实验目的: {ctx['purpose']}")
        materials = ctx.get("materials", [])
        if materials:
            lines = ["材料与试剂:"]
            for m in materials:
                if isinstance(m, dict):
                    name = m.get("name", "")
                    purity = f", 纯度 {m['purity']}" if m.get("purity") else ""
                    vendor = f", {m['vendor']}" if m.get("vendor") else ""
                    amount = f", {m['amount']}" if m.get("amount") else ""
                    lines.append(f"  - {name}{purity}{vendor}{amount}")
            parts.append("\n".join(lines))
        equipment = ctx.get("equipment", [])
        if equipment:
            lines = ["仪器设备:"]
            for e in equipment:
                if isinstance(e, dict):
                    lines.append(f"  - {e.get('device', '')}")
            parts.append("\n".join(lines))
        sop = ctx.get("sop", [])
        if sop:
            lines = ["实验步骤:"]
            for i, s in enumerate(sop, 1):
                lines.append(f"  {i}. {s}")
            parts.append("\n".join(lines))
        params = ctx.get("process_parameters", [])
        if params:
            lines = ["过程参数:"]
            for p in params:
                if isinstance(p, dict):
                    lines.append(f"  - {p.get('parameter', '')}: {p.get('setpoint', '')}")
            parts.append("\n".join(lines))
        chara = ctx.get("characterization", [])
        if chara:
            lines = ["表征手段:"]
            for c in chara:
                if isinstance(c, dict):
                    lines.append(f"  - {c.get('method', '')}")
            parts.append("\n".join(lines))
        results = ctx.get("results", {})
        if isinstance(results, dict):
            if results.get("qualitative"):
                parts.append(f"定性结果: {results['qualitative']}")
            kd = results.get("key_data", [])
            if kd:
                lines = ["关键数据:"]
                for k in kd:
                    if isinstance(k, dict):
                        lines.append(f"  - {k.get('metric', '')}: {k.get('value', '')}")
                parts.append("\n".join(lines))
        obs = ctx.get("observations", {})
        if isinstance(obs, dict):
            items = obs.get("items", [])
            if items:
                parts.append("异常观察: " + "; ".join(str(i) for i in items))
        if ctx.get("conclusion"):
            parts.append(f"结论: {ctx['conclusion']}")
        if ctx.get("next_steps"):
            nss = ctx["next_steps"]
            if isinstance(nss, list):
                parts.append("下一步: " + "; ".join(str(s) for s in nss))
        return "\n\n".join(parts) if parts else "（无实验描述）"

    def _core_fields_filled(self) -> bool:
        """检查核心字段是否已填充。"""
        CORE_BY_TYPE = {
            "photocatalysis": ["purpose", "materials", "process_parameters", "results"],
            "hydrothermal": ["purpose", "materials", "sop", "process_parameters", "results"],
            "sol-gel": ["purpose", "materials", "sop", "process_parameters", "results"],
            "spin-coating": ["purpose", "materials", "sop", "process_parameters", "results"],
            "ball-milling": ["purpose", "materials", "sop", "process_parameters", "results"],
            "electrochemistry": ["purpose", "materials", "process_parameters", "results"],
            "xrd": ["purpose", "materials", "process_parameters", "results"],
            "perovskite-solar": ["purpose", "materials", "sop", "process_parameters", "results"],
        }
        core = CORE_BY_TYPE.get(self.experiment_type,
                                ["purpose", "materials", "sop", "results"])
        return all(_is_filled((self._schema_context or {}).get(f)) for f in core)

    # -- 线程系统 --

    def _l0_stale(self) -> bool:
        """L0 摘要是否过期（距上次生成超过 1 小时）。"""
        if self._l0_generated_at is None:
            return True
        if not isinstance(self._l0_generated_at, datetime):
            try:
                self._l0_generated_at = datetime.fromisoformat(str(self._l0_generated_at))
            except (ValueError, TypeError):
                return True
        return (datetime.now() - self._l0_generated_at).total_seconds() > 3600

    def _refresh_l0(self) -> None:
        """重新生成 L0 摘要并替换 history[0]（如果 history[0] 是 L0）。"""
        if not self.thread_store:
            return
        l0 = self.thread_store.build_global_summary(self.store, self.update_log_store)
        self._l0_generated_at = getattr(self.thread_store, 'l0_generated_at', datetime.now())
        # 替换或插入 L0
        if self.history and "[全局上下文]" in (self.history[0].get("content") or ""):
            self.history[0]["content"] = f"[全局上下文]\n{l0}"
        else:
            self.history.insert(0, {"role": "system", "content": f"[全局上下文]\n{l0}"})

    def _build_thread_guidance(self, thread_type: str) -> dict:
        """生成线程模式引导消息。"""
        if thread_type == "record":
            return {"role": "system",
                    "content": "你正在记录一条新实验。优先收集材料、步骤、参数、结果。追问缺失的关键字段。目标：generate_record。"}
        elif thread_type == "analyze":
            return {"role": "system",
                    "content": "你正在进行跨实验分析。先了解用户需求，用 search_experiments 或 list_experiments 缩小范围，用 select_experiments 让用户勾选实验，用 load_reference 加载数据，用 ask_user 确认分析角度。需求明确后调用 generate_analysis 执行分析并归档。"}
        return {"role": "system", "content": ""}

    def _build_thread_status(self) -> str:
        """生成当前线程状态声明。每轮 LLM 请求注入，不入 history。"""
        # 子 Agent 角色覆盖 —— 不依赖 _thread_type
        if self._child_agent_role == "analysis_reviewer":
            return (
                "[系统状态] 你正在审阅/修改一份已完成的分析报告。"
                "可用工具：load_reference（查看报告中引用的实验）、search_experiments、"
                "read_update_log、modify_analysis（修改报告内容）。"
                "不要使用 start_analyze_thread、select_experiments、generate_analysis"
                "——这些属于分析创建阶段，不是报告审阅阶段。"
            )
        if self._child_agent_role == "exp_editor":
            return (
                "[系统状态] 你正在修改已完成的实验。"
                "修改前先用 load_reference 加载磁盘最新数据（不要依赖对话记忆）。"
                "修改用 modify_experiment 工具直接执行，会自动保存和记录日志。"
                "不要用 update_schema 或 generate_record。"
            )

        if not self.thread_id:
            return (
                "[系统状态] 自由模式。"
                "你可回答查询、管理收藏、闲聊。"
                "用户要记录新实验时调用 start_record_thread，"
                "要跨实验分析时调用 start_analyze_thread。"
            )
        if self._thread_type == "record":
            return (
                "[系统状态] record 线程进行中。"
                "持续收集实验信息，缺失关键字段时追问。目标：generate_record。"
            )
        if self._thread_type == "analyze":
            return (
                "[系统状态] analyze 线程进行中。"
                "深入讨论，使用 search_experiments + load_reference + 自身推理。"
                "目标：输出分析报告。"
            )
        return "[系统状态] 自由模式。"

    def _maybe_inject_thread_start(self) -> None:
        """analyze 工具触发时注入线程标记。record 线程由 start_record_thread 工具直接处理。"""
        if not self._pending_thread_start or not self.thread_store:
            return
        thread_type = self._pending_thread_start
        self._pending_thread_start = None
        thread_id = self.thread_store.next_id()
        self.thread_id = thread_id
        self._thread_type = thread_type
        self.thread_store.set_active_thread(thread_id)
        if thread_type == "record":
            self._enter_record_mode()
        begin = {"role": "system", "content": f"[系统内部] thread_begin id={thread_id} type={thread_type}"}
        insert_pos = self._current_turn_user_idx + 1
        self.history.insert(insert_pos, begin)
        guidance = self._build_thread_guidance(thread_type)
        if guidance.get("content"):
            self.history.insert(insert_pos + 1, guidance)
        self.thread_store.create(thread_type, [begin, guidance] if guidance.get("content") else [begin])
        log = get_logger()
        if log:
            log.operation("thread_start", agent="parent", thread=thread_id, type=thread_type)

    def _maybe_inject_thread_end(self, produced_id: str) -> None:
        """注入线程结束标记 + 提取 messages → 写线程文件 + 更新索引 + 重置上下文。"""
        if not self.thread_id or not self.thread_store:
            return
        end = {"role": "system",
               "content": f"[系统内部] thread_end id={self.thread_id} product={produced_id}"}
        self.history.append(end)
        self._extract_and_save_thread(produced_id)
        self.thread_store.set_active_thread(None)
        # 统一日志
        log = get_logger()
        if log:
            agent = "child" if self._is_child_agent else "parent"
            log.operation("thread_end", agent=agent, thread=self.thread_id, produced=produced_id)
        # 记录刚结束的线程ID，压缩时跳过它
        self._last_ended_thread_id = self.thread_id
        self.thread_id = None
        self._thread_type = None
        # 清理 Schema 状态和引用
        self._exit_record_mode()
        self.references = []
        self.experiment_type = "other"
        self.modified_values = {}

    def _extract_and_save_thread(self, produced_id: str) -> None:
        """提取 begin-end 标记间的 messages → 写入线程文件 + 更新索引。"""
        tid = self.thread_id
        # 找到 begin 标记位置
        begin_idx = None
        end_idx = None
        for i, m in enumerate(self.history):
            content = m.get("content") or ""
            if f"thread_begin id={tid}" in content:
                begin_idx = i
            elif f"thread_end id={tid}" in content:
                end_idx = i
                break
        if begin_idx is None or end_idx is None:
            return
        # 提取区间 messages（从触发用户消息开始，它位于 begin 标记之前）
        thread_msgs = self.history[begin_idx - 1:end_idx + 1]
        # 更新线程文件
        thread = self.thread_store.load(tid)
        if thread:
            thread["messages"] = thread_msgs
            thread["status"] = "done"
            thread["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if produced_id.startswith("EXP-") and not thread.get("exp_generated"):
                thread["exp_generated"] = produced_id
                # 从首个 user 消息截取标题
                for m in thread_msgs:
                    if m.get("role") == "user":
                        first_user = m.get("content") or ""[:30]
                        thread["title"] = first_user
                        break
                thread["summary"] = f"生成{produced_id}"
            elif produced_id.startswith("ANAL-"):
                thread["anal_generated"] = produced_id
            # Title: if >= 3 turns, generate with LLM later (simplified for now)
            self.thread_store.save(thread)
            self.thread_store.update_index(thread)
        # 更新用户画像 + L0
        if produced_id.startswith("EXP-") and self.thread_store:
            exp = self.store.load(produced_id)
            if exp:
                self.thread_store.update_user_profile(exp)
                self.thread_store.recalc_tag_counts(self.store)
            self._refresh_l0()

    def _check_thread_cancellation(self, consecutive_no_progress: int) -> None:
        """检测线程是否需要取消。返回更新后的 consecutive_no_progress。"""
        if not self.thread_id:
            return
        # 简单实现：如果连续 3 轮无进展，自动取消
        # 注意：调用方负责维护 consecutive_no_progress 计数
        if consecutive_no_progress >= 3:
            tid = self.thread_id
            self.history.append({"role": "system",
                "content": f"[系统内部] thread_cancelled id={tid}"})
            # 移除 begin 标记
            for i, m in enumerate(self.history):
                if f"thread_begin id={tid}" in (m.get("content") or ""):
                    self.history.pop(i)
                    # 同时移除紧跟的引导消息
                    if i < len(self.history) and self.history[i].get("role") == "system":
                        content = self.history[i].get("content") or ""
                        if "正在记录" in content or "正在进行" in content:
                            self.history.pop(i)
                    break
            self.thread_id = None
            self._thread_type = None
            self._exit_record_mode()
            self.modified_values = {}
            log = get_logger()
            if log:
                agent = "child" if self._is_child_agent else "parent"
                log.operation("thread_cancelled", agent=agent, thread=tid)

    # -- 子 Agent --

    @classmethod
    def create_child_agent(cls, parent_loop: "AgentLoop", thread_id: str) -> "AgentLoop":
        """从父 Agent 创建子 Agent，用于续接历史线程（修改已完成的实验）。"""
        thread = parent_loop.thread_store.load(thread_id)
        if not thread:
            raise ValueError(f"Thread {thread_id} not found")

        child = cls(
            parent_loop.llm,
            parent_loop.store,
            debug_dir=parent_loop.debug_dir,
            thread_store=parent_loop.thread_store,
            update_log_store=parent_loop.update_log_store,
            favorites_store=getattr(parent_loop.tools, 'favorites_store', None),
            analysis_store=getattr(parent_loop.tools, 'analysis_store', None),
        )
        # 子 Agent 上下文: L0 + 线程完整 messages（LLM 参考用）
        child.history = list(child.history)  # keep L0
        for m in thread.get("messages", []):
            if m.get("role") != "system" or "[全局上下文]" not in (m.get("content") or ""):
                child.history.append(dict(m))
        # 记录初始 history 长度——前端只渲染此索引之后的消息
        child._child_initial_history_len = len(child.history)
        child.thread_id = thread_id
        child._is_child_agent = True
        child._child_agent_role = "exp_editor"
        child._parent_thread_store = parent_loop.thread_store
        return child

    @classmethod
    def create_legacy_child_agent(cls, llm_client, store, exp_data: dict,
                                   thread_store=None, update_log_store=None,
                                   favorites_store=None,
                                   analysis_store=None) -> "AgentLoop":
        """为无线程关联的旧实验创建子 Agent。初始上下文: L0 + EXP 结构化数据。"""
        child = cls(llm_client, store,
                    thread_store=thread_store,
                    update_log_store=update_log_store,
                    favorites_store=favorites_store,
                    analysis_store=analysis_store)
        # 注入 EXP 数据作为上下文
        child.history.append({
            "role": "system",
            "content": f"[当前实验数据]\n{json.dumps(exp_data, ensure_ascii=False, indent=2)}"
        })
        child._is_child_agent = True
        child._is_legacy = True
        child._child_agent_role = "exp_editor"
        child._parent_thread_store = thread_store
        return child

    # -- 调试日志: LLM 请求 --

    def _log_llm_request(self, seq: int, messages: list) -> None:
        """保存发送给 LLM 的完整 messages（含 system prompt + history）"""
        try:
            compact = []
            for m in messages:
                entry = {"role": m["role"]}
                if m.get("content"):
                    c = m["content"]
                    entry["content"] = c if len(c) <= 5000 else c[:5000] + "\n\n... (截断) ..."
                if m.get("tool_calls"):
                    entry["tool_calls"] = [
                        {"name": tc["function"]["name"],
                         "arguments": tc["function"]["arguments"][:2000]}
                        for tc in m["tool_calls"]
                    ]
                if m.get("tool_call_id"):
                    entry["tool_call_id"] = m["tool_call_id"]
                compact.append(entry)
            filepath = self.debug_dir / f"call_{seq:03d}_request.json"
            filepath.write_text(
                json.dumps(compact, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception:
            print(f"[DEBUG] _log_llm_request failed: {sys.exc_info()[1]}", file=sys.stderr)

    def _log_llm_response(self, seq: int, msg, reasoning: str = "") -> None:
        """保存 LLM 的原始响应（含 content、reasoning_content 和 tool_calls）"""
        try:
            data = {}
            if reasoning:
                data["reasoning_content"] = reasoning[:3000]
            if msg.content:
                data["content"] = msg.content[:3000]
            if msg.tool_calls:
                data["tool_calls"] = []
                for tc in msg.tool_calls:
                    data["tool_calls"].append({
                        "id": tc.id,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    })
            if not data:
                data["content"] = "(empty response)"
            filepath = self.debug_dir / f"call_{seq:03d}_response.json"
            filepath.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception:
            print(f"[DEBUG] _log_llm_response failed: {sys.exc_info()[1]}", file=sys.stderr)

    def _log_tool_call(self, seq: int, tool_name: str, raw_args: str) -> None:
        """保存 LLM 调用的 tool 名称和原始参数"""
        try:
            filepath = self.debug_dir / f"call_{seq:03d}_tool_{tool_name}_args.json"
            filepath.write_text(raw_args, encoding="utf-8")
        except Exception:
            print(f"[DEBUG] _log_tool_call failed: {sys.exc_info()[1]}", file=sys.stderr)

    def _log_tool_result(self, seq: int, tool_name: str, result: dict) -> None:
        """保存 tool 执行后的返回结果"""
        try:
            filepath = self.debug_dir / f"call_{seq:03d}_tool_{tool_name}_result.json"
            text = json.dumps(result, ensure_ascii=False, indent=2)
            if len(text) > 10000:
                text = text[:10000] + "\n\n... (截断) ..."
            filepath.write_text(text, encoding="utf-8")
        except Exception:
            print(f"[DEBUG] _log_tool_result failed: {sys.exc_info()[1]}", file=sys.stderr)

    # -- 持久化: 每轮结束时实时保存 --



    def _save_runtime_state(self) -> None:
        """保存 AgentLoop 运行时状态。父 Agent 写 _current_state.yaml；子 Agent 写 child_state.yaml（不碰 _current_state.yaml）。"""
        if not self.thread_store:
            return
        try:
            if self._is_child_agent:
                # 子 Agent: 写独立 child_state.yaml，绝不覆盖父 Agent 的 _current_state.yaml
                key = self.thread_id or self._child_exp_id
                if key:
                    self.thread_store.save_child_state(key, self.state_to_dict())
            else:
                # 父 Agent: 写 _current_state.yaml
                self.thread_store.save_current_state(self.state_to_dict())
        except Exception:
            pass
        # 子 Agent 不触发摘要
        if not self._is_child_agent:
            self._maybe_summarize()

    # -- 上下文窗口管理 --

    def _maybe_summarize(self) -> None:
        """上次摘要后的新增消息超过 30 万 token 时，生成新摘要。self.history 不动。"""
        if not self.thread_store:
            return
        start = self._last_summarized_idx or 0
        new_msgs = self.history[start:]
        new_chars = sum(len(m.get("content") or "") for m in new_msgs)
        if new_chars // 2 < 300_000:
            return
        # 保留最近 10 万 token 完整，其余进摘要
        keep = 0; keep_chars = 0
        for m in reversed(new_msgs):
            keep_chars += len(m.get("content") or "")
            keep += 1
            if keep_chars // 2 >= 100_000:
                break
        to_summarize = new_msgs[:-keep] if keep < len(new_msgs) else []
        if not to_summarize:
            return
        try:
            text = "\n".join(
                f"[{m['role']}] {(m.get('content') or '')[:300]}"
                for m in to_summarize[-600:]
            )
            raw = self.llm.analyze(
                system_prompt="你是对话摘要助手。将以下对话压缩为 500-2000 字的摘要，保留实验记录、修改操作、关键决策。只摘要以下提供的对话，不要引用外部信息。用中文。",
                user_prompt=f"请摘要以下对话：\n\n{text[:15000]}",
                temperature=0.2
            )
            new_summary = raw[:3000]
        except Exception:
            lines = [f"用户: {(m.get('content') or '')[:80]}" for m in to_summarize if m.get("role") == "user"]
            new_summary = "\n".join(lines[-80:])
        # 追加到已有摘要后面，不覆盖
        prev = self.thread_store.get_global_context()
        combined = f"{prev}\n\n---\n\n{new_summary}" if prev else new_summary
        self._last_summarized_idx = start + len(to_summarize)
        self.thread_store.update_global_context(combined,
            uncompressed_thread_ids=[self.thread_id] if self.thread_id else [])

    # -- 状态序列化 --

    def state_to_dict(self) -> dict:
        return {
            "context": self._schema_context,
            "references": self.references,
            "experiment_type": self.experiment_type,
            "turn_count": self.turn_count,
            "llm_call_seq": self._llm_call_seq,
            "history": [
                {k: v for k, v in m.items() if v is not None}
                for m in self.history
            ],
            "debug_dir": str(self.debug_dir),
            "thread_id": self.thread_id,
            "_thread_type": self._thread_type,
            "_pending_thread_start": self._pending_thread_start,
            "_current_turn_user_idx": self._current_turn_user_idx,
            "modified_values": dict(self.modified_values),
            "_l0_generated_at": str(self._l0_generated_at) if self._l0_generated_at else None,
            "_last_summarized_idx": self._last_summarized_idx,
            "_is_child_agent": self._is_child_agent,
            "_is_legacy": self._is_legacy,
            "_child_exp_id": self._child_exp_id,
            "_child_initial_history_len": self._child_initial_history_len,
            "_child_agent_role": self._child_agent_role,
        }

    @classmethod
    def from_dict(cls, llm_client, store, data: dict,
                  thread_store=None, update_log_store=None,
                  favorites_store=None, analysis_store=None) -> "AgentLoop":
        loop = cls(llm_client, store, debug_dir=data.get("debug_dir") or None,
                   thread_store=thread_store, update_log_store=update_log_store,
                   favorites_store=favorites_store, analysis_store=analysis_store)
        # 向后兼容：旧的 context 可能为空 dict（不是 None），按 None 处理
        ctx = data.get("context")
        if ctx and any(_is_filled(v) for v in ctx.values()):
            loop._schema_context = ctx
        else:
            loop._schema_context = None
        loop.references = data.get("references", [])
        loop.experiment_type = data.get("experiment_type", "other")
        loop.turn_count = data.get("turn_count", 0)
        loop._llm_call_seq = data.get("llm_call_seq", 0)
        loop.history = data.get("history", [])
        loop.thread_id = data.get("thread_id")
        loop._thread_type = data.get("_thread_type")
        # 验证磁盘上线程是否仍活跃（可能已被其他进程或手动操作结束）
        if loop.thread_id and thread_store:
            thread = thread_store.load(loop.thread_id)
            if not thread or thread.get("status") != "active":
                loop.thread_id = None
                loop._thread_type = None
        loop._pending_thread_start = data.get("_pending_thread_start")
        loop._current_turn_user_idx = data.get("_current_turn_user_idx", -1)
        loop.modified_values = data.get("modified_values", {})
        loop._l0_generated_at = data.get("_l0_generated_at")
        loop._last_summarized_idx = data.get("_last_summarized_idx", 0)
        loop._is_child_agent = data.get("_is_child_agent", False)
        loop._is_legacy = data.get("_is_legacy", False)
        loop._child_exp_id = data.get("_child_exp_id")
        loop._child_initial_history_len = data.get("_child_initial_history_len", 0)
        loop._child_agent_role = data.get("_child_agent_role")

        # L0 摘要超过 1 小时 → 重新生成
        if thread_store and loop._l0_stale():
            loop._refresh_l0()

        return loop
