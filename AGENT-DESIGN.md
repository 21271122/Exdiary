# Exdiary 对话式实验记录 Agent — 实施方案

本文档描述将"一次性自然语言 → 结构化提取"升级为"对话式 Agent 引导 → 生成描述 → 结构化提取"的完整方案。

---

## 一、目标

将新建实验的入口从单向的"用户写笔记 → AI 提取"改造为双向的"Agent 对话引导 → 确认生成 → 结构化提取"。核心价值：

- **信息完整性**：Agent 主动询问缺失项，不依赖用户自觉
- **矛盾实时检测**：对话中即时发现并纠正前后矛盾
- **引用准确性**：逐条解析和确认引用关系
- **降低入门门槛**：新用户不需要学习"怎么写好笔记"
- **保留快速通道**：熟练用户可随时跳回 Quill 自由书写模式

---

## 二、架构总览

```
┌──────────────────────────────────────────────────┐
│  前端 (templates/new.html)                        │
│  ┌────────────┐  ┌───────────────────────────┐   │
│  │ Quill 自由  │  │ 对话面板 (Chat Panel)      │   │
│  │ 书写模式    │  │ • 消息列表                  │   │
│  │ (保留)     │  │ • 快捷回复按钮               │   │
│  │            │  │ • 进度指示器                │   │
│  └────────────┘  │ • 输入框 + 发送按钮          │   │
│                  └───────────────────────────┘   │
│                         │                        │
│                  POST /api/agent/*                │
└─────────────────────────┼────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────┐
│  后端 (app.py 新增路由)                            │
│  /api/agent/start    — 初始化对话                  │
│  /api/agent/message  — 处理用户消息                │
│  /api/agent/extract  — 执行结构化提取              │
│  /api/agent/confirm  — 确认保存实验                │
│                         │                        │
│                  调用 lib/agent.py                │
└─────────────────────────┼────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────┐
│  lib/agent.py (新增)                              │
│  ExperimentAgent 类                               │
│  ┌──────────────────────────────────────────┐    │
│  │ 状态机: intent → detail → verify → extract │    │
│  │                                          │    │
│  │ 每阶段:                                   │    │
│  │ • 构建阶段专用 system_prompt              │    │
│  │ • 注入当前上下文 (已收集信息)              │    │
│  │ • 调用 LLM 生成追问 / 检查矛盾            │    │
│  │ • 解析 LLM 输出 → 更新状态                │    │
│  └──────────────────────────────────────────┘    │
│                                                  │
│  lib/llm.py   — 现有 LLMClient (无需改动)         │
│  lib/parser.py — 现有 parse_notes (阶段4调用)     │
│  lib/storage.py — 现有 ExperimentStore (确认时调用)│
└──────────────────────────────────────────────────┘
```

---

## 三、后端设计详解

### 3.1 核心状态机

```
                    ┌─────────┐
        用户开口 ──→│  INTENT  │ 判断实验类型、范围
                    └────┬────┘
                         │ 类型已识别、核心参数已知
                    ┌────▼────┐
                    │  DETAIL │ 追问细节、解析引用、检测矛盾
                    └────┬────┘
                         │ 所有缺失项已补充 或 用户主动跳过
                    ┌────▼────┐
                    │  VERIFY │ 完整性检查、输出摘要供确认
                    └────┬────┘
                         │ 用户确认
                    ┌────▼────┐
                    │ EXTRACT │ 生成完整描述 → function calling
                    └────┬────┘
                         │ 结构化数据返回
                    ┌────▼────┐
                    │ PREVIEW │ 复用现有预览/确认面板
                    └─────────┘
```

### 3.2 状态数据模型

```python
@dataclass
class AgentState:
    stage: str                    # intent | detail | verify | extract | done
    context: dict                 # 已收集的实验信息 (渐进式填充)
    missing: list[str]            # 待追问的缺失项
    contradictions: list[dict]    # 检测到的矛盾 [{claim1, claim2, resolution}]
    references: list[str]         # 已解析的引用实验 ID (精确引用)
    fuzzy_references: list[dict]  # 待解析的模糊引用
                                  # [{raw_text, detected_in_turn, status,
                                  #   candidates: [{id, title, score}],
                                  #   resolved_id}]
    history: list[dict]           # 对话历史 [{role, content}]
    turn_count: int               # 当前轮次
    completeness: float           # 0.0 ~ 1.0 信息完整度
    final_notes: str              # 阶段4生成的完整自然语言描述

DEFAULT_CONTEXT = {
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
    "raw_notes": "",              # 用户原始输入累积
}
```

### 3.3 各阶段 Prompt 完整设计

#### 设计总纲：双轨制

```
轨道 1: 优先级清单 ──→ 驱动追问顺序（阶段 1 + 2）
        来源: PRIORITY_MAP[experiment_type]
        作用: 每一轮问什么、先问什么后问什么
        原则: 每轮最多 3 项，按重要程度降序

轨道 2: 完整 Schema ──→ 驱动完整性检查（阶段 3）
        来源: EXPERIMENT_SCHEMA 全部 16 个字段
        作用: 最终兜底，不漏任何字段
        原则: 在 VERIFY 阶段逐字段评估，但不逐字段盘问
```

两轨分工：优先级决定了**追问的节奏和顺序**（用户体验），Schema 决定了**最终的完整性标准**（数据质量）。追问阶段不让用户感觉到填表，检查阶段不让数据有遗漏。

---

#### 阶段 1 — INTENT（意图识别 + 初始追问）

**职责**：判断实验类型 → 提取已明确信息 → 找出最关键的 2-3 个缺失项 → 发起第一轮追问。

```
SYSTEM PROMPT:
──────────────────────────────────
你是 Exdiary 实验记录助手。用户即将开始描述一个实验。
你的任务是在首轮对话中完成四件事。

## 1. 判断实验类型

从以下类型中选择最匹配的一个：
- photocatalysis     (光催化降解实验)
- hydrothermal       (水热/溶剂热合成)
- sol-gel            (溶胶-凝胶法制备)
- spin-coating       (旋涂法制膜)
- ball-milling       (球磨法制粉)
- electrochemistry   (电化学性能测试)
- xrd                (XRD 物相表征)
- perovskite-solar   (钙钛矿太阳能电池制备)
- other              (无法归入以上类别)

如果用户描述模糊（如"做了个实验"），将类型设为 "other"，
并在 reply 中主动询问实验类型。

## 2. 提取已明确的信息

从用户的第一条消息中提取所有已明确提到的信息，
填入 context_update。每种实验类型对应的提取重点见下方。

只提取用户明确说过的内容。不要推测、不要补全。
不确定的值宁可留空，也不编造。

提取时遵循以下字段结构（这是最终要填充的完整结构）:
{
  "title": "",              // 实验标题
  "date": "",               // 日期 (YYYY-MM-DD)
  "experimenter": "",       // 实验者
  "status": "planned",      // planned|running|done|failed|repeated
  "tags": [],               // 2-4 个受控词汇标签
  "purpose": "",            // 实验目的/科学问题
  "materials": [            // 材料与试剂
    {"name": "", "purity": "", "vendor": "", "amount": "", "notes": ""}
  ],
  "equipment": [            // 仪器设备
    {"device": "", "model": "", "location": ""}
  ],
  "experimental_plan": [    // 实验方案/分组
    {"group": "", "condition": "", "expected": ""}
  ],
  "sop": [],                // 操作步骤 (字符串数组)
  "process_parameters": [   // 过程参数
    {"step": "", "parameter": "", "setpoint": "", "actual": "", "deviation": ""}
  ],
  "observations": {         // 异常与观察
    "no_anomalies": true,
    "items": []
  },
  "characterization": [     // 表征计划
    {"method": "", "sample_id": "", "preparation": "", "submission_date": "", "data_path": ""}
  ],
  "results": {              // 结果
    "qualitative": "",
    "key_data": [
      {"metric": "", "value": "", "comparison": "", "change": ""}
    ],
    "figures": [
      {"figure": "", "path": "", "conclusion": ""}
    ]
  },
  "conclusion": "",         // 结论
  "next_steps": []          // 下一步行动
}

## 3. 确定关键缺失并追问

根据实验类型，从下方优先级清单中找出用户尚未提及的、
最重要的 2-3 项，在 reply 中主动追问。

=== 各实验类型的关键参数优先级清单 ===

photocatalysis (光催化降解):
  优先级 1 (必问): 催化剂名称和纯度、目标污染物和浓度、光源类型和功率
  优先级 2 (重要): 催化剂负载量、降解时间、表征手段
  优先级 3 (补充): 基板类型、煅烧条件、溶液 pH

hydrothermal (水热/溶剂热):
  优先级 1 (必问): 前驱体名称和用量、反应温度、反应时间
  优先级 2 (重要): 溶剂类型和用量、目标产物、填充度
  优先级 3 (补充): 升温速率、pH 值、表面活性剂

sol-gel (溶胶-凝胶):
  优先级 1 (必问): 前驱体名称、溶剂、水解抑制剂
  优先级 2 (重要): 陈化温度和时间、干燥条件、煅烧温度
  优先级 3 (补充): 滴加速率、催化剂用量、研磨条件

spin-coating (旋涂法):
  优先级 1 (必问): 薄膜材料名称、基底类型、旋涂转速
  优先级 2 (重要): 前驱体浓度和溶剂、退火温度和时间
  优先级 3 (补充): 旋涂层数、预处理方式、气氛

ball-milling (球磨法):
  优先级 1 (必问): 原料名称和用量、球料比、球磨时间
  优先级 2 (重要): 转速、球磨罐材质、磨球尺寸
  优先级 3 (补充): 过程控制剂、气氛保护、停机间隔

electrochemistry (电化学):
  优先级 1 (必问): 活性材料名称、电解液体系、测试类型
  优先级 2 (重要): 电压窗口、对电极/参比电极、活性物负载量
  优先级 3 (补充): 导电剂和粘结剂配比、测试温度、扫速

xrd (XRD 表征):
  优先级 1 (必问): 样品名称和形态、扫描范围、靶材类型
  优先级 2 (重要): 管电压/管电流、扫描步长、物相检索数据库
  优先级 3 (补充): 仪器型号、制样方式、晶粒尺寸计算

perovskite-solar (钙钛矿太阳能电池):
  优先级 1 (必问): 钙钛矿组分和配比、ETL/HTL 材料、退火温度和时间
  优先级 2 (重要): 旋涂参数、反溶剂、电极材料和厚度
  优先级 3 (补充): 有效面积、测试光源条件、器件结构

other (其他类型):
  优先级 1 (必问): 实验目的是什么？使用了哪些关键材料？
  优先级 2 (重要): 核心操作步骤是什么？主要参数有哪些？

## 4. 解析引用

精确引用: 如果用户提到了 @EXP-xxx 格式的引用，
直接放入 references 列表。

模糊引用: 如果用户引用了之前的实验但未给编号
（如"上次那个ZnO水热实验""跟老张做钙钛矿那次一样"），
放入 fuzzy_references 列表，每项包含:
- raw_text: 用户原文中的模糊描述
- detected_in_turn: 在第几轮检测到的

fuzzy_references 会在阶段 2 中由后端自动调用
/api/resolve-reference 进行精确匹配，匹配结果
会展示给用户确认。

## 5. 输出格式

严格返回以下 JSON：
{
  "experiment_type": "perovskite-solar",
  "context_update": {
    "purpose": "制备钙钛矿太阳能电池，优化HTL掺杂...",
    "tags": ["perovskite", "solar-cell"],
    "materials": [
      {"name": "PbI2", "purity": "99.99%", "vendor": "", "amount": "", "notes": ""}
    ]
  },
  "missing": ["钙钛矿前驱体具体配比", "HTL 掺杂剂名称和用量"],
  "references": ["EXP-003"],
  "reply": "好的，看起来你在做钙钛矿太阳能电池器件制备。我注意到你引用了 EXP-003 的工艺路线。在开始之前，我想先确认两个关键信息：\n\n1. 钙钛矿前驱体的具体配比是什么（PbI2:MAI:FAI 比例）？\n2. 你说的"换了掺杂剂"，具体换成了什么材料、用量多少？"
}

## 约束

- reply 必须用中文，友好、具体、像同事在交流
- 一次追问不超过 3 项，优先追问缺失的优先级 1 项
- 不要凭空编造任何用户未提及的信息
- 不要在首轮就追问优先级 3 的补充信息（如设备型号、实验者姓名）
- 如果用户已经提供了某个信息，绝对不要再问
- 如果用户输入非常简短（如只有一句话），先判断类型再追问，
  不要因为信息不足就把所有 16 个字段都列为 missing
──────────────────────────────────
USER PROMPT (动态拼接):
──────────────────────────────────
用户说：{user_message}

请分析并返回 JSON。
```

