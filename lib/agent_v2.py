"""
Exdiary Agent v2 — 基于 Tool Calling 的对话式实验记录系统

LLM 自主决策流程，Python 仅执行工具和注入 Schema 状态。
"""

import json, os, re, sys, traceback
from copy import deepcopy
from datetime import datetime
from pathlib import Path

# ============================================================================
# Step 1.1: Tool Definitions (OpenAI function calling format)
# ============================================================================

TOOL_LOAD_REFERENCE = {
    "type": "function",
    "function": {
        "name": "load_reference",
        "description": (
            "加载引用实验的完整数据（SOP、参数、结果、结论等）。"
            "用户说'跟EXP-xxx一样''复现EXP-xxx'或模糊描述'上次的ZnO实验'时调用。"
            "结果写回messages，你可以据此判断哪些字段可直接继承。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "实验编号（如EXP-2026-003）或模糊描述（如'上次的ZnO实验'）",
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
            "在历史实验库中模糊搜索。当用户用自然语言描述历史实验但未给编号时调用。"
            "返回候选列表。如用户随后确认了某个候选，再调 load_reference 加载。"
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

TOOLS_OPENAI_FORMAT = [
    TOOL_LOAD_REFERENCE,
    TOOL_SEARCH_EXPERIMENTS,
    TOOL_UPDATE_SCHEMA,
    TOOL_ASK_USER,
]

# ============================================================================
# Step 1.7: SYSTEM_PROMPT
# ============================================================================

SYSTEM_PROMPT = """\
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


# ============================================================================
# Step 1.2: ToolExecutor
# ============================================================================

class ToolExecutor:
    """注册、校验、执行 LLM 调用的工具"""

    def __init__(self, store):
        self.store = store
        self.registry = {
            "load_reference": self._load_reference,
            "search_experiments": self._search_experiments,
            "update_schema": self._update_schema,
            "ask_user": self._ask_user,
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

    # -- ask_user（占位，实际由前端处理）--

    def _ask_user(self, args: dict, loop: "AgentLoop") -> dict:
        return {"status": "asked"}

    # -- Step 1.5: load_reference --

    def _load_reference(self, args: dict, loop: "AgentLoop") -> dict:
        """加载引用实验。先试 EXP ID 直接加载，再试模糊搜索。"""
        results = {}
        for ref in args.get("refs", []):
            ref = str(ref).strip()
            if not ref:
                continue

            # 第一步：正则匹配 EXP ID
            m = re.match(r"^(?:@)?(EXP-\d{4}-\d{3})$", ref, re.IGNORECASE)
            if m:
                exp_id = m.group(1).upper()
                exp = self.store.load(exp_id)
                if exp:
                    loop.references.append(exp_id)
                    results[exp_id] = self._summarize_exp(exp)
                    continue

            # 第二步：模糊搜索
            candidates = self._fuzzy_search(ref, loop)
            if candidates:
                top = candidates[0]
                exp = self.store.load(top["id"])
                if exp:
                    loop.references.append(top["id"])
                    results[top["id"]] = self._summarize_exp(exp)
            else:
                results[ref] = {"error": "未找到匹配实验"}

        # 从首个加载的实验推断 experiment_type
        if results and loop.experiment_type == "other":
            for key, val in results.items():
                if "tags" in val and val["tags"]:
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
        return {
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

    # -- Step 1.6: search_experiments --

    def _search_experiments(self, args: dict, loop: "AgentLoop") -> dict:
        candidates = self._fuzzy_search(args.get("query", ""), loop)
        return {"candidates": candidates[:5]}

    def _fuzzy_search(self, query: str, loop: "AgentLoop") -> list[dict]:
        """本地关键词搜索"""
        if not query or len(query) < 2:
            return []
        all_exps = loop.store.list_all_full()
        results = []
        text_lower = query.lower()
        has_cjk = any('一' <= c <= '鿿' for c in query)

        for exp in all_exps:
            score = 0.0
            title = (exp.get("title") or "").lower()
            tags = " ".join(exp.get("tags") or []).lower()
            purpose = (exp.get("purpose") or "")[:200].lower()
            mat_names = " ".join(
                m.get("name", "") for m in (exp.get("materials") or [])
                if isinstance(m, dict)
            ).lower()
            searchable = f"{title} {tags} {purpose} {mat_names}"

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
        fields = args.get("fields", {})
        merge_context(loop.context, fields)

        # 推断 experiment_type（从 tags 中）
        if loop.experiment_type == "other":
            tags = loop.context.get("tags", [])
            for tag in tags:
                if tag in ("photocatalysis", "hydrothermal", "sol-gel",
                           "spin-coating", "ball-milling",
                           "electrochemistry", "xrd", "perovskite-solar"):
                    loop.experiment_type = tag
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

    def __init__(self, llm_client, experiment_store):
        self.llm = llm_client
        self.store = experiment_store
        self.context = deepcopy(DEFAULT_CONTEXT)
        self.history = []           # [{role, content, tool_calls?, tool_call_id?}]
        self.references = []        # 已加载的引用实验 ID
        self.experiment_type = "other"
        self.turn_count = 0
        self.tools = ToolExecutor(experiment_store)

        # 调试目录
        self.debug_dir = (
            Path(experiment_store.path) / "_debug" /
            datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        os.makedirs(self.debug_dir, exist_ok=True)
        self._llm_call_seq = 0      # LLM 调用全局序号（跨 turn 递增）

        # 保存 system prompt 到调试目录（只保存一次）
        try:
            (self.debug_dir / "000_system_prompt.txt").write_text(
                SYSTEM_PROMPT, encoding="utf-8")
        except Exception:
            print(f"[DEBUG] save system_prompt failed: {sys.exc_info()[1]}", file=sys.stderr)

    # -- 主循环 --

    def run(self, user_message: str = "") -> dict:
        """处理一条用户消息。返回 {type, message?, context}"""
        if user_message:
            self.history.append({"role": "user", "content": user_message})
            self.turn_count += 1
            self._save_turn_snapshot()
            self._save_final_messages()
            self._save_context()

        consecutive_errors = 0
        last_tool = None

        while True:
            messages = [{"role": "system", "content": SYSTEM_PROMPT}, *self.history]
            self._llm_call_seq += 1
            seq = self._llm_call_seq

            # ---- 日志: LLM 请求 ----
            self._log_llm_request(seq, messages)

            response = self.llm.client.chat.completions.create(
                model=self.llm.model,
                messages=messages,
                tools=TOOLS_OPENAI_FORMAT,
                temperature=0.3,
                reasoning_effort="max",
            )
            msg = response.choices[0].message
            _reasoning = getattr(msg, "reasoning_content", None) or ""

            # ---- 日志: LLM 响应 ----
            self._log_llm_response(seq, msg, _reasoning)

            # 纯文本 → 不再调工具
            if msg.content and not msg.tool_calls:
                entry = {"role": "assistant", "content": msg.content}
                if _reasoning:
                    entry["reasoning_content"] = _reasoning
                self.history.append(entry)
                self._save_turn_snapshot()
                self._save_final_messages()
                self._save_context()
                if self._core_fields_filled():
                    return {"type": "extract", "context": self.context}
                return {"type": "reply", "message": msg.content,
                        "context": self.context}

            # 调用了工具
            for tc in (msg.tool_calls or []):
                name = tc.function.name
                raw_args_str = tc.function.arguments

                # ---- 日志: tool 调用入参 ----
                self._log_tool_call(seq, name, raw_args_str)

                args = json.loads(raw_args_str)
                result = self.tools.execute(name, args, self)

                # ---- 日志: tool 执行结果 ----
                self._log_tool_result(seq, name, result)

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
                        self._save_turn_snapshot()
                        self._save_final_messages()
                        self._save_context()
                        return {"type": "reply",
                                "message": "抱歉，处理请求时遇到技术问题。请换个方式描述。",
                                "context": self.context}
                else:
                    consecutive_errors = 0
                    last_tool = None

                # ask_user 暂停循环
                if name == "ask_user":
                    self._save_turn_snapshot()
                    self._save_final_messages()
                    self._save_context()
                    questions = "\n".join(
                        f"{i+1}. {q}" for i, q in enumerate(args.get("questions", []))
                    )
                    if msg.content:
                        questions = msg.content + "\n\n" + questions
                    return {"type": "reply",
                            "message": questions,
                            "context": self.context}

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
            val = self.context.get(key)
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
        return all(_is_filled(self.context.get(f)) for f in core)

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

    def _save_turn_snapshot(self):
        """每轮结束后保存精简快照（保留历史格式兼容）"""
        try:
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
            filepath.write_text(
                json.dumps(compact, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception:
            print(f"[DEBUG] _save_turn_snapshot failed: {sys.exc_info()[1]}", file=sys.stderr)

    def _save_final_messages(self):
        """实时保存完整 messages（每轮结束后更新，不截断）"""
        try:
            filepath = self.debug_dir / "final_messages.json"
            filepath.write_text(
                json.dumps(self.history, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8")
        except Exception:
            print(f"[DEBUG] _save_final_messages failed: {sys.exc_info()[1]}", file=sys.stderr)

    def _save_context(self):
        """实时保存当前 Schema context 状态"""
        try:
            filepath = self.debug_dir / "context.json"
            filepath.write_text(
                json.dumps(self.context, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8")
        except Exception:
            print(f"[DEBUG] _save_context failed: {sys.exc_info()[1]}", file=sys.stderr)

    def save_final_messages(self):
        """对话最终结束时再保存一次（兼容外部调用，内部已实时更新）"""
        self._save_final_messages()

    # -- 状态序列化 --

    def state_to_dict(self) -> dict:
        return {
            "context": self.context,
            "references": self.references,
            "experiment_type": self.experiment_type,
            "turn_count": self.turn_count,
            "llm_call_seq": self._llm_call_seq,
            "history": [
                {k: v for k, v in m.items() if v is not None}
                for m in self.history
            ],
            "debug_dir": str(self.debug_dir),
        }

    @classmethod
    def from_dict(cls, llm_client, store, data: dict) -> "AgentLoop":
        loop = cls(llm_client, store)
        loop.context = data.get("context", deepcopy(DEFAULT_CONTEXT))
        loop.references = data.get("references", [])
        loop.experiment_type = data.get("experiment_type", "other")
        loop.turn_count = data.get("turn_count", 0)
        loop._llm_call_seq = data.get("llm_call_seq", 0)
        loop.history = data.get("history", [])
        debug_dir = data.get("debug_dir", "")
        if debug_dir:
            loop.debug_dir = Path(debug_dir)
        return loop
