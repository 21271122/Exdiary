# Exdiary Agent v2 — 基于 Tool Calling 的对话式实验记录系统

## 一、为什么重写

v1 四阶段状态机在实际对话中暴露了结构性问题：

| 问题 | 表现 |
|------|------|
| 阶段切换由 Python 硬编码 | LLM 无法根据对话情况自主决定下一步 |
| 四个独立 prompt 上下文断裂 | 每阶段重新拼接 prompt，信息在阶段间流失 |
| 单向流水线不可逆 | VERIFY 发现 completeness=0.28，LLM 想回头追问但代码不允许 |
| Schema 藏在 Python 代码里 | LLM 看不到当前收集进度，凭记忆判断，对话长了就忘 |
| 引用数据注入不稳定 | 依赖 prompt 约束，LLM 有时"说"会继承但不写入 context_update |

v2 将控制权还给 LLM，给它一套工具，由它自主决定每一步。同时把 Schema 从 Python 隐形状态提升为 messages 中 LLM 可见的常驻信息。

---

## 二、核心架构

```
用户输入
  │
  ▼
LLM 决定: 需要加载引用吗？
  │ 是 → 调 load_reference → 结果写回 messages
  │ 否 → 跳过
  ▼
LLM 比对 messages 中已有信息（Schema 状态 + 引用实验数据）：
  ├─ 有矛盾 → 先通过 ask_user 或自然语言向用户求证
  └─ 无矛盾 → 调 update_schema(fields) 写入
  │
  ▼
Python 执行 update_schema:
  ├─ 合并 fields 到 context
  └─ Schema 状态摘要注入 messages（system 角色）
  │
  ▼
LLM 看 messages: 当前 Schema 状态 + 缺失字段 + 优先级清单(system prompt 中)
  → 决定: ask_user？ 输出结束文本？ 再调 load_reference？
```

**核心差异 vs v2 初版**：

| | v2 初版 | v2 改进版 |
|---|---|---|
| Schema 在哪 | Python `loop.context`，LLM 看不到 | Python `loop.context` + messages 中 system 消息，LLM 可见 |
| 完整性评估 | 单独 `assess_completeness` tool，LLM 主动调用 | Schema 状态摘要常驻 messages，实时可见，无需调用 |
| 矛盾检测 | Python 精确匹配 + LLM 语义检测（两个独立 tool） | LLM 写入前自行比对 messages 中的 Schema 状态和引用数据，Python 不参与 |
| 追问依据 | LLM 凭记忆 + system prompt 中的清单 | 缺什么直接看 messages 中的 Schema 状态 |
| LLM 负担 | 需记忆收集了什么、还缺什么 | 读 messages 即可 |

---

## 三、工具定义

### 3.1 工具目录

| # | 工具名 | 作用 | 暂停等用户 |
|---|--------|------|----------|
| T1 | `load_reference` | 加载引用实验的完整数据 | 否 |
| T2 | `search_experiments` | 模糊搜索历史实验库 | 否 |
| T3 | `update_schema` | 将确认的信息写入 Schema，写入后自动更新 Schema 状态 | 否 |
| T4 | `ask_user` | 向用户提问 | 是 |

> 无独立 `finalize` tool。结束条件: LLM 输出纯文本(未调任何 tool) + Python 检查核心字段已填 → 触发提取，跳转预览。用户主动说"够了""直接生成"走同一条路径。

### 3.2 工具 Schema

#### T1: load_reference

```json
{
  "name": "load_reference",
  "description": "加载引用实验的完整数据（SOP、参数、结果、结论等）。用户说'跟EXP-xxx一样''复现EXP-xxx'或模糊描述'上次的ZnO实验'时调用。结果写回messages，你可以据此判断哪些字段可直接继承。",
  "parameters": {
    "type": "object",
    "properties": {
      "refs": {
        "type": "array",
        "items": {"type": "string"},
        "description": "实验编号（如EXP-2026-003）或模糊描述（如'上次的ZnO实验'）"
      }
    },
    "required": ["refs"]
  }
}
```

#### T2: search_experiments