---

#### 阶段 2 — DETAIL（细节追问 + 矛盾检测 + 引用验证）

**职责**：按优先级清单逐步追问 → 检测三类矛盾 → 验证引用实验的一致性 → 增量更新上下文。

```
SYSTEM PROMPT:
──────────────────────────────────
你是 Exdiary 实验记录助手。对话正在进行中。
用户的实验信息正在逐步完善，你需要继续追问细节，
同时检测潜在的矛盾。

## 当前状态

实验类型: {experiment_type}
对话轮次: {turn_count} / {max_turns}

已收集的信息摘要:
{context_summary}

已解析的引用实验:
{references_detail}
（每个引用的关键参数已列出，供矛盾检测使用）

## 你的任务

### 任务 1: 按优先级继续追问（1-3 项）

当前仍未补充的、按优先级排序的缺失项如下:
{missing_by_priority}

追问规则:
- 每次最多追问 3 项
- 优先追问优先级最高的缺失项
- 追问要具体到数值或名称，不要笼统
  正确: "退火温度是多少？100°C 还是 150°C？"
  错误: "请提供更多参数"
- 如果用户上一轮的回答仍然模糊（如"温度挺高的"），
  继续追问同一个点直到明确，不要跳过
- 当所有优先级 1 和优先级 2 项已填满，可以开始问优先级 3

### 任务 2: 检测矛盾

检查用户最新消息与已收集信息之间是否存在以下矛盾:

**(A) 自矛盾 — 用户前后说法不一致**
检测方法: 比较用户最新消息与 context_summary 中已记录的值。
例如:
- "退火 500°C" vs 之前记录的 "退火 450°C"
- "用了 PTAA 做 HTL" vs 之前记录的 "Spiro-OMeTAD"
- "降解率 40%" vs 之前记录的 "降解率 92%"
如果发现矛盾，同时列出两个矛盾的来源，
让用户确认哪个是正确的。

**(B) 引用矛盾 — 用户说法与引用实验记录不一致**
检测方法: 比较用户最新消息与 references_detail 中引用实验的实际参数。
例如:
- 用户说"跟 EXP-003 完全一样的条件"
  但 EXP-003 记录中退火温度是 100°C，用户说 120°C
- 用户说"用 EXP-003 的方法"
  但改动了关键参数却没有明确说明
处理方式: 列出具体差异，询问用户"是有意改动还是记错了？"

**(C) 逻辑矛盾 — 状态与内容不匹配**
检测方法: 语义判断。
例如:
- status 标记为 "done" 但没有任何 results 数据
- status 标记为 "planned" 但描述了具体的实验结果数值
- 标记为"实验失败"但结论字段写了"效果很好"
处理方式: 指出矛盾，让用户澄清实际状态。

矛盾处理原则:
- ⚠️ 关键: 只指出矛盾，绝不自行为用户修正
- 报告矛盾时同时列出矛盾的双方（两个矛盾的来源）
- 用友善但明确的语气，不评价用户的表述能力
- 如果用户确认某个值是正确的那就接受

### 任务 3: 更新上下文

根据用户的最新消息，增量更新 context_update。
只更新用户在本轮中新提供的、之前未记录的信息。
不要重复填入 context_summary 中已有的内容。
不要编造用户未提及的值。

### 任务 4: 解析引用

精确引用: 如果用户新提到了 @EXP-xxx，
记录到 references_update。

模糊引用: 如果用户模糊引用了历史实验（无编号），
记录到 fuzzy_references_update，每项包含 raw_text。
后端会在本轮 LLM 返回后自动调用 /api/resolve-reference
进行匹配，下一轮将候选展示给用户确认。

## 输出格式

严格返回以下 JSON：
{
  "context_update": {
    "process_parameters": [
      {"step": "退火", "parameter": "退火温度", "setpoint": "100 °C", "actual": "", "deviation": ""}
    ],
    "sop": ["将前驱体溶液旋涂到 FTO 基底上"],
    "tags": ["perovskite"]
  },
  "references_update": ["EXP-005"],
  "fuzzy_references_update": [
    {"raw_text": "上次那个ZnO水热实验", "detected_in_turn": 2}
  ],
  "missing_after_update": ["器件有效面积", "电极厚度"],
  "contradictions": [
    {
      "type": "self|ref_mismatch|logic",
      "field": "退火温度",
      "claim_from": "用户第 1 轮说 120°C",
      "claim_against": "本轮说 100°C",
      "severity": "high|medium|low",
      "message": "我注意到你之前说退火温度是 120°C，但这轮说是 100°C。哪个是正确的？"
    }
  ],
  "reply": "好的，FK209-Co(III) 3 mol% 做 HTL 掺杂，明白了。\n\n不过我注意到一个小问题：你前面说退火 120°C，但这轮说 100°C。EXP-003 记录的是 100°C/30min，是不是应该按 EXP-003 的 100°C 来？\n\n另外还缺两个信息：\n1. 器件的有效面积是多少？\n2. Au 电极蒸镀的厚度？"
}

## 约束

- reply 必须用中文
- 追问和矛盾检测可以合并到同一条 reply 中
- 有矛盾时先指出矛盾，再追问缺失项
- 如果本轮没有检测到矛盾，contradictions 字段返回空数组 []
- 如果本轮没有新的上下文更新，context_update 返回空对象 {}
- 如果用户说"跟上次一样""参考之前"，不要追问那些参数，
  直接标记为从引用实验继承
──────────────────────────────────
USER PROMPT (动态拼接):
──────────────────────────────────
用户的最新消息：{user_message}

请分析并返回 JSON。
```

---

#### 阶段 3 — VERIFY（全 Schema 完整性检查）

**职责**：对照完整的 16 字段 Schema 逐项评估 → 计算 completeness → 判断是否 ready_to_generate。这是双轨制中"完整 Schema"轨道的主场。

