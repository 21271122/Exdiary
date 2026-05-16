"""
Exdiary 对话式实验记录 Agent — 核心模块

四阶段状态机:
  intent  →  detail  →  verify  →  extract
  意图识别   细节追问    完整性检查   生成提取
"""

from dataclasses import dataclass, field
from copy import deepcopy

# ============================================================================
# Step 1.1: AgentConfig — 可调参数
# ============================================================================

@dataclass
class AgentConfig:
    """Agent 行为配置，所有参数可通过构造函数覆盖"""
    max_turns: int = 6
    completeness_threshold: float = 0.80
    model: str = "deepseek-v4-flash"           # Agent 对话用 flash 模型
    max_missing_per_round: int = 3              # 每轮最多追问几项
    context_summary_max_chars: int = 500        # 上下文摘要最大字符数
    verify_reserved_turns: int = 2              # 为 VERIFY 阶段预留的轮次
    no_progress_threshold: int = 2              # 连续无进展的轮次上限
    debug: bool = False                         # 是否启用调试日志


# ============================================================================
# Step 1.2: PRIORITY_MAP — 9 种实验类型 × 三级优先级
# ============================================================================

PRIORITY_MAP: dict[str, dict[str, list[str]]] = {
    "photocatalysis": {
        "priority_1": [
            "催化剂名称和纯度",
            "目标污染物和浓度",
            "光源类型和功率",
        ],
        "priority_2": [
            "催化剂负载量",
            "降解时间",
            "表征手段",
        ],
        "priority_3": [
            "基板类型",
            "煅烧条件",
            "溶液pH",
        ],
    },
    "hydrothermal": {
        "priority_1": [
            "前驱体名称和用量",
            "反应温度",
            "反应时间",
        ],
        "priority_2": [
            "溶剂类型和用量",
            "目标产物",
            "填充度",
        ],
        "priority_3": [
            "升温速率",
            "pH值",
            "表面活性剂",
        ],
    },
    "sol-gel": {
        "priority_1": [
            "前驱体名称",
            "溶剂",
            "水解抑制剂",
        ],
        "priority_2": [
            "陈化温度和时间",
            "干燥条件",
            "煅烧温度",
        ],
        "priority_3": [
            "滴加速率",
            "催化剂用量",
            "研磨条件",
        ],
    },
    "spin-coating": {
        "priority_1": [
            "薄膜材料名称",
            "基底类型",
            "旋涂转速",
        ],
        "priority_2": [
            "前驱体浓度和溶剂",
            "退火温度和时间",
        ],
        "priority_3": [
            "旋涂层数",
            "预处理方式",
            "气氛",
        ],
    },
    "ball-milling": {
        "priority_1": [
            "原料名称和用量",
            "球料比",
            "球磨时间",
        ],
        "priority_2": [
            "转速",
            "球磨罐材质",
            "磨球尺寸",
        ],
        "priority_3": [
            "过程控制剂",
            "气氛保护",
            "停机间隔",
        ],
    },
    "electrochemistry": {
        "priority_1": [
            "活性材料名称",
            "电解液体系",
            "测试类型",
        ],
        "priority_2": [
            "电压窗口",
            "对电极/参比电极",
            "活性物负载量",
        ],
        "priority_3": [
            "导电剂和粘结剂配比",
            "测试温度",
            "扫速",
        ],
    },
    "xrd": {
        "priority_1": [
            "样品名称和形态",
            "扫描范围",
            "靶材类型",
        ],
        "priority_2": [
            "管电压/管电流",
            "扫描步长",
            "物相检索数据库",
        ],
        "priority_3": [
            "仪器型号",
            "制样方式",
            "晶粒尺寸计算",
        ],
    },
    "perovskite-solar": {
        "priority_1": [
            "钙钛矿组分和配比",
            "ETL/HTL材料",
            "退火温度和时间",
        ],
        "priority_2": [
            "旋涂参数",
            "反溶剂",
            "电极材料和厚度",
        ],
        "priority_3": [
            "有效面积",
            "测试光源条件",
            "器件结构",
        ],
    },
    "other": {
        "priority_1": [
            "实验目的是什么",
            "使用了哪些关键材料",
        ],
        "priority_2": [
            "核心操作步骤",
            "主要参数有哪些",
        ],
        "priority_3": [
            "表征手段",
            "预期结果",
        ],
    },
}

# 将优先级清单展平为 schema 字段映射（供阶段 3 VERIFY 使用）
# 优先级 1 → 核心字段, 优先级 2 → 重要字段, 优先级 3 → 补充字段
_PRIORITY_TO_SCHEMA_FIELD: dict[str, dict[str, list[str]]] = {
    "photocatalysis": {
        "core_fields": ["title", "purpose", "materials", "tags",
                        "process_parameters", "results"],
        "important_fields": ["sop", "status", "characterization",
                             "experimental_plan"],
        "optional_fields": ["date", "experimenter", "equipment",
                            "observations", "conclusion", "next_steps"],
    },
    "hydrothermal": {
        "core_fields": ["title", "purpose", "materials", "sop",
                        "process_parameters", "results"],
        "important_fields": ["tags", "status", "characterization",
                             "experimental_plan"],
        "optional_fields": ["date", "experimenter", "equipment",
                            "observations", "conclusion", "next_steps"],
    },
    "sol-gel": {
        "core_fields": ["title", "purpose", "materials", "sop",
                        "process_parameters", "results"],
        "important_fields": ["tags", "status", "characterization",
                             "experimental_plan"],
        "optional_fields": ["date", "experimenter", "equipment",
                            "observations", "conclusion", "next_steps"],
    },
    "spin-coating": {
        "core_fields": ["title", "purpose", "materials", "sop",
                        "process_parameters", "results"],
        "important_fields": ["tags", "status", "characterization",
                             "experimental_plan"],
        "optional_fields": ["date", "experimenter", "equipment",
                            "observations", "conclusion", "next_steps"],
    },
    "ball-milling": {
        "core_fields": ["title", "purpose", "materials", "sop",
                        "process_parameters", "results"],
        "important_fields": ["tags", "status", "characterization",
                             "experimental_plan"],
        "optional_fields": ["date", "experimenter", "equipment",
                            "observations", "conclusion", "next_steps"],
    },
    "electrochemistry": {
        "core_fields": ["title", "purpose", "materials", "process_parameters",
                        "results"],
        "important_fields": ["tags", "status", "sop", "characterization",
                             "experimental_plan"],
        "optional_fields": ["date", "experimenter", "equipment",
                            "observations", "conclusion", "next_steps"],
    },
    "xrd": {
        "core_fields": ["title", "purpose", "materials", "process_parameters",
                        "results"],
        "important_fields": ["tags", "status", "sop", "characterization"],
        "optional_fields": ["date", "experimenter", "equipment",
                            "experimental_plan",
                            "observations", "conclusion", "next_steps"],
    },
    "perovskite-solar": {
        "core_fields": ["title", "purpose", "materials", "sop",
                        "process_parameters", "results"],
        "important_fields": ["tags", "status", "characterization",
                             "experimental_plan"],
        "optional_fields": ["date", "experimenter", "equipment",
                            "observations", "conclusion", "next_steps"],
    },
    "other": {
        "core_fields": ["title", "purpose", "materials", "sop", "results"],
        "important_fields": ["tags", "status", "process_parameters",
                             "characterization"],
        "optional_fields": ["date", "experimenter", "equipment",
                            "experimental_plan",
                            "observations", "conclusion", "next_steps"],
    },
}