```json
{
  "name": "search_experiments",
  "description": "在历史实验库中模糊搜索。当用户用自然语言描述历史实验但未给编号时调用。返回候选列表。如用户随后确认了某个候选，再调 load_reference 加载。",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "搜索关键词或自然语言描述"
      }
    },
    "required": ["query"]
  }
}
```

#### T3: update_schema

```json
{
  "name": "update_schema",
  "description": "将本轮确认的信息写入Schema。写入后系统自动更新messages中的Schema状态摘要。注意: messages中已有当前Schema状态和引用实验数据，写入前请自行比对——新值与已有数据矛盾时，先向用户求证再写入，不要写入矛盾值后又覆盖。",
  "parameters": {
    "type": "object",
    "properties": {
      "fields": {
        "type": "object",
        "properties": {
          "title": {"type": "string"},
          "date": {"type": "string"},
          "experimenter": {"type": "string"},
          "status": {"type": "string", "enum": ["planned","running","done","failed","repeated"]},
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
          "next_steps": {"type": "array", "items": {"type": "string"}}
        },
        "description": "要更新的字段。增量更新——只传变化的，不传整个Schema。如果字段为空列表[]或空对象{}，表示清空该字段。"
      },
      "round_summary": {
        "type": "string",
        "description": "一句话描述本轮收集/确认了哪些信息（用于日志）"
      }
    },
    "required": ["fields"]
  }
}
```

#### T4: ask_user

```json
{
  "name": "ask_user",
  "description": "向用户提问。一次最多3个问题。问题应具体、可回答。依据是什么该问: 看messages中Schema状态的缺失字段 + system prompt中的优先级清单，自己决定问什么、问几个。如果缺失的都是补充字段，可以跳过直接结束。",
  "parameters": {
    "type": "object",
    "properties": {
      "questions": {
        "type": "array",
        "items": {"type": "string"},
        "maxItems": 3
      }
    },
    "required": ["questions"]
  }
}
```

---

## 四、System Prompt 设计

```
你是 Exdiary 实验记录助手。你与用户对话，逐步收集实验信息，
最终生成完整的结构化实验记录。

## 工作方式

你有 4 个工具。收到用户消息后:

1. 如果用户引用了历史实验 → 先调 load_reference（或 search_experiments 搜索）。
   不确定的引用先向用户确认，不要盲猜。

2. 因为每轮可能有多个对话来回，对于用户提供的信息，调用 update_schema 写入。
   如加载了引用且用户说"完全一样"，将引用实验的匹配字段整批写入。
   如用户说"xxx一样但改了yyy"，继承未改动的字段，改动字段等用户提供。

3. 写入后系统自动更新 Schema 状态到 messages 中。
   根据 Schema 状态判断: 如果关键字段还有缺失 → 调 ask_user 追问。
   追问看两点: Schema 状态中的缺失字段 + 各类实验的优先级(见下方)。
   自己决定问什么、问几个。不要一次问太多。

4. 如果 Schema 状态显示关键字段基本齐备 → 输出自然语言文本
   （如"差不多了，我来整理一下"），系统检测到你不再调工具
   且核心字段已填，自动触发提取。

4a. 如果用户主动说"够了""直接生成""就这样"等 → 同样先判断
   核心字段是否已填。已填则输出确认文本并结束。未填则
   追问最后1-2个关键项，不要盲目结束生成残缺记录。

## 实验 Schema（16 字段）

最终要填充的字段如下。messages 中的 Schema 状态会实时反映哪些已填、哪些缺失:

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

## 行为准则

- 中文回复，友好、具体
- 不要编造任何用户未提及的信息
- 用户说"跟EXP-xxx一样""完全一致"时，通过 update_schema 把引用实验数据写入
- 一次追问不超过3项，优先问高优先级的缺失字段
```

---

## 五、Agent Loop 实现

### 5.1 核心循环