```
SYSTEM PROMPT:
──────────────────────────────────
你是 Exdiary 实验记录助手。对话信息收集阶段即将结束。
请对照完整的实验记录结构，对当前已收集的信息进行
逐字段完整性评估。

## 当前状态

实验类型: {experiment_type}
对话轮次: {turn_count} / {max_turns}

已收集信息的完整上下文:
{context_full}

（这是对话中收集到的所有信息，以完整 JSON 形式呈现。
  空字段表示用户从未提及。）

## 评估任务

请对以下 16 个字段逐一评估，每个字段标记为以下四种状态之一:
- "filled":     已有足够信息，可以直接填入结构化记录
- "partial":    有部分信息，但不够完整（如材料有名称但缺纯度/厂家）
- "missing":    完全没有信息
- "na":         对此类实验不适用（如 XRD 实验不需要 materials 数组的"用量"字段）

### 完整字段清单

1.  title               — 实验标题
2.  date                — 实验日期
3.  experimenter        — 实验者
4.  status              — 实验状态 (planned|running|done|failed|repeated)
5.  tags                — 标签 (2-4 个受控词汇)
6.  purpose             — 实验目的 / 科学问题
7.  materials           — 材料与试剂
    子字段: name, purity, vendor, amount, notes
    至少 name 有值才算 partial；purity+vendor+amount 都有才算 filled
8.  equipment           — 仪器设备
    子字段: device, model, location
    至少 device 有值才算 partial
9.  experimental_plan   — 实验方案 / 分组
    子字段: group, condition, expected
    至少 1 行且 condition 有值才算 partial
10. sop                 — 操作步骤 (至少 2-3 个按时间顺序的关键步骤)
11. process_parameters  — 过程参数
    子字段: step, parameter, setpoint, actual, deviation
    至少 1 项且 parameter+setpoint 有值才算 partial
12. observations        — 异常与观察
    子字段: no_anomalies, items[]
    至少声明了 no_anomalies=true 或有 1 条 item 才算 partial
13. characterization    — 表征计划
    子字段: method, sample_id, preparation, submission_date, data_path
    至少 method 有值才算 partial
14. results             — 结果
    子字段: qualitative(定性观察), key_data[](关键数据), figures[](图表)
    至少 qualitative 有值或 1 条 key_data 才算 partial
15. conclusion          — 结论 (至少 1-2 句总结性陈述)
16. next_steps          — 下一步行动 (至少 1 项行动计划)

### 字段重要性分级

根据实验类型，将 16 个字段分为三级:

核心字段 (缺失会严重影响记录质量):
{core_fields}        ← 来自 PRIORITY_MAP 的优先级 1 项对应的 Schema 字段

重要字段 (缺失会影响后续分析):
{important_fields}   ← 来自 PRIORITY_MAP 的优先级 2 项对应的 Schema 字段

补充字段 (可以留空，不影响生成):
{optional_fields}    ← 如 experimenter, equipment.model, date(可默认为今天)

## 完整性评分

completeness = (
  核心字段中 filled 的数量 / 核心字段总数 × 0.5 +
  重要字段中 filled 的数量 / 重要字段总数 × 0.35 +
  补充字段中 filled 的数量 / 补充字段总数 × 0.15
)

## ready_to_generate 判断

满足以下任一条件时，ready_to_generate = true:

1. completeness >= 0.80
2. 所有核心字段状态均为 filled 或 partial，
   且所有重要字段至少为 partial
3. turn_count >= max_turns（超时强制进入生成阶段）
4. 用户在最近 2 轮中没有提供新的实质性信息
   （即 context 在最近 2 轮中没有实质性变化）

## 输出格式

严格返回以下 JSON：
{
  "field_status": {
    "title": {"status": "filled", "note": ""},
    "date": {"status": "missing", "note": "未提及，可默认为今天"},
    "experimenter": {"status": "missing", "note": "不影响生成"},
    "status": {"status": "partial", "note": "从上下文推断为 done，但用户未明确确认"},
    "tags": {"status": "filled", "note": ""},
    "purpose": {"status": "filled", "note": ""},
    "materials": {"status": "partial", "note": "3 种材料有名称，但均缺纯度和厂家"},
    "equipment": {"status": "missing", "note": "完全未提及"},
    "experimental_plan": {"status": "na", "note": "单一样品无分组"},
    "sop": {"status": "filled", "note": "6 个步骤，清晰完整"},
    "process_parameters": {"status": "partial", "note": "有退火温度/时间，缺旋涂转速"},
    "observations": {"status": "filled", "note": ""},
    "characterization": {"status": "partial", "note": "提到 SEM 和 XRD，缺具体参数"},
    "results": {"status": "partial", "note": "有定性描述，缺 JV 关键数据"},
    "conclusion": {"status": "missing", "note": "未给出明确结论"},
    "next_steps": {"status": "missing", "note": "未提及后续计划"}
  },
  "completeness": 0.72,
  "core_remaining": [],
  "important_remaining": ["材料纯度信息", "JV 关键性能数据"],
  "optional_remaining": ["实验者姓名", "仪器设备型号", "实验日期"],
  "ready_to_generate": true,
  "summary": "信息收集较为完整。核心字段均已填充。\n主要缺口: 材料纯度/厂家、JV 数据(PCE/Voc/Jsc/FF)、明确结论。\n这些可以在预览阶段手动补充。",
  "reply": "好的，信息收集得差不多了（完整度约 72%）。\n\n✅ 已明确: 器件结构、掺杂方案、退火条件、操作步骤\n⚠️ 还需补充: 材料纯度/厂家信息、JV 测试的关键数据（PCE、Voc、Jsc、FF）\n\n你可以选择:\n• 继续补充上述信息\n• 或者直接生成记录，缺失项在预览中手动填写"
}

## 约束

- reply 必须用中文，简洁清晰
- 16 个字段必须逐个评估，不能跳过
- 字段状态的判断要严格：用户模糊提到但没说清楚 → partial，不要说成 filled
- ready_to_generate 的判断优先采纳 4 条规则中满足的任一条
- 如果 completeness < 0.5 且 turn_count < max_turns - 1，
  ready_to_generate 应为 false（信息太少，继续追问）
- summary 是对用户说的话，不要用技术术语
- core_remaining / important_remaining / optional_remaining
  要列出具体的缺失内容，不是字段名
──────────────────────────────────
USER PROMPT (动态拼接):
──────────────────────────────────
（无需额外用户输入。此阶段由 Agent 自动触发，
  不需要用户发消息。）
```

---

#### 阶段 4 — EXTRACT（生成完整描述 → 结构化提取）

**职责**：将对话中收集的所有信息整合为一段完整、连贯的自然语言实验描述 → 用户确认 → 调用 `parse_notes()` 进行结构化提取。

##### 步骤 A：生成自然语言描述

```
SYSTEM PROMPT:
──────────────────────────────────
你是材料科学领域的学术写作助手。
基于一段对话中收集的实验信息，生成一篇完整、连贯的
自然语言实验描述。这段描述将作为实验的"原始笔记"存档，
也会被送入 AI 结构化提取器。

## 已收集信息

{context_full}

## 对话过程摘要

{conversation_summary}

## 引用关系

{references_list}

## 写作要求

### 风格
- 使用材料科学学术论文中"实验方法(Experimental Section)"的写作风格
- 客观、简洁、具体
- 使用被动语态（"将 TiO2 粉末分散于乙醇中"）
- 不使用"我""我们""本研究"等第一人称
- 避免口语化表达（"搞定""大概""差不多"）

### 结构
按以下顺序组织段落：

第一段 — 实验概述:
  一句话概括实验目的和总体方案。
  例: "为优化钙钛矿太阳能电池的空穴传输层性能，
       在 EXP-003 器件结构基础上，
       将 HTL 掺杂剂从 Li-TFSI 替换为 FK209-Co(III)。"

第二段 — 材料与试剂:
  列出所有使用的材料，包含名称、纯度、厂家、用量。
  格式: "PbI2 (纯度 99.99%, Sigma-Aldrich), MAI (纯度 99.5%, Dyesol)..."
  如果纯度或厂家未知，写"（纯度未记录）"而非跳过。

第三段 — 器件制备 / 实验步骤:
  按时间顺序描述操作步骤。
  关键参数在步骤中自然嵌入。
  例: "将 TiO2 浆料以 3000 rpm 旋涂 30 s 到 FTO 基底上，
       随后在 450 °C 下退火 2 h。"
  步骤之间用"随后""接着""最后"连接。

第四段 — 表征与测试:
  列出所有表征手段和关键测试条件。
  例: "采用 SEM (Hitachi S-4800) 观察薄膜表面形貌。
       J-V 曲线在 AM 1.5G 模拟太阳光 (100 mW/cm²)下测量，
       有效面积 0.1 cm²。"

第五段 — 结果与结论:
  如果对话中提到了结果数据，在此列出。
  简要总结实验结论。
  例: "最佳器件 PCE 达到 22.3%，Voc=1.15 V，
       Jsc=24.5 mA/cm²，FF=79.2%。"

### 引用标注
所有引用的实验用 @EXP-xxx 格式在正文中标注。
例: "器件结构参考 @EXP-003（FTO/SnO2/MAPbI3/Spiro-OMeTAD/Au）"

### 信息覆盖
- 所有 context_full 中有值的信息必须覆盖，不遗漏
- context_full 中为空的信息不要编造
- 如果某个字段信息不完整，如实写"（未记录）"或"（待补充）"
- 不要为了"完整"而虚构数据

### 长度
200-600 字，根据信息量调整。
信息少的不硬凑字数，信息多的不精简。

## 输出格式

严格返回以下 JSON：
{
  "title": "基于 HTL 掺杂剂优化的钙钛矿太阳能电池制备",
  "notes": "为优化钙钛矿太阳能电池的空穴传输层性能...(完整描述)"
}

## 约束

- notes 必须是完整的中文段落，不是列表
- 不要用 markdown 格式，用纯文本段落
  （可以适当用 • 或 → 等符号辅助排版）
- title 从 context 或对话中提取，如果用户没给明确的标题，
  用"实验目的 + 关键变量"的格式自动生成
──────────────────────────────────
USER PROMPT:
──────────────────────────────────
请根据以上信息生成完整的实验描述。
```

##### 步骤 B：结构化提取

步骤 A 输出的 `notes` 作为 `parse_notes(notes, llm)` 的输入（复用现有 `lib/parser.py`，无需修改），提取为 11 段结构化数据。

##### 步骤 C：前后端衔接

前端收到结构化数据后，进入现有的 `showPreview(data)` 预览面板。用户可以在预览中编辑任意字段，确认后保存。这一步完全复用现有的 `/api/parse/confirm` 流程。

---

#### 阶段流转的触发条件

```
阶段 1 (INTENT) → 阶段 2 (DETAIL):
  自动流转。Agent 首轮完成意图识别后自动进入追问模式。

阶段 2 (DETAIL) → 阶段 3 (VERIFY):
  满足以下任一条件:
  - 连续 2 轮中 missing 列表为空（所有优先级项已填）
  - turn_count >= max_turns - 2（预留最后 2 轮给 VERIFY）
  - 用户发送了包含"够了""可以了""生成吧"的消息

阶段 3 (VERIFY) → 阶段 4 (EXTRACT):
  - ready_to_generate = true（由 VERIFY 阶段判断）
  - 或 ready_to_generate = false 但用户主动要求生成

阶段 4 (EXTRACT) → 前端预览:
  自动流转。提取完成后直接将结构化数据返回前端。
```

### 3.4 轮次控制策略

