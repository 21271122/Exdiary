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

TOOLS_OPENAI_FORMAT = [
    TOOL_LOAD_REFERENCE,
    TOOL_SEARCH_EXPERIMENTS,
    TOOL_UPDATE_SCHEMA,
    TOOL_ASK_USER,
    TOOL_GENERATE_RECORD,
]

# ============================================================================
# Step 1.7: SYSTEM_PROMPT
# ============================================================================

SYSTEM_PROMPT = """\
你是 Exdiary 实验记录助手。你与用户对话，逐步收集实验信息，
最终生成完整的结构化实验记录。

## 工作方式

你有 5 个工具。收到用户消息后:

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


def _fallback_preview(loop: "AgentLoop") -> dict:
    """确定性回退：从 context 直接构造预览数据，不调 LLM"""
    ctx = loop.context
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
            "generate_record": self._generate_record,
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

    # -- generate_record --

    def _generate_record(self, args: dict, loop: "AgentLoop") -> dict:
        notes = loop._build_notes_from_context()
        try:
            from lib.parser import parse_notes
            result = parse_notes(notes, loop.llm)
            result["original_notes"] = notes
            result["id"] = loop.store.next_id()
            result["references"] = list(loop.references)
            loop._generated_preview = result
            loop._generated_notes = notes
            return {"status": "generated", "id": result["id"],
                    "title": result.get("title", ""),
                    "fields_count": sum(1 for v in result.values() if v)}
        except Exception:
            preview = _fallback_preview(loop)
            loop._generated_preview = preview
            loop._generated_notes = notes
            return {"status": "generated",
                    "id": preview["id"],
                    "title": preview.get("title", ""),
                    "note": "使用了确定性回退，部分字段可能需手动补全"}

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

    def __init__(self, llm_client, experiment_store, debug_dir: str | Path | None = None):
        self.llm = llm_client
        self.store = experiment_store
        self.context = deepcopy(DEFAULT_CONTEXT)
        self.history = []           # [{role, content, tool_calls?, tool_call_id?}]
        self.references = []        # 已加载的引用实验 ID
        self.experiment_type = "other"
        self.turn_count = 0
        self.tools = ToolExecutor(experiment_store)
        self._generated_preview = None   # generate_record 工具产出
        self._generated_notes = None
        self._llm_call_seq = 0      # LLM 调用全局序号（跨 turn 递增）

        # 调试目录：新会话创建新目录，恢复会话复用已有路径
        if debug_dir:
            self.debug_dir = Path(debug_dir)
        else:
            self.debug_dir = (
                Path(experiment_store.path) / "_debug" /
                datetime.now().strftime("%Y%m%d_%H%M%S")
            )
            os.makedirs(self.debug_dir, exist_ok=True)
            # 新会话才写 system_prompt（恢复会话已存在）
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

            # 纯文本 → 不再调工具，直接返回
            if msg.content and not msg.tool_calls:
                entry = {"role": "assistant", "content": msg.content}
                if _reasoning:
                    entry["reasoning_content"] = _reasoning
                self.history.append(entry)
                self._save_turn_snapshot()
                self._save_final_messages()
                self._save_context()
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

                # generate_record → 生成实验记录，停止循环
                if name == "generate_record":
                    self._save_turn_snapshot()
                    self._save_final_messages()
                    self._save_context()
                    if self._generated_preview is None:
                        return {"type": "reply",
                                "message": "生成失败，请重试或补充更多信息。",
                                "context": self.context}
                    return {"type": "generate",
                            "message": "实验记录已生成，请在预览中确认。",
                            "state": self.state_to_dict(),
                            "preview": self._generated_preview,
                            "notes": self._generated_notes,
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

    def _build_notes_from_context(self) -> str:
        """从 context 生成自然语言实验描述（Python 模板，不调 LLM）"""
        ctx = self.context
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
        loop = cls(llm_client, store, debug_dir=data.get("debug_dir") or None)
        loop.context = data.get("context", deepcopy(DEFAULT_CONTEXT))
        loop.references = data.get("references", [])
        loop.experiment_type = data.get("experiment_type", "other")
        loop.turn_count = data.get("turn_count", 0)
        loop._llm_call_seq = data.get("llm_call_seq", 0)
        loop.history = data.get("history", [])
        return loop