```python
class AgentLoop:
    def __init__(self, llm_client, experiment_store):
        self.llm = llm_client
        self.store = experiment_store
        self.context = deepcopy(DEFAULT_CONTEXT)
        self.history = []           # [{role, content, tool_calls?, tool_call_id?}]
        self.references = []        # 已加载的引用实验 ID
        self.experiment_type = "other"  # LLM 通过 load_reference 或用户输入确定
        self.turn_count = 0

    def run(self, user_message: str = "") -> dict:
        if user_message:
            self.history.append({"role": "user", "content": user_message})
            self.turn_count += 1

        while True:
            messages = [{"role": "system", "content": SYSTEM_PROMPT}, *self.history]
            response = self.llm.client.chat.completions.create(
                model=self.llm.model,
                messages=messages,
                tools=TOOLS_OPENAI_FORMAT,
                temperature=0.3,
            )
            msg = response.choices[0].message

            # LLM 输出纯文本 → 不再调工具
            if msg.content and not msg.tool_calls:
                self.history.append({"role": "assistant", "content": msg.content})
                # 核心字段已填 → 触发提取；否则只是给用户的文本回复
                if self._core_fields_filled():
                    return {"type": "extract", "context": self.context}
                return {"type": "reply", "message": msg.content,
                        "context": self.context}

            # LLM 调用了工具
            for tc in msg.tool_calls:
                name = tc.function.name
                args = json.loads(tc.function.arguments)
                result = self._execute_tool(name, args)

                # 记录 tool 调用和结果
                self.history.append({
                    "role": "assistant", "content": None,
                    "tool_calls": [tc],
                })
                self.history.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })

                # ask_user 暂停循环等用户
                if name == "ask_user":
                    return {"type": "reply", "message": self._format_questions(args),
                            "context": self.context}

            # 其他工具执行完 → 继续循环

    def _core_fields_filled(self) -> bool:
        """检查核心字段是否已填充。核心字段按实验类型从 PRIORITY_MAP 的
        P1+P2 项映射到 Schema 字段名。非 other 类型至少需 purpose,materials,
        sop,process_parameters 中有值。"""
        CORE_BY_TYPE = {
            "photocatalysis": ["purpose","materials","process_parameters","results"],
            "hydrothermal": ["purpose","materials","sop","process_parameters","results"],
            "sol-gel": ["purpose","materials","sop","process_parameters","results"],
            "spin-coating": ["purpose","materials","sop","process_parameters","results"],
            "ball-milling": ["purpose","materials","sop","process_parameters","results"],
            "electrochemistry": ["purpose","materials","process_parameters","results"],
            "xrd": ["purpose","materials","process_parameters","results"],
            "perovskite-solar": ["purpose","materials","sop","process_parameters","results"],
        }
        core = CORE_BY_TYPE.get(self.experiment_type, ["purpose","materials","sop","results"])
        return all(self._is_filled(self.context.get(f)) for f in core)
```

### 5.2 update_schema 执行逻辑

纯写入操作。矛盾检测由 LLM 在调用前自行完成（比对 messages 中的 Schema 状态和引用实验数据）。

```python
def _execute_update_schema(self, fields, round_summary, loop):
    # 1. 合并到 context
    loop.context = merge_context(loop.context, fields)

    # 2. 生成 Schema 状态并注入 messages
    status_msg = self._build_schema_status(loop)
    loop.history.append({
        "role": "system",
        "content": status_msg,
    })

    return {
        "status": "ok",
        "updated_fields": list(fields.keys()),
    }
```

### 5.3 Schema 状态摘要生成

```python
def _build_schema_status(self, loop) -> str:
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
        val = loop.context.get(key)
        if self._is_filled(val):
            filled.append(f"{label}({self._brief(val)})")
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

def _brief(self, val) -> str:
    """字段值的简短描述"""
    if isinstance(val, list):
        return f"{len(val)}项" if val else "空"
    if isinstance(val, dict):
        has = sum(1 for v in val.values() if v)
        return f"{has}子字段" if has else "空"
    if isinstance(val, str):
        return val[:15] + ("..." if len(val) > 15 else "")
    return "有" if val else "空"

def _is_filled(self, val) -> bool:
    if val is None:
        return False
    if isinstance(val, list):
        return len(val) > 0
    if isinstance(val, dict):
        return any(v for v in val.values() if v)
    if isinstance(val, str):
        return val.strip() != ""
    return bool(val)
```