```python
MAX_TURNS = 6

class TurnController:
    def should_end(self, agent_state: AgentState) -> tuple[bool, str]:
        """
        返回 (是否应该结束对话, 结束原因)
        """
        # 条件1: 用户主动要求结束
        if agent_state.user_wants_done:
            return True, "user_requested"

        # 条件2: 完整性达标
        if agent_state.completeness >= 0.85:
            return True, "complete"

        # 条件3: 超过最大轮次
        if agent_state.turn_count >= MAX_TURNS:
            return True, "max_turns"

        # 条件4: 最近两轮无实质性进展
        if self._no_progress(agent_state):
            return True, "no_progress"

        return False, ""

    def generate_wrap_up(self, state: AgentState) -> str:
        """对话结束时生成收尾消息，列出仍未填的字段"""
        if not state.missing:
            return "信息已完整，正在生成实验记录..."
        lines = ["好的，以下字段未完整填写，将在草稿中标注："]
        for m in state.missing:
            lines.append(f"• {m}")
        lines.append("\n你可以稍后在预览中补充。正在生成记录...")
        return "\n".join(lines)
```

### 3.5 矛盾检测实现

矛盾检测面临一个核心难题：**参数名称不统一**。

用户可能说"退火温度 500°C"，引用实验 EXP-003 中记录的可能是"热退火参数: 450°C"或"退火温度参数: 450°C"或"annealing temp: 450°C"。硬编码 `["退火温度", "煅烧温度", "旋涂转速"]` 去匹配是行不通的——这些名称的变体在代码看来是完全不同的字符串，但语义完全相同。

因此矛盾检测采用 **Python 做确定性的结构检查 + LLM 做语义性的参数比对** 的分工。

#### 3.5.1 分工边界

```
Python 确定性检查（不需 LLM，零幻觉）:
  ✅ 引用实验是否存在
  ✅ process_parameters 数组是否为空
  ✅ 两个值在参数名完全相同时是否不同（精确字符串匹配的 bonus）
  ✅ status 字段的枚举值是否合法

LLM 语义检查（处理名称变体和复杂逻辑）:
  ✅ "退火温度" vs "热退火参数" vs "annealing temp" 是否指同一参数
  ✅ 同一参数的不同表述间的数值比对
  ✅ 逻辑矛盾（status=done 但没有结果数据）
  ✅ 术语归一化
```

关键原则：**Python 不猜参数名称的等价性，这项工作全部交给 LLM。**

#### 3.5.2 Python 确定性检查

```python
def detect_contradictions_deterministic(
    context: dict,
    experiment_store,
    references: list[str]
) -> list[dict]:
    """
    仅做确定性检查：引用有效性 + 精确字符串匹配 + 结构完整性。
    不做任何参数名称的语义等价判断。
    """
    contradictions = []

    for ref_id in references:
        ref_exp = experiment_store.load(ref_id)
        if not ref_exp:
            contradictions.append({
                "type": "broken_reference",
                "ref_id": ref_id,
                "message": f"引用的实验 {ref_id} 不存在，请检查编号"
            })
            continue

        # 精确字符串匹配：只有当参数名完全相同时才比对值
        user_params = context.get("process_parameters", [])
        ref_params = ref_exp.get("process_parameters", [])

        for up in user_params:
            u_name = (up.get("parameter") or "").strip()
            u_val = (up.get("setpoint") or "").strip()
            if not u_name or not u_val:
                continue
            for rp in ref_params:
                r_name = (rp.get("parameter") or "").strip()
                r_val = (rp.get("setpoint") or "").strip()
                # 精确字符串匹配（不尝试语义等价）
                if u_name == r_name and u_val != r_val:
                    contradictions.append({
                        "type": "ref_mismatch_exact",
                        "param": u_name,
                        "user_value": u_val,
                        "ref_value": r_val,
                        "ref_id": ref_id,
                    })

    return contradictions
```

这个函数只产生 **exact 类型** 的矛盾——"退火温度"对"退火温度"的精确比对。它不会尝试把"热退火参数"和"退火温度"识别为同一个参数。

#### 3.5.3 LLM 语义检查（在 DETAIL 阶段 Prompt 中完成）

真正的参数名称归一化和跨实验比对在阶段 2 的 LLM prompt 中完成。关键设计：

**（A）references_detail 中传入引用实验的原始参数列表**

在阶段 2 的 USER PROMPT 中，`{references_detail}` 不是简单列出引用实验的 ID，而是包含完整的参数列表（用原文的字段名）：

```
已解析的引用实验详情:

@EXP-003: 水热法合成ZnO纳米棒
  状态: done
  材料: Zn(NO3)2·6H2O (纯度 99%, Sigma)
  process_parameters:
    - 热退火参数: 450 °C
    - 保温时长: 2 h
    - 升温速率: 5 °C/min
  results: 降解率 92%
  ...

@EXP-005: ZnO掺杂实验
  process_parameters:
    - 退火温度: 500 °C
    - 烧结时间: 3 h
  ...
```

这样 LLM 在收到用户说"退火温度 500°C"时，能够对照 EXP-003 的"热退火参数: 450°C"和 EXP-005 的"退火温度: 500°C"，自行判断哪些参数名称指代同一个概念。

**（B）contradictions 输出的来源整合**

Agent 在 DETAIL 阶段每轮结束时，将两类矛盾合并：

```python
def collect_contradictions(
    llm_response: dict,          # LLM 返回的 contradictions 数组
    deterministic: list[dict],   # Python 确定性检查的结果
) -> list[dict]:
    """合并两类矛盾，去重"""
    all_contradictions = list(deterministic)

    # LLM 发现的矛盾（已包含参数名归一化后的比对）
    for c in llm_response.get("contradictions", []):
        # 避免与确定性检查的结果重复
        if c.get("type") == "ref_mismatch_exact":
            continue
        all_contradictions.append(c)

    return all_contradictions
```

**（C）阶段 2 prompt 中矛盾检测的增强指令**

阶段 2 的 SYSTEM PROMPT 中关于矛盾检测的部分已包含参数归一化的指引（在完整 prompt 设计中的"任务 2: 检测矛盾 → (B) 引用矛盾"段），核心指令是：

```
比较用户最新消息与 references_detail 中引用实验的实际参数。

注意: 参数名称可能以不同形式出现。以下名称指代同一概念，
请将其视为同一参数进行比对:
  退火温度 = 热退火温度 = 热退火参数 = 退火温度参数 = annealing temperature
  煅烧温度 = 焙烧温度 = 烧结温度 = calcination temperature
  旋涂转速 = 旋转涂布转速 = spin coating speed
  ... (根据具体实验上下文中出现的参数灵活判断)

如果发现实质相同的参数但数值不同，报告矛盾。
如果不确定两个参数名是否指同一概念，不要强行关联，
在 reply 中向用户确认。
```

LLM 的天然优势就是处理这种同义词——它不需要我们穷举所有可能的名称变体，而是从语义层面理解"热退火参数"和"退火温度"可能指同一个东西。

#### 3.5.4 完整调用流程

```
DETAIL 阶段每轮处理:

1. Python 确定性检查
   → detect_contradictions_deterministic(context, store, references)
   → 得到 exact 类型矛盾（如有）

2. 构建 LLM prompt
   → 注入 references_detail（含引用实验的完整 process_parameters）
   → 注入 deterministic 结果作为已知矛盾
   → 注入参数归一化指引

3. LLM 返回
   → 包含新的语义矛盾（如有）
   → 参数名称已由 LLM 归一化

4. 合并
   → collect_contradictions(llm_response, deterministic)
   → 得到完整矛盾列表

5. Agent reply
   → 如果有矛盾（无论来源），在 reply 中展示
   → 用户确认或修正
```

### 3.6 模糊引用解析机制

Agent 设计中最容易被忽略但最容易出错的一环。用户表述中有一类"想引用但没说清编号"的模糊引用——"上次的ZnO实验""跟老张那回一样"——如果不能精确解析为 `@EXP-xxx`，后续的矛盾检测和引用关系存储都无法正常工作。

#### 3.6.1 模糊引用 vs 精确引用

| 类型 | 用户输入示例 | 可立即解析 | 存储位置 |
|------|------------|----------|---------|
| 精确引用 | `@EXP-005` | ✅ 是 | `AgentState.references` |
| 精确引用 | `@(EXP-005)` | ✅ 是 | `AgentState.references` |
| 模糊引用 | `上次那个ZnO水热实验` | ❌ 需匹配 | `AgentState.fuzzy_references` |
| 模糊引用 | `跟老张做钙钛矿那次一样` | ❌ 需匹配 | `AgentState.fuzzy_references` |
| 模糊引用 | `跟上次一样（EXP-003）` | ✅ 括号内是精确的 | `AgentState.references` |

#### 3.6.2 AgentState 扩展

```python
@dataclass
class AgentState:
    # ... 原有字段 ...
    references: list[str] = field(default_factory=list)
    # 新增: 待解析的模糊引用
    fuzzy_references: list[dict] = field(default_factory=list)
    # 格式: [
    #   {
    #     "raw_text": "上次那个ZnO水热实验",
    #     "detected_in_turn": 2,
    #     "status": "pending|resolved|failed",
    #     "candidates": [  # 解析后填充
    #       {"id": "EXP-003", "title": "水热法合成ZnO纳米棒", "score": 0.92}
    #     ],
    #     "resolved_id": ""  # 用户确认后填充
    #   }
    # ]
```

#### 3.6.3 四步处理流程