def get_schema_priority(experiment_type: str) -> dict[str, list[str]]:
    """根据实验类型返回 {core_fields, important_fields, optional_fields}"""
    return _PRIORITY_TO_SCHEMA_FIELD.get(
        experiment_type,
        _PRIORITY_TO_SCHEMA_FIELD["other"]
    )


# ============================================================================
# Step 1.3: PARAM_ALIASES — 参数名称归一化映射
# ============================================================================

PARAM_ALIASES: dict[str, list[str]] = {
    # 温度类
    "退火温度": ["热退火温度", "热退火参数", "退火温度参数",
                 "annealing temperature", "annealing temp"],
    "煅烧温度": ["焙烧温度", "烧结温度", "calcination temperature",
                 "煅烧温度参数", "焙烧温度参数"],
    "干燥温度": ["烘干温度", "drying temperature"],
    "反应温度": ["水热温度", "溶剂热温度", "合成温度", "reaction temperature"],
    "陈化温度": ["老化温度", "aging temperature"],
    "测试温度": ["测量温度", "测试环境温度"],

    # 时间类
    "退火时间": ["热退火时间", "annealing time", "退火时长", "保温时长"],
    "煅烧时间": ["焙烧时间", "烧结时间", "calcination time", "煅烧时长"],
    "反应时间": ["水热时间", "合成时间", "reaction time", "反应时长"],
    "干燥时间": ["烘干时间", "drying time"],
    "陈化时间": ["老化时间", "aging time"],
    "球磨时间": ["研磨时间", "milling time", "球磨时长"],

    # 转速/速度类
    "旋涂转速": ["旋转涂布转速", "spin coating speed", "旋涂速度",
                 "spin speed", "涂布转速"],
    "球磨转速": ["研磨转速", "milling speed", "球磨机转速"],

    # 浓度/用量类
    "前驱体浓度": ["前驱体用量", "前驱体配比", "precursor concentration"],
    "掺杂量": ["掺杂浓度", "掺杂比例", "doping amount", "掺杂剂用量"],
    "负载量": ["催化剂负载量", "loading amount", "负载比例"],
    "球料比": ["ball-to-powder ratio", "料球比"],

    # 电压/电流类
    "管电压": ["X射线管电压", "tube voltage", "加速电压"],
    "管电流": ["X射线管电流", "tube current", "发射电流"],
    "电压窗口": ["电位窗口", "potential window", "扫描范围"],

    # 气氛类
    "退火气氛": ["退火氛围", "annealing atmosphere", "热处理气氛"],
    "保护气氛": ["保护气体", "inert atmosphere", "气氛条件"],

    # 其他
    "扫描步长": ["步进", "step size", "扫描步进"],
    "扫描范围": ["2θ范围", "scan range", "扫描角度范围"],
    "升温速率": ["加热速率", "heating rate", "升温速度"],
    "填充度": ["填充率", "filling ratio", "填充比例"],
    "旋涂层数": ["涂层次数", "number of layers", "涂覆层数"],
}


def normalize_param_name(name: str) -> str:
    """将参数名称归一化到标准名称。未找到映射时返回原名称。"""
    name_lower = name.strip().lower()
    # 先精确匹配
    for canonical, aliases in PARAM_ALIASES.items():
        if name_lower == canonical.lower():
            return canonical
        for alias in aliases:
            if name_lower == alias.lower():
                return canonical
    # 模糊匹配: 别名包含输入 或 输入包含别名
    for canonical, aliases in PARAM_ALIASES.items():
        for alias in aliases:
            if alias.lower() in name_lower or name_lower in alias.lower():
                return canonical
    return name.strip()


# ============================================================================
# Step 1.4: AgentState — 对话状态数据模型
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
    "raw_notes": "",
}