### 5.4 Messages 快照示例

经过 3 轮对话后，messages 中的 Schema 状态看起来是这样：

```
[system] 你是 Exdiary 实验记录助手...
[user]   复现了exp-2026-001
[assistant, tool_call: load_reference(refs=["EXP-2026-001"])]
[tool]   {"loaded": {"EXP-2026-001": {"title":"TiO2光催化...","sop":[...],...}}}
[user]   完全一样，直接生成吧
[assistant, tool_call: update_schema(fields={title:"...",sop:[...],process_parameters:[...]})]
[tool]   {"status":"ok","updated_fields":["title","sop","process_parameters","results","conclusion"]}
[system] [Schema状态] 已填充 11/16 字段
         已填: 标题(...), 状态(planned), 标签(3项), 目的(...), 材料(3项),
               步骤(7项), 参数(6项), 观察(已确认无异常), 表征(2项),
               结果(定性+1项数据), 结论(...)
         缺失: 日期, 实验者, 设备, 方案, 下一步
         提示: 缺失项多为补充字段，可考虑结束收集。
```

LLM 下一轮看到的就是这个。它自己判断：缺失的都是补充字段，关键字段全在，
输出文本（不再调 tool）→ Python 检查 `_core_fields_filled()`=True → 触发提取。

### 5.5 Messages 持久化

完整的 messages 数组（system prompt + 每轮 user/assistant/tool/system 消息）是对话的完整记录，也是调试的核心数据源。每轮 `run()` 结束时将其保存到磁盘。

```python
import json, os
from datetime import datetime
from pathlib import Path

class AgentLoop:
    def __init__(self, llm_client, experiment_store, debug_dir=None):
        ...
        self.debug_dir = debug_dir or (
            Path(experiment_store.path) / "_debug" /
            datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        os.makedirs(self.debug_dir, exist_ok=True)

    def _save_messages(self):
        """每轮结束后保存精简快照（截断过长内容）"""
        filepath = self.debug_dir / f"turn_{self.turn_count:03d}.json"
        compact = []
        for m in self.history:
            entry = {"role": m["role"]}
            if m.get("content"):
                c = m["content"]
                entry["content"] = c if len(c) <= 3000 else c[:3000] + "..."
            if m.get("tool_calls"):
                entry["tool_calls"] = [
                    {"name": tc["function"]["name"],
                     "arguments": tc["function"]["arguments"][:500]}
                    for tc in m["tool_calls"]
                ]
            if m.get("tool_call_id"):
                entry["tool_call_id"] = m["tool_call_id"]
            compact.append(entry)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(compact, f, ensure_ascii=False, indent=2)

    def _save_final_messages(self):
        """对话结束时保存完整 messages（不截断）"""
        filepath = self.debug_dir / "final_messages.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.history, f, ensure_ascii=False, indent=2, default=str)
```

保存时机：
- 每轮 `run()` 返回前调 `_save_messages()` — 精简快照（content>3000截断，arguments>500截断）
- 对话结束（`type == "extract"`）时调 `_save_final_messages()` — 完整保存，不截断

输出目录示例：
```
experiments/_debug/20260517_120000/
├── turn_001.json       # 第1轮: system prompt + user + assistant(tool_call) + tool + system(Schema)
├── turn_002.json       # 第2轮: user + assistant(tool_call) + tool + system(Schema)
├── turn_003.json       # 第3轮: user + assistant(文本, 表示结束)
├── final_messages.json # 完整 messages，无截断
```

---

## 六、容错设计

### 6.1 参数校验

```python
def _execute_tool(self, name, args):
    if name not in self.registry:
        return {"error": "unknown_tool", "message": f"未知工具 '{name}'"}
    schema = self.registry[name]
    for key in schema.get("required", []):
        if key not in args:
            return {"error": "missing_required", "message": f"缺少必要参数 '{key}'"}
    for key, val in args.items():
        expected = schema["properties"].get(key, {}).get("type")
        if expected == "array" and not isinstance(val, list):
            args[key] = [val]  # 宽容转换
        elif expected == "string" and isinstance(val, (int, float)):
            args[key] = str(val)
    try:
        return self._do_execute(name, args)
    except Exception as e:
        return {"error": "execution_failed", "message": str(e)[:300]}
```