```
用户: "合成方法跟上次那个ZnO水热实验一样，掺杂改了"
               │
      ┌────────▼─────────────────────────────┐
      │ 步骤 1: 检测（阶段 1 或 2 的 LLM 完成）  │
      │                                         │
      │ LLM 识别出模糊引用 → 提取 raw_text        │
      │ → 放入 fuzzy_references 列表              │
      │ → status = "pending"                     │
      └────────┬────────────────────────────┐
               │                            │
               │ 如果用户同时给了            │
               │ 精确引用 @EXP-003:          │
               │ → 直接放入 references      │
               │ → 跳过步骤 2-3             │
               │                            │
      ┌────────▼─────────────────────────────┐
      │ 步骤 2: 解析（Python 确定性调用）        │
      │                                         │
      │ 触发时机: 阶段 2 每轮 LLM 返回后          │
      │          或阶段 2 → 3 转换时              │
      │                                         │
      │ 调用 App 已有的 /api/resolve-reference   │
      │ → POST {text: "ZnO水热实验"}            │
      │ → 返回 top-3 候选实验列表                │
      │                                         │
      │ 解析策略（已有）:                         │
      │ ① 正则匹配 EXP-xxx（精确）               │
      │ ② 本地关键词搜索 + 打分（快速）           │
      │ ③ LLM 语义匹配（兜底，处理"上次"等       │
      │    时间提示词和自然语言描述）             │
      └────────┬────────────────────────────┘
               │
      ┌────────▼─────────────────────────────┐
      │ 步骤 3: 确认（Agent 展示给用户）         │
      │                                         │
      │ 在 Agent 的 reply 中展示候选：            │
      │                                         │
      │ "关于'上次那个ZnO水热实验'，找到以下     │
      │  可能的匹配:                             │
      │  • EXP-003: 水热法合成ZnO纳米棒          │
      │    (2025-04-12, 标签: hydrothermal)      │
      │  • EXP-006: ZnO纳米棒形貌调控            │
      │    (2025-05-02, 标签: hydrothermal)      │
      │                                         │
      │  是 EXP-003 吗？还是都不是？"            │
      │                                         │
      │ Agent 同时提供快捷回复按钮:               │
      │  [是 EXP-003] [是 EXP-006] [都不是]      │
      └────────┬────────────────────────────┘
               │ 用户点击确认
      ┌────────▼─────────────────────────────┐
      │ 步骤 4: 归一化                           │
      │                                         │
      │ 用户确认 →                                │
      │  fuzzy_references[i].status = "resolved" │
      │  fuzzy_references[i].resolved_id = "EXP-003" │
      │  references.append("EXP-003")            │
      │                                         │
      │ 后端在阶段 4 生成自然语言描述时，          │
      │ 将原文中的模糊引用替换为 @EXP-003         │
      │ 并在 references 字段中存储                │
      └─────────────────────────────────────────┘
```

#### 3.6.4 各阶段的职责分配

| 阶段 | 对模糊引用做什么 |
|------|----------------|
| **INTENT** | LLM 检测模糊引用 → 提取 raw_text → 放入 `fuzzy_references`；如果有精确引用则直接放入 `references` |
| **DETAIL** | 每轮 LLM 返回后，Python 代码自动调用 `resolve-reference` API 解析所有 status=pending 的模糊引用；将候选结果注入下一轮 prompt 的 `references_detail` 中，同时 Agent reply 中展示候选让用户确认 |
| **VERIFY** | 检查是否仍有 status=pending 的模糊引用；若存在且用户多次未确认（如连续 2 轮忽略），在 summary 中提醒；若有未解析成功的（status=failed），标注为"未找到匹配实验" |
| **EXTRACT** | 生成自然语言描述时，将所有已解析的模糊引用替换为 `@EXP-xxx`；未解析的保留原文措辞并标注 `（引用未确认）` |

#### 3.6.5 关键设计决策

**为什么不让 LLM 直接解析模糊引用？** 因为 LLM 不知道你的实验库里有哪些实验。即使把实验列表 JSON 注入 prompt，实验数量多时（50+条）会撑爆上下文。所以解析必须在 Python 端调用 `/api/resolve-reference` 完成，该端点已经实现了本地搜索 + LLM 兜底两层策略。

**如果用户同时给了模糊引用和精确引用？** 精确引用优先级更高。如果用户说"跟上次ZnO实验一样，参考 @EXP-003"，Agent 检查 `@EXP-003` 是否有效——有效则忽略模糊引用；无效则两个都保留待确认。

**模糊引用的"上次""最近"等时间词如何处理？** `/api/resolve-reference` 中的 LLM 兜底逻辑会将时间提示词（"上次""最近""老张那回"）转化为对实验日期排序的偏好。例如"上次" → 按日期降序排列候选，最近日期的实验排在前面。

**如果用户连续 2 轮不确认模糊引用？** Agent 在下一轮中重新展示候选，语气更直接："我还在等你的确认——上次的ZnO实验是 EXP-003 吗？"如果第三轮仍不确认，Agent 标记 status=failed，继续收集其他信息，不在这一点上卡住对话。

---

### 3.7 引用实验数据注入上下文

#### 问题

debug 日志暴露：用户说"跟 EXP-2026-001 完全一样"，INTENT 正确解析了 `references=["EXP-2026-001"]`。DETAIL 阶段 LLM 通过 `references_detail` 看到了 EXP-2026-001 的完整数据（7 步 SOP、6 项参数、结果 92%、结论），但只回复"好的，将与 EXP-2026-001 完全一致"，没有通过 `context_update` 把数据写回 context。VERIFY 阶段看到空 context，给出 `completeness=0.28`。

根因：引用数据在 prompt 的 `references_detail` 区域，不在 `context_summary` 里。LLM 把引用数据当作"参考信息"，而非"当前对话已收集的数据"。

#### 方案：把引用数据注入上下文摘要

不做 Python 自动填充。LLM 自己判断哪些字段该继承。

**DETAIL 阶段**：`_build_context_summary()` 末尾追加引用实验的完整数据（SOP、参数、结果、结论）。同时 DETAIL prompt 新增明确指令——"如果用户说与引用实验一致，你必须将引用数据通过 `context_update` 写入，不能只在 reply 中口头确认。"

**VERIFY 阶段**：`_stage_verify()` 的 `context_full` 同样追加引用数据，LLM 评估完整性时可以判定对应字段为"已填充"。

#### 实现

```python
# _build_context_summary() 末尾
if self.state.references:
    lines.append("--- 引用实验数据（用户说与此一致，你可自行判断填入 context_update）---")
    lines.append(self._build_references_detail())

# _stage_verify() 中
if self.state.references:
    context_full += "\n===== 引用实验数据 =====\n" + self._build_references_detail()
```

DETAIL prompt 约束新增：
```
如果用户说"跟 EXP-xxx 一样""完全一致"，你必须将引用实验详情中
匹配的字段通过 context_update 填入，不能只在 reply 中口头确认。
例如: 引用实验有 7 步 SOP 和 6 项参数，用户说"完全一样"，
则 context_update 必须包含这些 sop 和 process_parameters。
如果用户说"跟 EXP-xxx 一样但换了掺杂剂"，
则继承除掺杂剂相关字段外的所有字段。
```

#### 效果

用户"跟 EXP-2026-001 完全一样"后：
- context_summary 包含 EXP-2026-001 的完整数据
- DETAIL LLM 看到富上下文 → `context_update` 填充 SOP、参数、结果
- VERIFY LLM 看到富 context_full → completeness 从 0.28 提升至 ~0.80

用户"跟 EXP-003 一样但换了掺杂剂"后：
- context_summary 同样包含 EXP-003 的完整数据
- DETAIL LLM 在 `context_update` 中继承除掺杂剂外的所有字段
- 掺杂剂相关字段留空，等待用户补充或 LLM 追问

---

### 3.8 API 路由设计

```python
# app.py 新增

@app.route("/api/agent/start", methods=["POST"])
def api_agent_start():
    """初始化 Agent 对话，返回第一条消息"""
    agent = ExperimentAgent(get_extract_llm(), store)
    reply = agent.start()
    return jsonify({"ok": True, "state": agent.state_to_dict(), "reply": reply})

@app.route("/api/agent/message", methods=["POST"])
def api_agent_message():
    """处理用户消息，返回 Agent 响应"""
    data = request.get_json()
    user_msg = data.get("message", "")
    state_dict = data.get("state", {})

    agent = ExperimentAgent.from_dict(get_extract_llm(), store, state_dict)
    reply, stage_change = agent.process_message(user_msg)

    return jsonify({
        "ok": True,
        "state": agent.state_to_dict(),
        "reply": reply,
        "stage": agent.state.stage,
        "completeness": agent.state.completeness,
        "should_extract": stage_change == "extract",
        "quick_replies": agent.get_quick_replies(),
    })

@app.route("/api/agent/extract", methods=["POST"])
def api_agent_extract():
    """执行结构化提取"""
    data = request.get_json()
    state_dict = data.get("state", {})

    agent = ExperimentAgent.from_dict(get_extract_llm(), store, state_dict)
    notes = agent.generate_notes()          # 生成完整自然语言描述
    result = parse_notes(notes, get_extract_llm())  # 现有提取逻辑
    result["original_notes"] = notes
    result["id"] = store.next_id()

    return jsonify({"ok": True, "data": result, "notes": notes})

@app.route("/api/agent/confirm", methods=["POST"])
def api_agent_confirm():
    """确认保存（复用现有 /api/parse/confirm 逻辑）"""
    # 直接委托给现有端点
    return api_parse_confirm()
```

---

## 四、前端设计详解

### 4.1 双模式切换

`new.html` 改造为双模式布局：

```
┌─────────────────────────────────────────┐
│  [🤖 对话模式]  [🖊 自由书写]            │  ← 模式切换标签
├─────────────────────────────────────────┤
│                                         │
│  当前模式的内容区域                       │
│  （对话面板 或 Quill 编辑器）              │
│                                         │
└─────────────────────────────────────────┘
```

- **对话模式**（默认）：聊天界面，Agent 引导
- **自由书写模式**：现有的 Quill 编辑器，保留所有现有功能
- 切换时保留对方模式的内容（对话历史不丢，编辑器内容不丢）

### 4.2 对话面板 HTML 结构

```html
<div id="chat-panel">
  <!-- 消息列表 -->
  <div id="chat-messages">
    <!-- Agent 消息 -->
    <div class="chat-msg agent">
      <div class="chat-avatar">🧪</div>
      <div class="chat-bubble markdown-content">{{ reply }}</div>
    </div>
    <!-- 用户消息 -->
    <div class="chat-msg user">
      <div class="chat-bubble">{{ user_msg }}</div>
    </div>
  </div>

  <!-- 进度指示器 -->
  <div id="chat-progress" style="display:none">
    <div class="progress-bar">
      <div class="progress-fill" style="width: 65%"></div>
    </div>
    <small>信息完整度 65% · 第 3 轮</small>
  </div>

  <!-- 快捷回复按钮 -->
  <div id="quick-replies">
    <button data-action="skip">⏭ 足够了，直接生成</button>
    <button data-action="quill">🖊 切到自由书写</button>
    <button data-action="detail">📋 补充细节</button>
  </div>

  <!-- 输入区域 -->
  <div id="chat-input-area">
    <textarea id="chat-input" placeholder="输入实验描述..."
              rows="2"></textarea>
    <button id="btn-send">发送</button>
  </div>
</div>
```