@dataclass
class AgentState:
    """对话 Agent 的完整状态，可在前后端之间序列化传递"""
    stage: str = "intent"                    # intent | detail | verify | extract | done
    context: dict = field(default_factory=lambda: deepcopy(DEFAULT_CONTEXT))
    missing: list[str] = field(default_factory=list)
    contradictions: list[dict] = field(default_factory=list)
    references: list[str] = field(default_factory=list)         # 已解析的精确引用
    fuzzy_references: list[dict] = field(default_factory=list)  # 待解析的模糊引用
    history: list[dict] = field(default_factory=list)           # [{role, content}]
    turn_count: int = 0
    completeness: float = 0.0
    final_notes: str = ""
    experiment_type: str = "other"
    user_wants_done: bool = False          # 用户主动要求结束对话
    prev_context_hash: str = ""            # 上一轮 context 的 md5，跨请求检测无进展

    def state_to_dict(self) -> dict:
        """序列化为 JSON 兼容的 dict（供前后端传递）"""
        return {
            "stage": self.stage,
            "context": self.context,
            "missing": self.missing,
            "contradictions": self.contradictions,
            "references": self.references,
            "fuzzy_references": self.fuzzy_references,
            "history": self.history,
            "turn_count": self.turn_count,
            "completeness": self.completeness,
            "final_notes": self.final_notes,
            "experiment_type": self.experiment_type,
            "user_wants_done": self.user_wants_done,
            "prev_context_hash": self.prev_context_hash,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentState":
        """从 JSON dict 反序列化"""
        return cls(
            stage=data.get("stage", "intent"),
            context=data.get("context", deepcopy(DEFAULT_CONTEXT)),
            missing=data.get("missing", []),
            contradictions=data.get("contradictions", []),
            references=data.get("references", []),
            fuzzy_references=data.get("fuzzy_references", []),
            history=data.get("history", []),
            turn_count=data.get("turn_count", 0),
            completeness=data.get("completeness", 0.0),
            final_notes=data.get("final_notes", ""),
            experiment_type=data.get("experiment_type", "other"),
            user_wants_done=data.get("user_wants_done", False),
            prev_context_hash=data.get("prev_context_hash", ""),
        )

    def merge_context(self, update: dict) -> None:
        """将增量更新合并到 context 中。
        简单字段覆盖；数组字段追加（去重）；嵌套对象递归合并。"""
        if not update:
            return
        for key, value in update.items():
            if key not in self.context:
                continue
            existing = self.context[key]
            if isinstance(existing, list) and isinstance(value, list):
                # 数组字段: 追加新元素（对简单字符串去重）
                for item in value:
                    if isinstance(item, str) and item in existing:
                        continue
                    existing.append(item)
            elif isinstance(existing, dict) and isinstance(value, dict):
                # 嵌套对象（如 results, observations）: 递归合并
                for sub_key, sub_value in value.items():
                    if isinstance(existing.get(sub_key), list) and isinstance(sub_value, list):
                        for sv in sub_value:
                            if sv not in existing[sub_key]:
                                existing[sub_key].append(sv)
                    elif sub_value not in (None, ""):
                        existing[sub_key] = sub_value
            elif value not in (None, ""):
                # 简单字段: 覆盖
                self.context[key] = value


# ============================================================================
# Step 1.5: TurnController — 轮次控制
# ============================================================================

class TurnController:
    """决定对话是否应该结束、以何种方式结束"""

    def __init__(self, config: AgentConfig | None = None):
        self.config = config or AgentConfig()

    def should_end(self, state: AgentState) -> tuple[bool, str]:
        """
        返回 (是否应该结束对话, 结束原因)
        原因: user_requested | complete | max_turns | no_progress
        """
        # 条件1: 用户主动要求结束
        if state.user_wants_done:
            return True, "user_requested"

        # 条件2: 完整性达标
        if state.completeness >= self.config.completeness_threshold:
            return True, "complete"

        # 条件3: 超过最大轮次
        if state.turn_count >= self.config.max_turns:
            return True, "max_turns"

        # 条件4: 最近两轮无实质性进展
        if self._no_progress(state):
            return True, "no_progress"

        return False, ""

    def should_enter_verify(self, state: AgentState) -> bool:
        """判断是否应该从 DETAIL 阶段进入 VERIFY 阶段"""
        # 条件1: 所有优先级项都已填充（missing 为空或只剩 optional 项）
        if not state.missing:
            return True

        # 条件2: 接近最大轮次上限，预留轮次给 VERIFY
        if state.turn_count >= self.config.max_turns - self.config.verify_reserved_turns:
            return True

        # 条件3: 用户主动要求
        if state.user_wants_done:
            return True

        return False

    def generate_wrap_up(self, state: AgentState) -> str:
        """对话结束时生成收尾消息，列出仍未填的字段"""
        if not state.missing:
            return "信息已完整，正在生成实验记录..."

        lines = ["好的，以下信息尚未完整填写，将在生成的草稿中标注为待补充："]
        for m in state.missing[:8]:  # 最多列 8 项，避免过长
            lines.append(f"  • {m}")
        if len(state.missing) > 8:
            lines.append(f"  …还有 {len(state.missing) - 8} 项")
        lines.append("\n你可以稍后在预览中手动补充这些字段。")
        lines.append("正在生成实验记录…")
        return "\n".join(lines)

    def _no_progress(self, state: AgentState) -> bool:
        """检测最近 {threshold} 轮是否有实质性进展。
        通过比对 context 的哈希值判断。
        """
        import hashlib
        import json

        current_hash = hashlib.md5(
            json.dumps(state.context, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

        prev = state.prev_context_hash
        state.prev_context_hash = current_hash

        if prev and prev == current_hash:
            return True
        return False


# ============================================================================
# ExperimentAgent
# ============================================================================

class ExperimentAgent:
    """对话式实验记录 Agent"""

    def __init__(self, llm_client, experiment_store, config: AgentConfig | None = None,
                 tracer=None):
        """
        Args:
            llm_client: LLMClient 实例
            experiment_store: ExperimentStore 实例
            config: AgentConfig，为 None 时使用默认值
            tracer: DebugTracer 实例，为 None 时根据 config.debug 自动创建
        """
        self.llm = llm_client
        self.store = experiment_store
        self.config = config or AgentConfig()
        self.state = AgentState()
        self.turn_controller = TurnController(self.config)
        self.tracer = tracer
        if self.tracer is None and self.config.debug:
            from lib.debug import create_debug_tracer
            self.tracer = create_debug_tracer(str(experiment_store.path))

    # ========================================================================
    # Step 2.1: 四个阶段的 System Prompt 模板
    # 变量占位符用 Python str.format() 填充
    # ========================================================================

    PROMPT_INTENT = """\
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
填入 context_update。

只提取用户明确说过的内容。不要推测、不要补全。
不确定的值宁可留空，也不编造。

提取时遵循以下字段结构：
{{
  "title": "",              // 实验标题
  "date": "",               // 日期 (YYYY-MM-DD)
  "experimenter": "",       // 实验者
  "status": "planned",      // planned|running|done|failed|repeated
  "tags": [],               // 2-4 个受控词汇标签
  "purpose": "",            // 实验目的/科学问题
  "materials": [            // 材料与试剂
    {{"name": "", "purity": "", "vendor": "", "amount": "", "notes": ""}}
  ],
  "equipment": [            // 仪器设备
    {{"device": "", "model": "", "location": ""}}
  ],
  "experimental_plan": [    // 实验方案/分组
    {{"group": "", "condition": "", "expected": ""}}
  ],
  "sop": [],                // 操作步骤 (字符串数组)
  "process_parameters": [   // 过程参数
    {{"step": "", "parameter": "", "setpoint": "", "actual": "", "deviation": ""}}
  ],
  "observations": {{         // 异常与观察
    "no_anomalies": true,
    "items": []
  }},
  "characterization": [     // 表征计划
    {{"method": "", "sample_id": "", "preparation": "", "submission_date": "", "data_path": ""}}
  ],
  "results": {{              // 结果
    "qualitative": "",
    "key_data": [{{"metric": "", "value": "", "comparison": "", "change": ""}}],
    "figures": [{{"figure": "", "path": "", "conclusion": ""}}]
  }},
  "conclusion": "",         // 结论
  "next_steps": []          // 下一步行动
}}

## 3. 确定关键缺失并追问

根据实验类型，从下方优先级清单中找出用户尚未提及的、
最重要的 2-3 项，在 reply 中主动追问。

=== 各实验类型的关键参数优先级清单 ===

{priority_map_text}

## 4. 解析引用

精确引用: 如果用户提到了 @EXP-xxx 格式的引用，
直接放入 references 列表。

模糊引用: 如果用户引用了之前的实验但未给编号
（如"上次那个ZnO水热实验""跟老张做钙钛矿那次一样"），
放入 fuzzy_references 列表，每项包含:
- raw_text: 用户原文中的模糊描述
- detected_in_turn: 在第几轮检测到的

## 5. 输出格式

严格返回以下 JSON（不要包含在 markdown 代码块中）:
{{
  "experiment_type": "perovskite-solar",
  "context_update": {{
    "purpose": "制备钙钛矿太阳能电池，优化HTL掺杂...",
    "tags": ["perovskite", "solar-cell"]
  }},
  "missing": ["钙钛矿前驱体具体配比", "HTL 掺杂剂名称和用量"],
  "references": ["EXP-003"],
  "fuzzy_references": [{{"raw_text": "上次那个ZnO实验", "detected_in_turn": 0}}],
  "reply": "好的，看起来你在做钙钛矿太阳能电池..."
}}

## 约束

- reply 必须用中文，友好、具体、像同事在交流
- 一次追问不超过 3 项，优先追问缺失的优先级 1 项
- 不要凭空编造任何用户未提及的信息
- 不要在首轮就追问优先级 3 的补充信息（如设备型号、实验者姓名）
- 如果用户已经提供了某个信息，绝对不要再问
- 如果用户输入非常简短，先判断类型再追问
"""

    PROMPT_DETAIL = """\
你是 Exdiary 实验记录助手。对话正在进行中。
用户的实验信息正在逐步完善，你需要继续追问细节，
同时检测潜在的矛盾。

## 当前状态

实验类型: {experiment_type}
对话轮次: {turn_count} / {max_turns}

已收集的信息摘要:
{context_summary}

已解析的引用实验详情:
{references_detail}

## 你的任务

### 任务 1: 按优先级继续追问（1-3 项）

当前仍未补充的缺失项（按优先级排序）:
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
比较用户最新消息与 context_summary 中已记录的值。
如果发现矛盾，同时列出两个矛盾的来源，
让用户确认哪个是正确的。

**(B) 引用矛盾 — 用户说法与引用实验记录不一致**
比较用户最新消息与 references_detail 中引用实验的实际参数。

注意: 参数名称可能以不同形式出现。以下名称可能指代同一概念，
请将其视为同一参数进行比对:
  退火温度 = 热退火温度 = 热退火参数 = 退火温度参数
  煅烧温度 = 焙烧温度 = 烧结温度
  旋涂转速 = 旋转涂布转速 = 涂布转速
  （根据具体实验上下文中出现的参数灵活判断）

如果发现实质相同的参数但数值不同，报告矛盾。
如果不确定两个参数名是否指同一概念，不要强行关联，
在 reply 中向用户确认。

**(C) 逻辑矛盾 — 状态与内容不匹配**
例如:
- status 标记为 "done" 但没有任何 results 数据
- status 标记为 "planned" 但描述了具体的实验结果数值

矛盾处理原则:
- 关键: 只指出矛盾，绝不自行为用户修正
- 报告矛盾时同时列出矛盾的双方
- 用友善但明确的语气，不评价用户的表述能力
- 如果用户确认某个值是正确的那就接受

### 任务 3: 更新上下文

根据用户的最新消息，增量更新 context_update。
只更新用户在本轮中新提供的、之前未记录的信息。
不要重复填入已有的内容。
不要编造用户未提及的值。

### 任务 4: 解析引用

精确引用: 如果用户新提到了 @EXP-xxx，记录到 references_update。
模糊引用: 如果用户模糊引用了历史实验（无编号），
记录到 fuzzy_references_update，每项包含 raw_text。

## 任务 5: 判断用户是否想结束对话

根据用户最新消息判断其意图：

- 如果用户明确表示想结束对话（"够了""可以了""生成吧""直接生成"
  "就这样""不用了""跳过""就这些"），设置 user_wants_done = true
- 如果用户是在回答参数值（"退火温度 500 度够了"）或继续提供信息，
  设置 user_wants_done = false
- 如果用户连续两轮只回复非常简短的内容（"嗯""好""知道了"），
  也可能意味着不想继续补充，此时可设 user_wants_done = true

关键区别: "温度 500 度够了" → false（在说参数值够了）
         "够了，就这些信息" → true（在说要结束对话）

## 输出格式

严格返回以下 JSON（不要包含在 markdown 代码块中）:
{{
  "context_update": {{}},
  "references_update": [],
  "fuzzy_references_update": [],
  "user_wants_done": false,
  "missing_after_update": [],
  "contradictions": [
    {{
      "type": "self|ref_mismatch|logic",
      "field": "退火温度",
      "claim_from": "用户第 1 轮说 120°C",
      "claim_against": "本轮说 100°C",
      "severity": "high|medium|low",
      "message": "我注意到你之前说退火温度是 120°C，但这轮说是 100°C。哪个是正确的？"
    }}
  ],
  "reply": "好的，明白了。不过我注意到一个小问题..."
}}

## 约束

- reply 必须用中文
- 追问和矛盾检测可以合并到同一条 reply 中
- 有矛盾时先指出矛盾，再追问缺失项
- 如果本轮没有检测到矛盾，contradictions 字段返回空数组 []
- 如果本轮没有新的上下文更新，context_update 返回空对象 {{}}
- 如果用户说"跟上次一样""参考之前""跟 EXP-xxx 一样""完全一致"，
  不要追问那些参数。你必须将引用实验详情中匹配的字段
  通过 context_update 填入，而不是只在 reply 中说"好的，将与 XXX 一致"。
  例如: 引用实验有 7 步 SOP 和 6 项参数，用户说"完全一样"，
  则 context_update 必须包含这些 sop 和 process_parameters。
  如果用户说"跟 EXP-xxx 一样但换了掺杂剂"，
  则继承除掺杂剂相关字段外的所有字段。
"""

    PROMPT_VERIFY = """\
你是 Exdiary 实验记录助手。对话信息收集阶段即将结束。
请对照完整的实验记录结构，对当前已收集的信息进行
逐字段完整性评估。

## 当前状态

实验类型: {experiment_type}
对话轮次: {turn_count} / {max_turns}

已收集信息的完整上下文:
{context_full}

## 评估任务

请对以下 16 个字段逐一评估，每个字段标记为以下四种状态之一:
- "filled":     已有足够信息，可以直接填入结构化记录
- "partial":    有部分信息，但不够完整（如材料有名称但缺纯度/厂家）
- "missing":    完全没有信息
- "na":         对此类实验不适用

### 完整字段清单

1.  title               — 实验标题
2.  date                — 实验日期
3.  experimenter        — 实验者
4.  status              — 实验状态 (planned|running|done|failed|repeated)
5.  tags                — 标签 (2-4 个受控词汇)
6.  purpose             — 实验目的 / 科学问题
7.  materials           — 材料与试剂 (name, purity, vendor, amount, notes)
8.  equipment           — 仪器设备 (device, model, location)
9.  experimental_plan   — 实验方案 / 分组 (group, condition, expected)
10. sop                 — 操作步骤 (至少 2-3 个关键步骤)
11. process_parameters  — 过程参数 (step, parameter, setpoint, actual, deviation)
12. observations        — 异常与观察 (no_anomalies, items[])
13. characterization    — 表征计划 (method, sample_id, preparation, ...)
14. results             — 结果 (qualitative, key_data[], figures[])
15. conclusion          — 结论 (至少 1-2 句总结)
16. next_steps          — 下一步行动 (至少 1 项)

## 字段重要性分级

核心字段 (缺失会严重影响记录质量):
{core_fields}

重要字段 (缺失会影响后续分析):
{important_fields}

补充字段 (可以留空，不影响生成):
{optional_fields}

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

## 输出格式

严格返回以下 JSON:
{{
  "field_status": {{
    "title": {{"status": "filled", "note": ""}},
    "date": {{"status": "missing", "note": "未提及，可默认为今天"}},
    "experimenter": {{"status": "missing", "note": "不影响生成"}},
    "status": {{"status": "partial", "note": ""}},
    "tags": {{"status": "filled", "note": ""}},
    "purpose": {{"status": "filled", "note": ""}},
    "materials": {{"status": "partial", "note": "缺纯度和厂家"}},
    "equipment": {{"status": "missing", "note": "完全未提及"}},
    "experimental_plan": {{"status": "na", "note": ""}},
    "sop": {{"status": "filled", "note": ""}},
    "process_parameters": {{"status": "partial", "note": ""}},
    "observations": {{"status": "filled", "note": ""}},
    "characterization": {{"status": "partial", "note": ""}},
    "results": {{"status": "partial", "note": ""}},
    "conclusion": {{"status": "missing", "note": ""}},
    "next_steps": {{"status": "missing", "note": ""}}
  }},
  "completeness": 0.72,
  "core_remaining": [],
  "important_remaining": ["材料纯度信息", "关键性能数据"],
  "optional_remaining": ["实验者姓名", "仪器设备型号"],
  "ready_to_generate": true,
  "summary": "信息收集较为完整。核心字段均已填充。",
  "reply": "好的，信息收集得差不多了..."
}}

## 约束

- reply 必须用中文，简洁清晰
- 16 个字段必须逐个评估，不能跳过
- 字段状态的判断要严格：用户模糊提到但没说清楚 → partial
- ready_to_generate 的判断优先采纳 4 条规则中满足的任一条
- core_remaining / important_remaining / optional_remaining
  要列出具体的缺失内容（如"材料纯度"），不是字段名（如"materials.purity"）
"""

    PROMPT_EXTRACT = """\
你是材料科学领域的学术写作助手。
基于一段对话中收集的实验信息，生成一篇完整、连贯的
自然语言实验描述。这段描述将作为实验的"原始笔记"存档。

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
- 避免口语化表达

### 结构
按以下顺序组织段落：

第一段 — 实验概述:
  一句话概括实验目的和总体方案。

第二段 — 材料与试剂:
  列出所有使用的材料，包含名称、纯度、厂家、用量。
  纯度或厂家未知时写"（纯度未记录）"而非跳过。

第三段 — 实验步骤:
  按时间顺序描述操作步骤。关键参数在步骤中自然嵌入。
  步骤之间用"随后""接着""最后"连接。

第四段 — 表征与测试:
  列出所有表征手段和关键测试条件。

第五段 — 结果与结论:
  如果对话中提到了结果数据，在此列出。
  简要总结实验结论。

### 引用标注
所有引用的实验用 @EXP-xxx 格式在正文中标注。

### 信息覆盖
- 所有有值的信息必须覆盖，不遗漏
- 为空的信息不要编造
- 信息不完整的如实写"（未记录）"或"（待补充）"

### 长度
200-600 字，根据信息量调整。

## 输出格式

严格返回以下 JSON:
{{
  "title": "基于HTL掺杂剂优化的钙钛矿太阳能电池制备",
  "notes": "完整的自然语言实验描述..."
}}

## 约束

- notes 必须是完整的中文段落，不是列表
- title 从 context 提取，如果用户没给明确的标题，
  用"实验目的 + 关键变量"的格式自动生成
"""

    # ========================================================================
    # Step 2.2: JSON 容错解析
    # ========================================================================

    def _safe_parse_json(self, raw: str, stage: str) -> dict:
        """容错解析 LLM 返回的 JSON。

        LLM 有时会在 JSON 外包裹 ```json``` 代码块，
        或返回带尾部逗号的畸形 JSON。
        """
        import re
        import json as _json

        raw = raw.strip()

        # 尝试直接解析
        try:
            return _json.loads(raw)
        except _json.JSONDecodeError:
            pass

        # 尝试从 markdown 代码块中提取
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw)
        if m:
            try:
                return _json.loads(m.group(1).strip())
            except _json.JSONDecodeError:
                pass

        # 尝试用正则找到第一个 { 和最后一个 } 之间的内容
        m = re.search(r'\{[\s\S]*\}', raw)
        if m:
            try:
                return _json.loads(m.group(0))
            except _json.JSONDecodeError:
                pass

        self._trace_parse_error(stage, raw,
            "无法解析为 JSON: 直接解析、代码块提取、正则匹配均失败")
        raise ValueError(
            f"[{stage}] LLM 返回的内容无法解析为 JSON:\n{raw[:500]}"
        )

    # ========================================================================
    # Step 2.3: 上下文摘要构建（Python 字符串格式化，不调 LLM）
    # ========================================================================

    def _build_context_summary(self) -> str:
        """将 AgentState.context（完整 dict）压缩为自然语言摘要。

        压缩策略：
        - 有值的字段才输出，空字段跳过
        - 数组字段取前 N 项，超长截断
        - 目标: 150-200 tokens
        """
        ctx = self.state.context
        lines = []

        # 基本信息
        if ctx.get("title"):
            lines.append(f"标题: {ctx['title']}")
        if ctx.get("status") and ctx["status"] != "planned":
            lines.append(f"状态: {ctx['status']}")
        if ctx.get("date"):
            lines.append(f"日期: {ctx['date']}")
        if ctx.get("experimenter"):
            lines.append(f"实验者: {ctx['experimenter']}")
        if ctx.get("tags"):
            lines.append(f"标签: {', '.join(ctx['tags'])}")

        # 目的
        if ctx.get("purpose"):
            purpose = ctx["purpose"]
            if len(purpose) > 150:
                purpose = purpose[:147] + "..."
            lines.append(f"目的: {purpose}")

        # 材料 (最多列 8 种)
        materials = ctx.get("materials", [])
        if materials:
            mat_parts = []
            for m in materials[:8]:
                if isinstance(m, dict):
                    parts = [m.get("name", "?")]
                    if m.get("purity"):
                        parts.append(m["purity"])
                    if m.get("amount"):
                        parts.append(m["amount"])
                    if len(parts) > 1:
                        mat_parts.append(f"{parts[0]} ({', '.join(parts[1:])})")
                    else:
                        mat_parts.append(parts[0])
                else:
                    mat_parts.append(str(m))
            lines.append(f"材料: {'; '.join(mat_parts)}")
            if len(materials) > 8:
                lines[-1] += f" (共{len(materials)}种)"

        # 仪器设备
        equipment = ctx.get("equipment", [])
        if equipment:
            eq_names = [e.get("device", str(e)) if isinstance(e, dict) else str(e)
                       for e in equipment[:5]]
            lines.append(f"设备: {', '.join(eq_names)}")

        # 实验方案
        plan = ctx.get("experimental_plan", [])
        if plan:
            plan_parts = []
            for p in plan[:4]:
                if isinstance(p, dict):
                    plan_parts.append(p.get("group", "") + ": " + p.get("condition", ""))
            if plan_parts:
                lines.append(f"方案: {'; '.join(plan_parts)}")

        # SOP (取前 6 步)
        sop = ctx.get("sop", [])
        if sop:
            sop_display = sop[:6]
            for i, step in enumerate(sop_display, 1):
                s = str(step)
                if len(s) > 80:
                    s = s[:77] + "..."
                lines.append(f"  步骤{i}: {s}")
            if len(sop) > 6:
                lines.append(f"  (共{len(sop)}步)")

        # 过程参数 (取前 8 项)
        params = ctx.get("process_parameters", [])
        if params:
            param_parts = []
            for pp in params[:8]:
                if isinstance(pp, dict):
                    p_name = pp.get("parameter", "")
                    p_val = pp.get("setpoint", "")
                    if p_name and p_val:
                        param_parts.append(f"{p_name}: {p_val}")
            if param_parts:
                lines.append(f"参数: {'; '.join(param_parts)}")

        # 观察
        obs = ctx.get("observations", {})
        if isinstance(obs, dict):
            if obs.get("no_anomalies") is False:
                items = obs.get("items", [])
                if items:
                    lines.append(f"异常: {'; '.join(str(i) for i in items[:4])}")

        # 表征
        chara = ctx.get("characterization", [])
        if chara:
            methods = [c.get("method", str(c)) if isinstance(c, dict) else str(c)
                      for c in chara[:5]]
            lines.append(f"表征: {', '.join(methods)}")

        # 结果
        results = ctx.get("results", {})
        if isinstance(results, dict):
            if results.get("qualitative"):
                qual = results["qualitative"]
                if len(qual) > 120:
                    qual = qual[:117] + "..."
                lines.append(f"定性结果: {qual}")
            key_data = results.get("key_data", [])
            if key_data:
                kd_parts = []
                for kd in key_data[:6]:
                    if isinstance(kd, dict):
                        kd_parts.append(f"{kd.get('metric', '')}: {kd.get('value', '')}")
                if kd_parts:
                    lines.append(f"关键数据: {'; '.join(kd_parts)}")

        # 结论
        if ctx.get("conclusion"):
            conc = ctx["conclusion"]
            if len(conc) > 150:
                conc = conc[:147] + "..."
            lines.append(f"结论: {conc}")

        # 下一步
        next_steps = ctx.get("next_steps", [])
        if next_steps:
            lines.append(f"下一步: {'; '.join(str(s) for s in next_steps[:4])}")

        # 引用实验的数据（供 LLM 自行判断是否继承）
        if self.state.references:
            lines.append("")
            lines.append("--- 引用实验数据（用户说与此实验一致，你可自行判断填入 context_update）---")
            ref_detail = self._build_references_detail()
            lines.append(ref_detail)

        return "\n".join(lines)

    # ========================================================================
    # Step 2.4: 引用实验详情构建
    # ========================================================================

    def _build_references_detail(self) -> str:
        """读取引用实验的完整信息，构造供阶段 2 prompt 注入的文本块。

        包含每个引用实验的: 标题、状态、材料、关键参数（原始字段名）、
        结果和结论摘要。
        """
        if not self.state.references:
            return "（无引用实验）"

        parts = []
        for ref_id in self.state.references:
            exp = self.store.load(ref_id)
            if not exp:
                parts.append(f"@{ref_id}: ⚠️ 实验不存在，请检查编号")
                continue

            lines = [f"@{ref_id}: {exp.get('title', '(无标题)')}"]
            if exp.get("date"):
                lines.append(f"  日期: {exp['date']}")
            if exp.get("status"):
                lines.append(f"  状态: {exp['status']}")
            if exp.get("tags"):
                lines.append(f"  标签: {', '.join(exp['tags'])}")
            if exp.get("purpose"):
                purpose = exp["purpose"]
                if len(purpose) > 120:
                    purpose = purpose[:117] + "..."
                lines.append(f"  目的: {purpose}")

            # 材料
            materials = exp.get("materials", [])
            if materials:
                mat_names = []
                for m in materials[:6]:
                    if isinstance(m, dict):
                        mat_names.append(m.get("name", "?"))
                lines.append(f"  材料: {', '.join(mat_names)}")

            # 过程参数（保留原始字段名）
            params = exp.get("process_parameters", [])
            if params:
                for pp in params[:10]:
                    if isinstance(pp, dict):
                        p_name = pp.get("parameter", "")
                        p_val = pp.get("setpoint", pp.get("actual", ""))
                        if p_name and p_val:
                            lines.append(f"    - {p_name}: {p_val}")

            # SOP (取前 5 步)
            sop = exp.get("sop", [])
            if sop:
                for i, step in enumerate(sop[:5], 1):
                    s = str(step)
                    if len(s) > 80:
                        s = s[:77] + "..."
                    lines.append(f"  步骤{i}: {s}")
                if len(sop) > 5:
                    lines.append(f"  (共{len(sop)}步)")

            # 结果
            results = exp.get("results", {})
            if isinstance(results, dict):
                if results.get("qualitative"):
                    qual = results["qualitative"]
                    if len(qual) > 120:
                        qual = qual[:117] + "..."
                    lines.append(f"  定性结果: {qual}")
                key_data = results.get("key_data", [])
                if key_data:
                    kd = []
                    for k in key_data[:5]:
                        if isinstance(k, dict):
                            kd.append(f"{k.get('metric','')}: {k.get('value','')}")
                    if kd:
                        lines.append(f"  关键数据: {'; '.join(kd)}")

            # 结论
            if exp.get("conclusion"):
                conc = exp["conclusion"]
                if len(conc) > 120:
                    conc = conc[:117] + "..."
                lines.append(f"  结论: {conc}")

            parts.append("\n".join(lines))

        return "\n\n".join(parts)

    # ========================================================================
    # Step 2.5: LLM 调用封装
    # ========================================================================

    def _call_llm(self, system_prompt: str, user_prompt: str,
                  temperature: float = 0.3) -> str:
        """统一封装 LLM 调用，处理异常"""
        raw = self.llm.analyze(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
        )
        # 调用方负责传 stage 名——这里拿不到。在调用后由上层决定是否记录。
        return raw

    def _trace_llm(self, stage: str, system_prompt: str, user_prompt: str,
                   temperature: float, raw_response: str) -> None:
        """记录 LLM 调用到调试日志"""
        if not self.tracer:
            return
        try:
            self.tracer.log_llm_call(stage, system_prompt, user_prompt,
                                     temperature, raw_response)
        except Exception:
            pass  # 调试日志失败不影响主流程

    def _trace_parse_error(self, stage: str, raw_response: str, error: str) -> None:
        """记录 JSON 解析错误到调试日志"""
        if not self.tracer:
            return
        try:
            self.tracer.log_parse_error(stage, raw_response, error)
        except Exception:
            pass

    def _trace_context(self, label: str, content) -> None:
        """记录中间上下文数据"""
        if not self.tracer:
            return
        try:
            self.tracer.log_context(label, content)
        except Exception:
            pass

    # ========================================================================
    # Step 2.6: start() — 初始化对话
    # ========================================================================

    def start(self, user_message: str = "") -> str:
        """初始化 Agent 对话，返回第一条 reply。

        Args:
            user_message: 用户的第一条消息。为空时 Agent 主动问候。

        Returns:
            Agent 的第一条回复文本 (Markdown 格式)
        """
        self.state = AgentState()
        self.state.stage = "intent"
        self.state.turn_count = 0

        if not user_message:
            # 用户还没开口，Agent 主动问候
            self.state.history.append({"role": "agent", "content": ""})
            return (
                "你好！我是你的实验记录助手 🧪\n\n"
                "请告诉我今天做了什么实验，可以是简单的描述，"
                "我会逐步帮你把细节补充完整。\n\n"
                "例如：\n"
                "• \"做了钙钛矿电池，跟 EXP-003 一样但换了掺杂剂\"\n"
                "• \"水热法合成 ZnO 纳米棒，想看看不同温度的影响\"\n"
                "• \"测了一批 XRD，样品是上周煅烧的 TiO2\""
            )

        # 用户有初始消息
        self.state.history.append({"role": "user", "content": user_message})

        # 构建优先级清单文本
        priority_text_parts = []
        for exp_type, levels in PRIORITY_MAP.items():
            exp_label = {
                "photocatalysis": "光催化降解",
                "hydrothermal": "水热/溶剂热合成",
                "sol-gel": "溶胶-凝胶",
                "spin-coating": "旋涂法",
                "ball-milling": "球磨法",
                "electrochemistry": "电化学",
                "xrd": "XRD表征",
                "perovskite-solar": "钙钛矿太阳能电池",
                "other": "其他类型",
            }.get(exp_type, exp_type)
            priority_text_parts.append(
                f"{exp_type} ({exp_label}):\n"
                f"  优先级1: {', '.join(levels['priority_1'])}\n"
                f"  优先级2: {', '.join(levels['priority_2'])}\n"
                f"  优先级3: {', '.join(levels['priority_3'])}"
            )
        priority_map_text = "\n".join(priority_text_parts)

        # 调用 LLM
        system_prompt = self.PROMPT_INTENT.format(
            priority_map_text=priority_map_text,
        )
        raw = self._call_llm(system_prompt, user_message, temperature=0.2)
        self._trace_llm("intent", system_prompt, user_message, 0.2, raw)
        result = self._safe_parse_json(raw, "intent")

        # 解析结果 → 更新 state
        self.state.experiment_type = result.get("experiment_type", "other")
        self.state.missing = result.get("missing", [])
        self.state.references = result.get("references", [])
        self.state.fuzzy_references = result.get("fuzzy_references", [])

        # 合并 context
        context_update = result.get("context_update", {})
        self.state.merge_context(context_update)

        # 更新 turn_count，并进入下一阶段
        self.state.turn_count = 1
        self.state.stage = "detail"

        reply = result.get("reply", "好的，请继续描述你的实验。")
        self.state.history.append({"role": "agent", "content": reply})

        return reply

    # ========================================================================
    # Step 3.1: process_message — 状态机主循环
    # ========================================================================

    def process_message(self, user_message: str) -> dict:
        """处理用户消息，返回 Agent 响应和元数据。

        Returns:
            {
                "reply": str,           # Agent 回复 (Markdown)
                "stage": str,           # 当前阶段
                "completeness": float,  # 0.0~1.0
                "should_extract": bool, # 是否应进入提取阶段
            }
        """
        # 记录用户消息
        self.state.history.append({"role": "user", "content": user_message})
        self.state.turn_count += 1

        try:
            # 路由到当前阶段
            if self.state.stage == "intent":
                reply = self._stage_intent(user_message)
                self.state.stage = "detail"

            elif self.state.stage == "detail":
                reply = self._stage_detail(user_message)
                if self.turn_controller.should_enter_verify(self.state):
                    self.state.stage = "verify"

            elif self.state.stage == "verify":
                result = self._stage_verify()
                reply = result.get("reply", "")
                if result.get("ready_to_generate") or self.state.user_wants_done:
                    self.state.stage = "extract"

            elif self.state.stage == "extract":
                result = self._stage_extract()
                reply = "正在生成实验记录..."
                self.state.stage = "done"

            else:  # done 或未知
                reply = "对话已结束。你可以在预览面板中继续编辑实验记录。"

        except Exception as e:
            # LLM 调用或 JSON 解析失败时，不丢失当前状态
            reply = (
                "抱歉，处理你的消息时出了点问题。请重试。\n\n"
                f"（错误信息: {str(e)[:200]}）"
            )
            # 回退 turn_count（这轮未成功处理）
            self.state.turn_count -= 1

        # 记录 Agent 回复
        self.state.history.append({"role": "agent", "content": reply})

        should_extract = (self.state.stage == "extract")

        return {
            "reply": reply,
            "stage": self.state.stage,
            "completeness": self.state.completeness,
            "should_extract": should_extract,
        }

    # ========================================================================
    # Step 3.2: _stage_intent — 意图识别
    # ========================================================================

    def _stage_intent(self, user_message: str) -> str:
        """阶段 1: 判断实验类型 + 提取初始信息 + 追问"""
        # 构建优先级清单文本
        exp_labels = {
            "photocatalysis": "光催化降解",
            "hydrothermal": "水热/溶剂热合成",
            "sol-gel": "溶胶-凝胶",
            "spin-coating": "旋涂法",
            "ball-milling": "球磨法",
            "electrochemistry": "电化学",
            "xrd": "XRD表征",
            "perovskite-solar": "钙钛矿太阳能电池",
            "other": "其他类型",
        }
        priority_text_parts = []
        for exp_type, levels in PRIORITY_MAP.items():
            label = exp_labels.get(exp_type, exp_type)
            priority_text_parts.append(
                f"{exp_type} ({label}):\n"
                f"  优先级1: {', '.join(levels['priority_1'])}\n"
                f"  优先级2: {', '.join(levels['priority_2'])}\n"
                f"  优先级3: {', '.join(levels['priority_3'])}"
            )
        priority_map_text = "\n".join(priority_text_parts)

        # 调用 LLM
        system_prompt = self.PROMPT_INTENT.format(
            priority_map_text=priority_map_text,
        )
        raw = self._call_llm(system_prompt, user_message, temperature=0.2)
        self._trace_llm("intent", system_prompt, user_message, 0.2, raw)
        result = self._safe_parse_json(raw, "intent")

        # 更新 state
        self.state.experiment_type = result.get("experiment_type", "other")
        self.state.missing = result.get("missing", [])
        self.state.references = result.get("references", [])
        self.state.fuzzy_references = result.get("fuzzy_references", [])

        context_update = result.get("context_update", {})
        self.state.merge_context(context_update)

        return result.get("reply", "好的，请继续描述你的实验。")

    # ========================================================================
    # Step 3.3: _stage_detail — 细节追问 + 矛盾检测
    # ========================================================================

    def _stage_detail(self, user_message: str) -> str:
        """阶段 2: 追问缺失信息 + 检测矛盾 + 解析引用"""

        # 解析模糊引用
        self._resolve_fuzzy_references()

        # Python 确定性矛盾检测
        det_contradictions = self._detect_contradictions_deterministic()

        # 构建 prompt 变量
        context_summary = self._build_context_summary()
        references_detail = self._build_references_detail()
        missing_by_priority = self._build_missing_by_priority()

        system_prompt = self.PROMPT_DETAIL.format(
            experiment_type=self.state.experiment_type,
            turn_count=str(self.state.turn_count),
            max_turns=str(self.config.max_turns),
            context_summary=context_summary,
            references_detail=references_detail,
            missing_by_priority=missing_by_priority,
        )

        raw = self._call_llm(system_prompt, user_message, temperature=0.3)
        self._trace_llm("detail", system_prompt, user_message, 0.3, raw)
        self._trace_context("context_summary", context_summary)
        self._trace_context("references_detail", references_detail)
        self._trace_context("missing_by_priority", missing_by_priority)
        result = self._safe_parse_json(raw, "detail")

        # 更新 context
        context_update = result.get("context_update", {})
        self.state.merge_context(context_update)

        # 更新引用
        refs_update = result.get("references_update", [])
        for ref_id in refs_update:
            if ref_id not in self.state.references:
                self.state.references.append(ref_id)

        fuzzy_update = result.get("fuzzy_references_update", [])
        for fr in fuzzy_update:
            raw_text = fr.get("raw_text", "")
            if raw_text and not any(
                f.get("raw_text") == raw_text for f in self.state.fuzzy_references
            ):
                self.state.fuzzy_references.append({
                    "raw_text": raw_text,
                    "detected_in_turn": self.state.turn_count,
                    "status": "pending",
                    "candidates": [],
                    "resolved_id": "",
                })

        # 更新 missing
        self.state.missing = result.get("missing_after_update", self.state.missing)

        # 读取 LLM 判断的用户跳过意图
        if result.get("user_wants_done"):
            self.state.user_wants_done = True

        # 合并矛盾
        llm_contradictions = result.get("contradictions", [])
        all_contradictions = det_contradictions + llm_contradictions
        # 去重
        seen = set()
        unique = []
        for c in all_contradictions:
            key = (c.get("type", ""), c.get("field", ""),
                   c.get("claim_from", ""), c.get("claim_against", ""))
            if key not in seen:
                seen.add(key)
                unique.append(c)
        self.state.contradictions = unique

        return result.get("reply", "好的，请继续。")

    # ========================================================================
    # Step 3.4: Python 确定性矛盾检测
    # ========================================================================

    def _detect_contradictions_deterministic(self) -> list[dict]:
        """Python 端确定性矛盾检测。

        仅做: 引用实验存在性检查 + 精确字符串匹配的参数值比对。
        不做: 参数名称的语义等价判断（交给 LLM）。
        """
        contradictions = []

        for ref_id in self.state.references:
            ref_exp = self.store.load(ref_id)
            if not ref_exp:
                contradictions.append({
                    "type": "broken_reference",
                    "ref_id": ref_id,
                    "severity": "high",
                    "message": f"引用的实验 {ref_id} 不存在，请检查编号",
                })
                continue

            # 精确字符串匹配的参数比对
            user_params = self.state.context.get("process_parameters", [])
            ref_params = ref_exp.get("process_parameters", [])

            for up in user_params:
                if not isinstance(up, dict):
                    continue
                u_name = (up.get("parameter") or "").strip()
                u_val = (up.get("setpoint") or "").strip()
                if not u_name or not u_val:
                    continue

                for rp in ref_params:
                    if not isinstance(rp, dict):
                        continue
                    r_name = (rp.get("parameter") or "").strip()
                    r_val = (rp.get("setpoint") or "").strip()
                    if not r_name or not r_val:
                        continue

                    # 只做精确匹配
                    if u_name == r_name and u_val != r_val:
                        contradictions.append({
                            "type": "ref_mismatch_exact",
                            "field": u_name,
                            "claim_from": f"用户说 {u_name}: {u_val}",
                            "claim_against": f"{ref_id} 记录 {r_name}: {r_val}",
                            "severity": "medium",
                            "message": (
                                f"{u_name}：你说的'{u_val}'"
                                f"与 {ref_id} 记录的'{r_val}'不同，确认一下？"
                            ),
                        })

        return contradictions

    # ========================================================================
    # Step 3.5: 模糊引用解析
    # ========================================================================

    def _resolve_fuzzy_references(self) -> None:
        """遍历 pending 状态的模糊引用，执行本地关键词搜索。

        对于每个模糊引用:
        1. 复制 /api/resolve-reference 的第二层逻辑（本地关键词搜索）
        2. 填充 candidates 列表
        3. 不在此函数中调用 LLM
        """
        for fr in self.state.fuzzy_references:
            if fr.get("status") != "pending":
                continue

            text = (fr.get("raw_text") or "").strip()
            if not text or len(text) < 2:
                fr["status"] = "failed"
                continue

            # 第一步：检查是否为 EXP 编号（含 @前缀、大小写变体）
            import re
            m = re.match(r"^(?:@)?(EXP-\d{4}-\d{3})$", text, re.IGNORECASE)
            if m:
                exp_id = m.group(1).upper()
                exp = self.store.load(exp_id)
                if exp:
                    fr["status"] = "resolved"
                    fr["resolved_id"] = exp_id
                    fr["candidates"] = [{
                        "id": exp_id,
                        "title": exp.get("title", ""),
                        "date": exp.get("date", ""),
                        "tags": exp.get("tags", []),
                        "score": 1.0,
                    }]
                    if exp_id not in self.state.references:
                        self.state.references.append(exp_id)
                    continue
                else:
                    fr["status"] = "failed"
                    continue

            # 第二步：本地关键词搜索
            all_exps = self.store.list_all_full()
            results = []
            text_lower = text.lower()

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

                # 关键词匹配
                has_cjk = any('一' <= c <= '鿿' or '぀' <= c <= 'ヿ'
                             for c in text)

                if has_cjk:
                    # 中文: 用字符 bigram 作为匹配单元
                    tokens = [text_lower]
                    for i in range(len(text_lower) - 1):
                        tokens.append(text_lower[i:i+2])
                else:
                    # 非中文: 按空格分词
                    tokens = text_lower.split()

                for token in tokens:
                    if len(token) >= 2 and token in searchable:
                        score += 0.25

                # 标签匹配加分
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
            fr["candidates"] = results[:5]

            if not results:
                fr["status"] = "failed"
            else:
                # 有候选 → 把得分 >= 0.3 的候选加入 references，引用数据注入上下文
                fr["status"] = "resolved"
                fr["resolved_id"] = results[0]["id"]
                for c in results[:3]:
                    if c["score"] >= 0.3 and c["id"] not in self.state.references:
                        self.state.references.append(c["id"])

    # ========================================================================
    # Step 3.6: _stage_verify — 完整性检查
    # ========================================================================

    def _stage_verify(self) -> dict:
        """阶段 3: 对照完整 Schema 做逐字段完整性评估"""
        import json as _json

        context_full = _json.dumps(self.state.context, ensure_ascii=False, indent=2)

        # 把引用实验的数据追加到 context_full，LLM 自行判断是否算作"已填充"
        if self.state.references:
            ref_detail = self._build_references_detail()
            context_full += (
                "\n\n===== 引用实验的结构化数据（用户说与此一致，"
                "你可判断对应字段是否算作已填充）=====\n" + ref_detail
            )

        priority = get_schema_priority(self.state.experiment_type)

        system_prompt = self.PROMPT_VERIFY.format(
            experiment_type=self.state.experiment_type,
            turn_count=str(self.state.turn_count),
            max_turns=str(self.config.max_turns),
            context_full=context_full,
            core_fields=", ".join(priority["core_fields"]),
            important_fields=", ".join(priority["important_fields"]),
            optional_fields=", ".join(priority["optional_fields"]),
        )

        raw = self._call_llm(system_prompt,
                             "请执行完整性检查并返回 JSON。",
                             temperature=0.2)
        self._trace_llm("verify", system_prompt, "请执行完整性检查并返回 JSON。", 0.2, raw)
        self._trace_context("context_full_for_verify", context_full)
        result = self._safe_parse_json(raw, "verify")

        self.state.completeness = result.get("completeness", 0.0)

        return result

    # ========================================================================
    # Step 3.7: _stage_extract — 生成自然语言描述
    # ========================================================================

    def _stage_extract(self) -> dict:
        """阶段 4: 生成完整自然语言描述"""
        import json as _json

        context_full = _json.dumps(self.state.context, ensure_ascii=False, indent=2)

        # 构建对话摘要
        conv_lines = []
        for entry in self.state.history[-8:]:  # 取最近 8 条
            role = "用户" if entry["role"] == "user" else "Agent"
            content = entry["content"]
            if len(content) > 100:
                content = content[:97] + "..."
            conv_lines.append(f"{role}: {content}")
        conversation_summary = "\n".join(conv_lines)

        # 引用列表
        if self.state.references:
            ref_display = ", ".join(f"@{r}" for r in self.state.references)
        else:
            ref_display = "（无引用）"

        system_prompt = self.PROMPT_EXTRACT.format(
            context_full=context_full,
            conversation_summary=conversation_summary,
            references_list=ref_display,
        )

        raw = self._call_llm(system_prompt,
                             "请根据以上信息生成完整的实验描述。",
                             temperature=0.3)
        self._trace_llm("extract", system_prompt,
                        "请根据以上信息生成完整的实验描述。", 0.3, raw)
        self._trace_context("context_full_for_extract", context_full)
        result = self._safe_parse_json(raw, "extract")

        self.state.final_notes = result.get("notes", "")
        if result.get("title"):
            if not self.state.context.get("title"):
                self.state.context["title"] = result["title"]

        return result

    # ========================================================================
    # 辅助方法
    # ========================================================================

    def _build_missing_by_priority(self) -> str:
        """构建按优先级排序的缺失项列表文本"""
        if not self.state.missing:
            return "（所有关键信息已齐备）"

        exp_type = self.state.experiment_type
        levels = PRIORITY_MAP.get(exp_type, PRIORITY_MAP["other"])

        lines = []
        for level_name, level_label in [("priority_1", "必问"),
                                         ("priority_2", "重要"),
                                         ("priority_3", "补充")]:
            level_items = levels.get(level_name, [])
            still_missing = [
                item for item in level_items
                if item in self.state.missing
            ]
            if still_missing:
                lines.append(f"{level_label}: {', '.join(still_missing)}")

        return "\n".join(lines) if lines else "（所有关键信息已齐备）"