### 6.2 防无限循环

同工具连续 3 次返回 error → 跳出循环，返回友好消息。

### 6.3 extract 回退

`parse_notes()` 失败时，从 `loop.context` 确定性构造预览数据（零 LLM）。

---

## 七、API 路由

3 个路由（比 v1 少 1 个）：

```
POST /api/agent/start     → 创建 AgentLoop → run("") → 返回初始消息
POST /api/agent/message   → 从 state 重建 AgentLoop → run(msg) → 返回 reply/extract
POST /api/agent/confirm   → 委托到 api_parse_confirm（复用）
```

`/api/agent/extract` 删除。提取逻辑在 message 路由中——当 `run()` 返回 `type: "extract"` 时，从 `loop.context` 生成描述 → 调 `parse_notes()` → 失败则回退确定性构造。

---

## 八、前端适配

对话面板 HTML/CSS **不变**。`sendMessage()` 判断从 `data.should_extract` 改为 `data.type === "extract"`。`doAgentExtract()` 删除。

---

## 九、实施计划

### 阶段 1: `lib/agent_v2.py`（约 500 行）

| 步骤 | 内容 |
|------|------|
| 1.1 | 4 个 tool 的 OpenAI function calling schema |
| 1.2 | `ToolExecutor` 类：注册表 + `_execute_tool()`（参数校验 + 宽容转换 + 执行异常捕获） |
| 1.3 | `_execute_update_schema()`：合并 context → 生成 Schema 状态注入 messages（纯写入，矛盾由 LLM 写入前自行比对） |
| 1.4 | `_build_schema_status()`：生成 Schema 状态摘要文本 |
| 1.5 | `_execute_load_reference()`：EXP ID 正则匹配 → 直接加载；否则模糊搜索 → 加载 |
| 1.6 | `_execute_search_experiments()`：本地关键词搜索（bigram + 标签匹配） |
| 1.7 | `SYSTEM_PROMPT` 常量（~2500 chars） |
| 1.8 | `AgentLoop` 类：`__init__()`, `run()`, `_core_fields_filled()` |
| 1.9 | Messages 持久化：`_save_messages()` 每轮精简快照 + `_save_final_messages()` 最终完整保存 |
| 1.10 | 防无限循环：同工具连续 3 次 error → 跳出 |

### 阶段 2: `app.py`（约 80 行）

| 步骤 | 内容 |
|------|------|
| 2.1 | 改造 `/api/agent/start` |
| 2.2 | 改造 `/api/agent/message`：从 state 重建 AgentLoop → `run(msg)` → 分辨 type |
| 2.3 | 删除 `/api/agent/extract` |
| 2.4 | `type == "extract"` 时：`_build_notes_from_context()` → `parse_notes()` → 失败回退 `_build_result_from_context()` |

### 阶段 3: `templates/new.html`（约 20 行）

| 步骤 | 内容 |
|------|------|
| 3.1 | `sendMessage()` 改为检查 `data.type` 而非 `data.should_extract` |
| 3.2 | 删除 `doAgentExtract()` |

### 阶段 4: 测试

| 步骤 | 内容 |
|------|------|
| 4.1 | Mock 测试：模拟 tool call 响应，验证循环和容错 |
| 4.2 | Mock 测试：连续 3 次错误 → 跳出 |
| 4.3 | Mock 测试：parse_notes 失败 → 回退 |
| 4.4 | 真实对话："复现了EXP-2026-001" → load_reference + update_schema 继承全量数据 |
| 4.5 | 真实对话："做了钙钛矿但换掺杂" → load_reference → update_schema 继承非掺杂字段 |
| 4.6 | 真实对话：模糊引用"上次的ZnO实验" → search_experiments → 向用户确认 → load_reference |