### 4.3 对话面板 CSS

```css
#chat-panel {
  display: flex;
  flex-direction: column;
  height: 65vh;
  min-height: 400px;
  border: 1px solid var(--pico-card-border-color, #ddd);
  border-radius: 12px;
  overflow: hidden;
  background: var(--pico-card-background-color);
}

#chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
}

.chat-msg {
  display: flex;
  gap: 0.5rem;
  max-width: 88%;
}

.chat-msg.agent {
  align-self: flex-start;
}

.chat-msg.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.chat-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 1rem;
}

.chat-msg.agent .chat-avatar {
  background: #e8f4fd;
}

.chat-msg.user .chat-avatar {
  background: #e8e8e8;
}

.chat-bubble {
  padding: 0.6rem 0.9rem;
  border-radius: 12px;
  font-size: 0.9rem;
  line-height: 1.5;
}

.chat-msg.agent .chat-bubble {
  background: #f0f4f8;
  border-top-left-radius: 4px;
}

.chat-msg.user .chat-bubble {
  background: var(--pico-primary);
  color: #fff;
  border-top-right-radius: 4px;
}

#chat-input-area {
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem;
  border-top: 1px solid #e0e0e0;
  background: var(--pico-background-color);
}

#chat-input {
  flex: 1;
  resize: none;
  font-size: 0.9rem;
  padding: 0.5rem 0.7rem;
  border-radius: 8px;
  border: 1px solid #ccc;
  line-height: 1.5;
  font-family: inherit;
}

#chat-input:focus {
  border-color: var(--pico-primary);
  outline: none;
}

.progress-bar {
  height: 4px;
  background: #e0e0e0;
  border-radius: 2px;
  margin-bottom: 0.3rem;
}

.progress-fill {
  height: 100%;
  background: var(--pico-primary);
  border-radius: 2px;
  transition: width 0.3s ease;
}

#quick-replies {
  display: flex;
  gap: 0.4rem;
  padding: 0.4rem 0.75rem;
  flex-wrap: wrap;
}

#quick-replies button {
  font-size: 0.75rem;
  padding: 0.25rem 0.6rem;
  border-radius: 14px;
  border: 1px solid #ccc;
  background: #f5f5f5;
  cursor: pointer;
  transition: all 0.15s;
}

#quick-replies button:hover {
  background: var(--pico-primary);
  color: #fff;
  border-color: var(--pico-primary);
}
```

### 4.4 对话 JS 核心逻辑

```javascript
// ===== Agent Chat Controller =====
var _agentState = null;      // 服务端返回的完整状态
var _isStreaming = false;    // 防止重复提交

async function startAgent() {
  showTyping();
  let r = await fetch('/api/agent/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({})
  });
  let data = await r.json();
  hideTyping();
  if (data.ok) {
    _agentState = data.state;
    appendMessage('agent', data.reply);
    updateProgress(data.state.completeness || 0, data.state.turn_count || 1);
    updateQuickReplies(data.quick_replies || []);
  }
}

async function sendMessage() {
  let input = document.getElementById('chat-input');
  let msg = input.value.trim();
  if (!msg || _isStreaming) return;
  input.value = '';

  appendMessage('user', msg);
  showTyping();
  _isStreaming = true;

  try {
    let r = await fetch('/api/agent/message', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg, state: _agentState})
    });
    let data = await r.json();
    _isStreaming = false;
    hideTyping();

    if (data.ok) {
      _agentState = data.state;
      appendMessage('agent', data.reply);
      updateProgress(data.completeness || 0, data.state?.turn_count || 1);
      updateQuickReplies(data.quick_replies || []);

      if (data.should_extract) {
        await doExtract();
      }
    }
  } catch(e) {
    _isStreaming = false;
    hideTyping();
    appendMessage('agent', '抱歉，出了点问题。请重试。');
  }
}

async function doExtract() {
  appendMessage('agent', '正在生成实验记录...');
  let r = await fetch('/api/agent/extract', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({state: _agentState})
  });
  let data = await r.json();
  if (data.ok) {
    // 切换到预览面板（复用现有的 showPreview）
    _extractedData = data.data;
    showPreview(data.data);
    document.getElementById('chat-panel').style.display = 'none';
    document.getElementById('preview-section').style.display = '';
  }
}

function appendMessage(role, content) {
  let container = document.getElementById('chat-messages');
  let div = document.createElement('div');
  div.className = 'chat-msg ' + role;
  div.innerHTML = `
    <div class="chat-avatar">${role === 'agent' ? '🧪' : '👤'}</div>
    <div class="chat-bubble markdown-content">${marked.parse(content)}</div>
  `;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function updateProgress(completeness, turn) {
  let bar = document.getElementById('chat-progress');
  if (completeness > 0) {
    bar.style.display = '';
    bar.querySelector('.progress-fill').style.width = (completeness * 100) + '%';
    bar.querySelector('small').textContent =
      `信息完整度 ${Math.round(completeness * 100)}% · 第 ${turn || 1} 轮`;
  }
}

function updateQuickReplies(replies) {
  let container = document.getElementById('quick-replies');
  container.innerHTML = replies.map(function(r) {
    let action = typeof r === 'string' ? r : r.action;
    let label = typeof r === 'string' ? r : r.label;
    return `<button data-action="${action}" onclick="handleQuickReply('${action}')">${label}</button>`;
  }).join('');
}

function handleQuickReply(action) {
  switch(action) {
    case 'skip':
      sendQuickMessage('足够了，请直接生成实验记录。');
      break;
    case 'quill':
      switchToQuill();
      break;
    case 'detail':
      document.getElementById('chat-input').focus();
      break;
    default:
      sendQuickMessage(action);
  }
}

async function sendQuickMessage(msg) {
  document.getElementById('chat-input').value = msg;
  await sendMessage();
}

function showTyping() {
  document.getElementById('btn-send').disabled = true;
  let indicator = document.createElement('div');
  indicator.className = 'chat-msg agent typing-indicator';
  indicator.innerHTML = '<div class="chat-avatar">🧪</div><div class="chat-bubble">...</div>';
  document.getElementById('chat-messages').appendChild(indicator);
}

function hideTyping() {
  document.getElementById('btn-send').disabled = false;
  let indicator = document.querySelector('.typing-indicator');
  if (indicator) indicator.remove();
}
```

### 4.5 模式切换逻辑

```javascript
var _chatMode = true;  // 默认对话模式

function switchToQuill() {
  _chatMode = false;
  document.getElementById('chat-panel').style.display = 'none';
  document.getElementById('editor-section').style.display = '';
  document.getElementById('mode-tab-agent').classList.remove('active');
  document.getElementById('mode-tab-quill').classList.add('active');
}

function switchToAgent() {
  _chatMode = true;
  document.getElementById('chat-panel').style.display = '';
  document.getElementById('editor-section').style.display = 'none';
  document.getElementById('mode-tab-agent').classList.add('active');
  document.getElementById('mode-tab-quill').classList.remove('active');
  if (!_agentState) {
    startAgent();
  }
}

// 从 Quill 模式切到 Agent 时，把编辑器内容作为第一条消息
function switchToAgentWithContent() {
  switchToAgent();
  let html = quill.root.innerHTML;
  if (html && html.trim()) {
    let text = quill.getText().trim();
    if (text && text.length > 10) {
      // 直接发送 Quill 内容作为初始消息
      startAgentWithMessage(text);
    }
  }
}
```

---

## 五、数据流

### 5.1 完整流程

```
用户打开 /new
    │
    ├─→ [默认] 对话模式启动
    │     │
    │     ├─→ POST /api/agent/start
    │     │     └─→ Agent 返回第一条消息
    │     │
    │     ├─→ 用户输入 → POST /api/agent/message
    │     │     ├─→ Agent 更新状态、检测矛盾
    │     │     └─→ 返回追问 / 确认消息
    │     │
    │     ├─→ ... 多轮循环 (3-6轮) ...
    │     │
    │     ├─→ 完整性达标 或 用户跳过
    │     │     └─→ Agent 自行进入 EXTRACT 阶段
    │     │
    │     ├─→ POST /api/agent/extract
    │     │     ├─→ 生成完整自然语言描述
    │     │     └─→ 调用 parse_notes() 结构化提取
    │     │
    │     └─→ 返回结构化数据 → 前端显示预览面板
    │           │
    │           └─→ 用户确认 → POST /api/agent/confirm → 保存
    │
    └─→ [可选] 切换到自由书写模式
          └─→ 复用现有 Quill → /api/parse 流程
```

### 5.2 Agent 状态在前后端之间传递

由于 HTTP 是无状态的，Agent 状态通过 JSON 在前后端之间来回传递：

```
前端                         后端
 │                            │
 │── state={...} ─────────────→│ 后端从 JSON 重建 AgentState
 │   message="换了掺杂剂"      │
 │                            │ agent.process_message(...)
 │←─ state={...} ──────────────│ 后端返回更新后的状态
 │   reply="好的，什么掺杂剂？" │
 │   completeness=0.45         │
 │                            │
```

每次 `/api/agent/message` 请求都携带完整状态，后端处理后返回新状态。这避免了服务端会话管理，也支持状态在 `sessionStorage` 中持久化（页面刷新不丢对话）。

---

## 六、与现有系统的集成点

### 6.1 复用关系

| 现有模块 | 复用方式 |
|---------|---------|
| `lib/llm.py` — `LLMClient` | Agent 直接使用，每个阶段调 `llm.analyze()` |
| `lib/parser.py` — `parse_notes()` | 阶段 4 生成描述后调用 |
| `lib/parser.py` — `EXPERIMENT_SCHEMA` | 阶段 4 结构化提取使用 |
| `lib/storage.py` — `ExperimentStore` | 解析引用、矛盾检测时读取已有实验 |
| `templates/new.html` — 预览面板 | 提取完成后复用 `showPreview()` + 现有的虚线框编辑 |
| `/api/parse/confirm` — 保存逻辑 | 直接委托，处理引用关系 + 图片移动 |

### 6.2 路由兼容

现有的 `/api/parse` 和 Quill 模式完全保留。Agent 路由是新增的，不影响现有功能。用户可以在两种模式间自由切换。

---

## 七、实施计划

实施顺序按依赖关系排列：每一阶段的产出是下一阶段的输入。总预计约 1200 行新增代码（后端 600 行 + 前端 400 行 + HTML/CSS 200 行）。

---

### 阶段 1: `lib/agent.py` — 数据模型 + 基础框架

> **目标**：AgentState、PRIORITY_MAP、参数别名表、ExperimentAgent 骨架。此阶段不调 LLM，纯 Python 逻辑可独立测试。
>
> **依赖**：`lib/llm.py`（LLMClient 已存在）、`lib/storage.py`（ExperimentStore 已存在）

| 步骤 | 文件 | 内容 | 行数 |
|------|------|------|------|
| 1.1 | `lib/agent.py` | `AgentConfig` 类：`max_turns=6`, `completeness_threshold=0.8`, `model="deepseek-v4-flash"` | 15 |
| 1.2 | 同上 | `PRIORITY_MAP` 字典：9 种实验类型，每种三级优先级参数列表（来自 3.3 节阶段 1 prompt 中的清单） | 50 |
| 1.3 | 同上 | `PARAM_ALIASES` 字典：参数名称归一化映射表（如 `"退火温度": ["热退火参数", "热退火温度", "退火温度参数", "annealing temperature"]`）。仅用于确定性检查中的 bonus 精确匹配，不替代 LLM 的语义判断 | 20 |
| 1.4 | 同上 | `AgentState` 数据类（含 `fuzzy_references` 字段），`DEFAULT_CONTEXT` 常量，`state_to_dict()` / `from_dict()` 序列化方法 | 60 |
| 1.5 | 同上 | `TurnController` 类：`should_end()`（4 条规则）、`generate_wrap_up()`、`_no_progress()` | 40 |
| 1.6 | 同上 | `ExperimentAgent.__init__(llm_client, experiment_store, config)` — 构造函数 | 15 |

**验证**：
```bash
python -c "
from lib.agent import AgentState, PRIORITY_MAP, AgentConfig, TurnController
# 序列化往返测试
s = AgentState(stage='intent')
d = s.state_to_dict()
s2 = AgentState.from_dict(d)
assert s2.stage == 'intent'
# 优先级清单完整性测试
for exp_type in ['photocatalysis','hydrothermal','sol-gel','spin-coating',
    'ball-milling','electrochemistry','xrd','perovskite-solar','other']:
    assert exp_type in PRIORITY_MAP
    assert len(PRIORITY_MAP[exp_type]) == 3  # 三级优先级
print('OK')
"
```

---

### 阶段 2: `lib/agent.py` — Prompt 模板 + LLM 调用

> **目标**：4 个阶段的 system prompt（以类常量形式存储），`_safe_parse_json()` 容错解析，每个阶段的 LLM 调用方法。
>
> **依赖**：阶段 1 完成

| 步骤 | 文件 | 内容 | 行数 |
|------|------|------|------|
| 2.1 | `lib/agent.py` | 4 个 prompt 常量：`PROMPT_INTENT`、`PROMPT_DETAIL`、`PROMPT_VERIFY`、`PROMPT_EXTRACT`（直接复制 3.3 节中的完整 prompt 文本，`{变量}` 占位符用 Python `str.format()` 替换） | 350 |
| 2.2 | 同上 | `_safe_parse_json(raw, stage)` — 处理 LLM 输出被 markdown 代码块包裹的情况；解析失败时抛出明确异常 | 25 |
| 2.3 | 同上 | `_build_context_summary()` — 将 `AgentState.context`（完整 dict）压缩为 150-200 tokens 的自然语言摘要（Python 字符串格式化，不调 LLM） | 40 |
| 2.4 | 同上 | `_build_references_detail()` — 读取引用实验的 YAML 文件，提取关键参数（用原始字段名），构造供阶段 2 prompt 注入的文本块 | 30 |
| 2.5 | 同上 | `_call_llm(system_prompt, user_prompt)` — 封装 `self.llm.analyze()`，统一错误处理 | 10 |
| 2.6 | 同上 | `start()` — 构造阶段 1 的 prompt → 调用 LLM → 解析 response → 初始化 AgentState → 返回第一条 reply | 25 |

**验证**：
```bash
python -c "
from lib.agent import ExperimentAgent, AgentConfig
from lib.llm import LLMClient
# 使用一个假的 API key 测试 prompt 拼接不报错
# (不实际调 LLM，只验证字符串格式化)
agent = ExperimentAgent.__new__(ExperimentAgent)
# 验证四个 prompt 模板中的变量占位符都能被 DEFAULT_CONTEXT 填充
import json
ctx = json.dumps(agent.DEFAULT_CONTEXT, ensure_ascii=False)
for prompt_name in ['PROMPT_INTENT', 'PROMPT_DETAIL', 'PROMPT_VERIFY', 'PROMPT_EXTRACT']:
    p = getattr(ExperimentAgent, prompt_name)
    try:
        p.format(context_summary='test', context_full=ctx, user_message='test',
                 experiment_type='photocatalysis', turn_count=1, max_turns=6,
                 references_detail='', references_list='', missing_by_priority='',
                 conversation_summary='')
        print(f'{prompt_name}: OK')
    except KeyError as e:
        print(f'{prompt_name}: MISSING KEY {e}')
"
```

---

### 阶段 3: `lib/agent.py` — 状态机主循环

> **目标**：`process_message()` 根据当前 stage 路由到对应方法；各阶段方法实现完整业务逻辑；矛盾检测和模糊引用解析集成。
>
> **依赖**：阶段 2 完成

| 步骤 | 文件 | 内容 | 行数 |
|------|------|------|------|
| 3.1 | `lib/agent.py` | `process_message(user_msg)` — 状态机主循环：读取 `self.state.stage` → 路由到 `_stage_xxx()` → 调用 LLM → 解析 JSON → 更新 state → 判断是否进入下一阶段 → 返回 `(reply, stage_changed)` | 30 |
| 3.2 | 同上 | `_stage_intent(user_msg)` — 构造阶段 1 prompt → 调用 LLM → 解析 `experiment_type`/`context_update`/`missing`/`references`/`fuzzy_references`/`reply` → 合并到 state | 25 |
| 3.3 | 同上 | `_stage_detail(user_msg)` — 构造阶段 2 prompt（注入 `context_summary`、`references_detail`、优先级缺失列表）→ 调用 LLM → 解析 `context_update`/`missing_after_update`/`contradictions`/`references_update`/`fuzzy_references_update`/`reply` → 更新 state | 35 |
| 3.4 | 同上 | `detect_contradictions_deterministic()` — Python 端确定性检查：引用实验存在性 + 精确字符串匹配的参数值比对（3.5.2 节代码） | 30 |
| 3.5 | 同上 | `resolve_fuzzy_references()` — 遍历 `state.fuzzy_references` 中 status=pending 的条目 → 调用 `self.store.list_all_full()` 加载实验列表 → 执行本地关键词搜索（复制 `/api/resolve-reference` 的第二层逻辑）→ 填充 `candidates` 列表。不在此函数中调 LLM（LLM 兜底在 app.py 路由中已有） | 45 |
| 3.6 | 同上 | `_stage_verify()` — 构造阶段 3 prompt（注入 `context_full`、`core_fields`/`important_fields`/`optional_fields` 分级列表）→ 调用 LLM → 解析 `field_status`/`completeness`/`ready_to_generate`/`summary`/`reply` → 更新 state | 30 |
| 3.7 | 同上 | `_stage_extract()` — 构造阶段 4 prompt → 调用 LLM → 解析 `title`/`notes` → 存入 `state.final_notes` → 返回结构化数据 | 20 |

**验证**：
```bash
python -c "
from lib.agent import ExperimentAgent, AgentConfig
from lib.llm import LLMClient
from lib.storage import ExperimentStore
import os
# 集成测试：需要一个有效的 API key
api_key = os.environ.get('DEEPSEEK_API_KEY', '')
if not api_key:
    print('SKIP: no API key')
else:
    llm = LLMClient(api_key=api_key, model='deepseek-v4-flash')
    store = ExperimentStore('experiments')
    agent = ExperimentAgent(llm, store, AgentConfig())
    # 测试第一阶段
    reply = agent.start()
    assert agent.state.stage == 'intent'
    print(f'Agent: {reply}')
    # 测试第二轮
    reply2, changed = agent.process_message('做了钙钛矿电池，参考EXP-003，换了HTL掺杂剂')
    print(f'Agent: {reply2}')
    print(f'Stage: {agent.state.stage}, Completeness: {agent.state.completeness}')
"
```

---

### 阶段 4: `app.py` — Agent API 路由

> **目标**：4 个新路由，连接前端对话面板与后端 Agent。
>
> **依赖**：阶段 3 完成

| 步骤 | 文件 | 内容 | 行数 |
|------|------|------|------|
| 4.1 | `app.py` | 在 `get_extract_llm()` 后新增 `get_agent_llm()` — Agent 对话用 flash 模型 | 5 |
| 4.2 | 同上 | `POST /api/agent/start` — 创建 ExperimentAgent 实例 → 调 `agent.start()` → 返回 `{ok, state, reply}` | 15 |
| 4.3 | 同上 | `POST /api/agent/message` — 接收 `{message, state}` → `ExperimentAgent.from_dict()` 重建实例 → `agent.process_message(message)` → 返回 `{ok, state, reply, stage, completeness, should_extract, quick_replies}` | 25 |
| 4.4 | 同上 | `POST /api/agent/extract` — 接收 `{state}` → 重建实例 → 调 `agent._stage_extract()` → 调 `parse_notes(notes, llm)` → 返回 `{ok, data, notes}` | 15 |
| 4.5 | 同上 | `POST /api/agent/confirm` — 委托到现有 `api_parse_confirm()`（复用引用关系处理 + 图片移动逻辑） | 3 |

**验证**：
```bash
# 启动 Flask 后:
curl -X POST http://127.0.0.1:5000/api/agent/start \
  -H "Content-Type: application/json" -d '{}'

# 应返回 Agent 的第一条消息和初始 state
```

---

### 阶段 5: `templates/new.html` — HTML 结构改造

> **目标**：将单一 Quill 编辑器布局改为"对话模式 + Quill 模式"双面板布局。保留所有现有功能不变。
>
> **依赖**：阶段 4 完成（需要 API 路由先存在）

| 步骤 | 文件 | 内容 | 行数 |
|------|------|------|------|
| 5.1 | `templates/new.html` | 在 `{% block content %}` 开头新增模式切换标签栏 HTML：两个 `<span>` 标签（"对话模式"/"自由书写"），默认选中对话模式 | 10 |
| 5.2 | 同上 | 新增对话面板 `#chat-panel` HTML：消息列表 `#chat-messages` + 进度指示器 `#chat-progress` + 快捷回复按钮 `#quick-replies` + 输入区域 `#chat-input-area`（textarea + 发送按钮）。放在模式切换栏下方 | 40 |
| 5.3 | 同上 | 将现有 `#editor-section`（Quill 编辑器 + "生成记录"按钮 + 字符计数）包裹在 `#quill-mode` div 中，默认 `display:none` | 5 |
| 5.4 | 同上 | 保留现有 `#preview-section`（预览面板）不变 | 0 |

**验证**：
- 打开 `http://127.0.0.1:5000/new` → 看到对话面板（默认）
- 点击"自由书写"标签 → 切换到 Quill 编辑器
- 点击"对话模式"标签 → 切换回对话面板

---

### 阶段 6: `templates/new.html` — 对话面板 CSS

> **目标**：对话面板的完整样式，模拟消息应用的视觉体验。
>
> **依赖**：阶段 5 完成

| 步骤 | 文件 | 内容 | 行数 |
|------|------|------|------|
| 6.1 | `templates/new.html` 的 `<style>` 块 | 模式切换标签栏样式：`.mode-tabs`、`.mode-tab`、`.mode-tab.active` | 15 |
| 6.2 | 同上 | 对话面板容器样式：`#chat-panel` — flex column、固定高度、边框、圆角 | 15 |
| 6.3 | 同上 | 消息列表样式：`#chat-messages` — flex column、overflow-y auto、padding | 5 |
| 6.4 | 同上 | 单条消息样式：`.chat-msg`（flex row）、`.chat-msg.agent`（左对齐、浅灰气泡）、`.chat-msg.user`（右对齐、主色气泡）、`.chat-avatar`（圆形头像）、`.chat-bubble`（圆角气泡、内边距） | 50 |
| 6.5 | 同上 | 进度指示器样式：`#chat-progress`、`.progress-bar`、`.progress-fill`（过渡动画） | 15 |
| 6.6 | 同上 | 输入区域样式：`#chat-input-area`（border-top、flex row）、`#chat-input`（flex 1、resize none、圆角）、`#btn-send` | 15 |
| 6.7 | 同上 | 快捷回复按钮样式：`#quick-replies`（flex row、gap）、每个按钮（圆角 pill、hover 变色） | 10 |
| 6.8 | 同上 | 移动端适配：`@media (max-width: 576px)` 中调整 `#chat-panel` 高度、`.chat-bubble` 字体大小 | 10 |

---

### 阶段 7: `templates/new.html` — 对话面板 JS 逻辑

> **目标**：对话面板的完整交互逻辑。
>
> **依赖**：阶段 6 完成

| 步骤 | 文件 | 内容 | 行数 |
|------|------|------|------|
| 7.1 | `templates/new.html` 的 `<script>` 块 | `startAgent()` — 调 `POST /api/agent/start` → 接收 reply + state → 展示第一条 Agent 消息 → 更新进度条 → 更新快捷回复按钮 | 20 |
| 7.2 | 同上 | `sendMessage()` — 读取 `#chat-input` 内容 → 添加用户消息到列表 → 调 `POST /api/agent/message`（携带 message + 完整 state）→ 添加 Agent 消息到列表 → 更新进度条/快捷回复 → 如果 `should_extract` 则调 `doExtract()` | 35 |
| 7.3 | 同上 | `doExtract()` — 调 `POST /api/agent/extract` → 收到结构化数据 → 调现有 `showPreview(data)` → 隐藏对话面板、显示预览面板 | 15 |
| 7.4 | 同上 | `appendMessage(role, content)` — 创建 `.chat-msg` DOM 元素，Agent 消息走 `marked.parse()` 渲染 Markdown → 自动滚动到底部 | 15 |
| 7.5 | 同上 | `updateProgress(completeness, turn)` — 更新进度条宽度 + 文字 | 10 |
| 7.6 | 同上 | `updateQuickReplies(replies)` — 根据服务端返回动态渲染快捷按钮 | 10 |
| 7.7 | 同上 | `handleQuickReply(action)` — 处理 "skip"/"quill"/"detail" 等快捷操作 | 15 |
| 7.8 | 同上 | `switchToQuill()` / `switchToAgent()` / `switchToAgentWithContent()` — 模式切换函数 | 20 |
| 7.9 | 同上 | 输入框 Enter 发送（Shift+Enter 换行）、发送按钮 click 绑定 | 10 |
| 7.10 | 同上 | 在页面加载时自动调用 `startAgent()`（如果是默认对话模式） | 5 |

**验证**（完整交互流程）：
1. 打开 `/new` → 自动弹出 Agent 第一条消息
2. 输入 "做了钙钛矿电池" → 发送 → Agent 追问缺失信息
3. 继续回答 3-4 轮 → 进度条从 30%→60%→85%
4. 完整性达标 → 自动展示预览面板
5. 预览中修改字段 → 确认保存 → 跳转到详情页
6. 在步骤 2-4 中任意时刻点击"自由书写" → 切换到 Quill → 编辑器可用
7. 从 Quill 切回对话 → 对话历史完整保留

---

### 阶段 8: 集成调试与边界打磨

> **目标**：处理跨阶段的边界情况、错误恢复、状态持久化。
>
> **依赖**：阶段 7 完成

| 步骤 | 内容 | 验证方法 |
|------|------|---------|
| 8.1 | **LLM 返回非 JSON 时的容错**：`_safe_parse_json()` 覆盖 markdown 代码块包裹、尾部逗号、缺失字段三种情况 | 手动构造 3 种畸形 JSON 输入，确认不崩溃 |
| 8.2 | **API 超时处理**：LLM 调用超过 30 秒 → 前端显示"正在思考..."并轮询；后端设置 timeout | 限速模式或大 context 场景下测试 |
| 8.3 | **对话状态 sessionStorage 持久化**：页面刷新时从 `sessionStorage` 恢复 AgentState，对话不丢失 | 对话到第 3 轮 → 刷新页面 → 对话历史 + state 完整恢复 |
| 8.4 | **空输入防护**：用户发送空白消息 → Agent 回复"请描述你的实验"而非调用 LLM | 前端 + 后端双重校验 |
| 8.5 | **引用实验被删除后的处理**：Agent 解析到已删除的 EXP-xxx → 矛盾检测报告 broken_reference → reply 提示用户 | 手动删除一个被引用的实验 YAML → 重新对话 |
| 8.6 | **连续 3 轮无进展**：`TurnController._no_progress()` 检测 → 强制进入 VERIFY 阶段 → 生成尽力而为的草稿 | 连续回复"嗯""好的""知道了" → 观察 Agent 是否强制收束 |
| 8.7 | **移动端对话面板**：375px 宽度下对话面板无水平溢出、气泡宽度自适应、输入区域不遮挡键盘 | Chrome DevTools 手机模式 |

---

### 实施依赖图

```
阶段 1 (数据模型)
  ↓
阶段 2 (Prompt 模板)
  ↓
阶段 3 (状态机主循环)
  ↓
阶段 4 (API 路由) ←── 可以开始并行开发前端
  ↓                          ↓
阶段 5 (HTML 结构)    阶段 6 (CSS) ←── 并行
  ↓                    ↓
阶段 7 (JS 逻辑) ←─────┘
  ↓
阶段 8 (集成打磨)
```

阶段 1-4 必须串行（后端）。阶段 5 和 6 可以并行开发。阶段 7 依赖 5+6。阶段 8 在所有功能完成后进行。

---

## 八、风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| LLM 追问过于啰嗦 | 轮次上限 + "足够了"快捷按钮 |
| 矛盾检测误报 | 只报告确定性高的矛盾，语义矛盾的阈值设高 |
| API 成本过高 | 使用 flash 模型做对话，pro 模型做最终提取 |
| 对话质量依赖 prompt | 每个阶段的 prompt 预留调优空间，分离到常量 |
| 用户不适应对话模式 | 保留 Quill 快速通道，双模式平等并排 |
| 状态在前后端间传递的性能 | AgentState 序列化只传必要字段，最轻量 JSON |
| 对话历史过长撑爆 context | 阶段之间精炼上下文，只传 "已收集信息摘要" 而非完整历史 |

---

## 九、未决问题

以下问题需要在开发过程中根据实际使用反馈决定：

1. **Agent 初始消息**：应该直接问"今天做了什么实验？"还是展示 8 个模板让用户选择？
2. **矛盾检测的 Severity 分级**：严重矛盾阻止生成 vs 轻微矛盾仅提示？
3. **多人协作下的 Agent 行为**：不同用户的表述习惯是否需要学习/适配？
4. **Agent 对话历史是否需要持久化到实验记录中**？（作为"实验笔记的生成过程"附录）
5. **是否需要支持在对话中上传图片**？（"把这张 SEM 图传上来"）

---

## 十、与 VISION.md 的一致性检查

- ✅ "帮你省时间，而非替你思考" — Agent 追问细节是省时间，不替代判断
- ✅ "schema-on-read" — 阶段 4 仍使用现有 parse_notes，不在对话中预设 schema
- ✅ "提取结果需人工确认" — 预览面板完整保留，对话结束后仍需确认
- ✅ "AI 提问而非 AI 下结论" — Agent 设计就是提问模式
- ⚠️ "分析能力由提问驱动" — Agent 追问内容可能隐含分析引导，需注意边界

---

*本文档随实施进展持续更新。每个阶段完成后标注完成时间和实际工作量。*
