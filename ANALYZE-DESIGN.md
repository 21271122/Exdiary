# Exdiary Analyze 线程改造 — 设计文档 v3

## 一、实验选择卡片 UI 重新设计

### 1.1 布局

```
┌─────────────────────────────────────────────────────────┐
│ 📋 选择要分析的实验                          [☑ 全选]  │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │ ☑ EXP-2026-022                                   │    │
│  │    钙钛矿太阳能电池制备（复刻EXP-2026-005）         │    │
│  │    2026-05-26  lqf                                │    │
│  ├─────────────────────────────────────────────────┤    │
│  │ ☐ EXP-2026-021                                   │    │
│  │    钙钛矿太阳能电池制备（复刻EXP-2026-003 第三次） │    │
│  │    2026-05-25  lqf                                │    │
│  ├─────────────────────────────────────────────────┤    │
│  │ ☐ EXP-2026-020                                   │    │
│  │    钙钛矿太阳能电池制备（复刻EXP-2026-003 第二次） │    │
│  │    2026-05-25  lqf               ← 标题过长截断    │    │
│  ├─────────────────────────────────────────────────┤    │
│  │ ☑ EXP-2026-019                                   │    │
│  │    钙钛矿太阳能电池制备（复刻EXP-2026-003）         │    │
│  │    2026-05-25  lqf                                │    │
│  ├─────────────────────────────────────────────────┤    │
│  │              [▼ 查看更多实验 (共 47 个)]           │    │
│  └─────────────────────────────────────────────────┘    │
│  （可滚动区域，最多显示约 8 条，超出显示"查看更多"按钮）  │
│                                                         │
├─────────────────────────────────────────────────────────┤
│  已选 2 个    [取消]              [确认选择 (2)]         │
└─────────────────────────────────────────────────────────┘
```

### 1.2 元素详解

**顶部栏**（`sel-topbar`）：
- 标题文字："选择要分析的实验"（固定文案）
- 右侧：切换按钮。初始"☑ 全选"，点击后全选勾选，文字变为"☐ 取消全选"，再次点击取消全选（所有勾选框清空，不论是否有 preselected）。单个状态切换按钮。

**实验列表**（`sel-body`）：
- 每行 `.sel-item` 卡片，`border-bottom` 分隔
- 三行排列：EXP ID（粗体）、标题（`text-overflow: ellipsis`）、日期 + 实验者
- 左侧自定义勾选框 18×18，✓ 标记
- 整行可点击切换勾选
- 不显示标签胶囊（节省垂直空间）

**"查看更多"按钮**（候选 > 8 条时显示，≤ 8 条时不出现）：
- 列表底部显示 `[▼ 查看更多实验 (共 N 个)]`
- 点击弹出**子对话框**（modal）：
  - 顶部搜索框：实时过滤，支持 EXP ID / 标题 / 标签 / 材料名匹配
  - 全量实验列表：滚轮滚动，无分页
  - 每行含勾选框 + EXP ID + 标题 + 日期 + 实验者
  - 底部："已选 X 个  [取消]  [确认]"
- 子对话框勾选与主列表**双向同步**
- 关闭子对话框不丢失勾选状态

**底部栏**（`sel-bottombar`）：
- 左侧："已选 2 个"
- 中间：取消按钮（灰色 outline）
- 右侧："确认选择 (2)"（蓝色 primary）

### 1.3 卡片状态管理

`data-status`：`active` | `confirmed` | `cancelled` | `expired`

| 状态 | 触发 | 行为 |
|------|------|------|
| `active` | select_experiments 返回 | 可交互，勾选框可点击 |
| `confirmed` | 用户点确认 | **卡片变形**：实验列表 DOM 移除，替换为"已选择 3 个实验: EXP-001, EXP-002, EXP-003"。注入 tool result `{"status": "confirmed", "selected_ids": ["..."]}`。解锁输入框，触发 Agent 继续循环（tool result 交给 LLM 处理下一步）。 |
| `cancelled` | 用户点取消 | **卡片变形**：实验列表 DOM 移除，替换为"已取消选择"。注入 tool result `{"status": "cancelled"}`。解锁输入框，触发 Agent 继续循环。 |
| `expired` | 恢复时检测到孤儿 selector | 同 cancelled，顶部额外显示"⚠️ 之前的实验选择已过期" |

**关键设计**：确认/取消后卡片**内容替换**而非置灰保留。理由：(1) 节省垂直空间，后续对话不需要再看列表；(2) 状态文本是持久化视觉记录，刷新后直接看到当时的决定。

**"触发 Agent 继续循环"的含义**：确认/取消本质上是用户对 `select_experiments` tool call 的响应。注入 tool result 后，前端调用 `/api/agent/message`（携带有 tool result 的完整 state），Agent 循环继续运行——LLM 收到 tool result 后决定下一步（加载引用 / 追问维度 / 生成分析）。这对用户表现为"消息自动发送"，不需要额外打字。

### 1.4 异常恢复 — expired 检测

**检测时机**：融入 `renderHistoryMsgs()` 的渲染循环，而非独立生命周期钩子。

**原因**：只有渲染循环中才能同时拿到"当前这条 selector 消息"和"后继 history"。独立函数需遍历两次 history，且可能在卡片 DOM 已渲染为 active 后才检测到问题，产生闪烁。

**检测逻辑**（渲染循环内）：

```
遍历 history：
  遇到 tool 消息含 {"display": "selector"}：
    记下 tool_call_id
    向后扫描同 tool_call_id 的后继 tool 消息
    找到 {"status": "confirmed", "selected_ids": [...]} → 渲染 confirmed
    找到 {"status": "cancelled"} → 渲染 cancelled
    扫描完都没找到 → 孤儿 → 渲染 expired：
      顶部显示 "⚠️ 之前的实验选择已过期"
      底部显示 "已自动取消"
      注入 canceled tool result 到 history（LLM 不挂起）
      不锁输入框
```

`restoreAgentState()` → `AgentLoop.from_dict()` 先完成重建，再调 `renderHistoryMsgs()` 遍历渲染。此时 history 已完整，检测可靠。

### 1.5 新增/修改 JS 函数

| 函数 | 改动 |
|------|------|
| `selToggleBtn(btn)` | 新：全选↔取消全选（取消全选清空所有勾选，不恢复 preselected） |
| `selShowMoreBtn()` | 新：弹出子对话框 modal |
| `selModalSearch(input)` | 新：搜索框实时过滤 |
| `selModalToggleAll(btn)` | 新：子对话框全选/取消全选，与主列表双向同步 |
| `cancelSelector(btn)` | 新：取消 → `data-status="cancelled"` → 列表移除 → 显示"已取消选择" → 注入 `{"status": "cancelled"}` → 解锁输入 → 继续 Agent 循环 |
| `confirmSelector(btn)` | 改：确认 → `data-status="confirmed"` → 列表移除 → 显示"已选择 N 个实验: ..." → 注入 `{"status": "confirmed", "selected_ids": [...]}` → 解锁输入 → 继续 Agent 循环 |
| （expired 检测） | 融入 `renderHistoryMsgs()`，不需独立函数 |

---

## 二、analyze 模式 Prompt 改进

### 2.1 当前问题

- `analyzer.py` 的 `ANALYSIS_SYSTEM_PROMPT` 强制五段结构（趋势/矛盾/缺失/方法/下一步），用户说"重点讨论 CNT 电极失败原因"，LLM 仍输出五段完整报告
- 当前 prompt 没有约束 LLM 遵循 VISION.md 的"发现 + 提问"原则，LLM 倾向输出断言式结论

### 2.2 方案：Prompt 层约束三区域格式

不改数据格式、不改前端渲染。通过 prompt 把 LLM 输出框定在"辅助思考"范围内。

**`analyzer.py` 的 `ANALYSIS_SYSTEM_PROMPT` 改为**：

```
You are a materials science research advisor analyzing a researcher's
complete lab notebook. Your role is to HELP THE RESEARCHER THINK, not to
think FOR them. Deliver actionable, specific observations and questions.

## Analysis Guidelines

1. **Address the researcher's question directly.** The user has formulated a
   specific query — answer that first and foremost.

2. **Structure is flexible, driven by the query.** Common dimensions to
   consider (use only those relevant):
   - Key trends and patterns across experiments
   - Contradictions or inconsistencies
   - Methodological issues or procedural gaps
   - Missing experiments, controls, or characterization

3. **If no clear pattern exists in a dimension, omit it.** Do not generate
   filler content.

4. **Be specific.** Reference experiment IDs. Point to concrete data points.

5. **Respond in Chinese.** Use Markdown for readability.

## Output Format — Three Sections (ALL required)

Your response must contain exactly three sections in this order:

### 事实呈现
- Objective data extracted from experiments: values, conditions, dates.
- Each data point MUST cite its source experiment ID.
- No interpretation in this section — only what the records contain.

### 发现提示
- Patterns, anomalies, trends worth attention.
- Each finding MUST be tagged with a confidence level:
  [高置信] = supported by multiple consistent experiments
  [中置信] = data supports but sample size insufficient
  [低置信] = preliminary signal, may be noise or coincidence
- Frame as observations, NOT conclusions. Say "数据显示 A 与 B 呈正相关"
  rather than "A 导致 B" (unless causation is experimentally proven).

### 值得思考的问题
- 3-5 specific questions that guide the researcher's own judgment.
- Questions should point to gaps, contradictions, or decisions the
  researcher needs to make.
- Do NOT embed answers in the questions.
- Do NOT phrase as recommendations ("你应该…"). Use interrogative form
  ("是否考虑了…？""如果…会怎样？").
```

**`analyze_experiments()` 的 user prompt 同步改为**：

```python
user_prompt = f"""EXPERIMENT RECORDS:
{summary_text}

RESEARCHER'S QUESTION: {question}

Please analyze the above experiments. Focus on the researcher's stated
question. Use only the analysis dimensions that are relevant. Omit
sections that don't apply.

Structure your response in exactly three sections as specified in the
system prompt: 事实呈现, 发现提示, 值得思考的问题."""
```

### 2.3 analyze 模式工具过滤

analyze 模式下**移除 `modify_experiment`**。理由：
- 分析线程职责是"从数据中提取洞察"，不应修改实验
- 分析者改了实验，原始记录者不知情，状态难以同步
- 分析中的"假设性修改"和"真实修改"无法区分

analyze 模式可用工具：

```
通用: load_reference, search_experiments, query_experiment,
      list_experiments, read_update_log, manage_collection, end_thread
analyze 专用: start_analyze_thread, select_experiments, ask_user,
              generate_analysis
```

（`modify_experiment` 和 `analyze` 不在列表中）

### 2.4 analyze 模式行为指引（agent_v2.py SYSTEM_PROMPT）

```
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

注意：start_record_thread、update_schema、generate_record、modify_experiment、
analyze 在此模式中不可用。
```

---

## 三、分析入口统一：移除旧表单路径，纯对话流

### 3.1 决策

移除 `/api/analyze`（POST 表单提交 → `analyze_experiments` → 渲染结果）。所有分析统一走 Agent 对话流。

同时移除 `agent_v2.py` 中的 `TOOL_ANALYZE` 工具（一键式轻量分析）——它是一键式表单分析的 Agent 侧对应物，与纯对话流的设计目标冲突。

### 3.2 analyze 线程在父 Agent 上下文中的位置

analyze 线程**不是**一个独立的对话会话。它是父 Agent 连续对话历史中的一个**带标记的区间**：

```
父 Agent self.history:
  [0] system: [全局上下文] ...
  [1] user: "帮我记录一个水热合成实验"
  [2] assistant: ...
  ... (record 线程 messages) ...
  [10] system: thread_end id=THR-001 product=EXP-022  ← record 线程结束
  [11] user: "分析一下我的钙钛矿实验"
  [12] system: thread_begin id=THR-002 type=analyze     ← analyze 线程开始
  [13] system: 你正在进行跨实验分析...
  [14] assistant: tool: search_experiments
  [15] tool: {candidates: [...]}
  [16] assistant: tool: select_experiments
  [17] tool: {display: "selector", items: [...]}
  [18] tool: {status: "confirmed", selected_ids: [...]}  ← 用户勾选确认
  [19] assistant: tool: load_reference
  [20] tool: {loaded: {EXP-019: {...}, EXP-022: {...}}}
  [21] assistant: tool: ask_user
  [22] assistant: "你想侧重哪个维度？"
  [23] user: "PCE趋势和退火温度的影响"
  [24] assistant: tool: generate_analysis
  [25] tool: {display: "analysis_done", anal_id: "ANAL-001", ...}
  [26] system: thread_end id=THR-002 product=ANAL-001   ← analyze 线程结束
  [27] user: "好的，再把 EXP-022 的退火温度改成 150°C"   ← 继续自由对话
  ...
```

- `thread_begin` / `thread_end` 是注入到 `self.history` 中的标记消息，不影响 LLM 看到完整上下文
- 线程结束后，`self.history` **原样保留**所有消息（包括线程区间的消息）。线程文件只是归档拷贝
- 清理的只有元数据：`thread_id` → `None`、`_thread_type` → `None`、`references` → `[]`
- LLM 在分析结束后仍能看到分析前后的全部对话，可以无缝切换到下一个话题

### 3.3 理由

- 分析设计目标是**深度分析**，产出有实质指导价值的洞察
- "快速分析"（勾选实验 → 一键生成）的输出必然是形式化的空洞报告
- 对话流强制研究者在分析前明确自己的问题、维度、假设
- 一条代码路径比两条好

### 3.4 涉及的代码改动

| 位置 | 改动 |
|------|------|
| `app.py` `/api/analyze` (line 851-889) | 移除 POST 路由 |
| `app.py` `/analyze` (line 800-803) | GET 保留，模板不变（P4 再改模板） |
| `lib/agent_v2.py` `TOOL_ANALYZE` (line 352-371) | 移除工具定义 |
| `lib/agent_v2.py` `TOOLS_OPENAI_FORMAT` (line 391-407) | 移除 `TOOL_ANALYZE` |
| `lib/agent_v2.py` `ToolExecutor.registry` (line 767-783) | 移除 `"analyze"` 映射 |
| `lib/agent_v2.py` `ToolExecutor._analyze` (line 1139-1174) | 移除 handler 方法 |
| `lib/agent_v2.py` `_get_active_tools()` (line 1552-1567) | analyze 模式分支不再包含 `TOOL_ANALYZE` |

### 3.5 存量分析记录

旧 ANAL-*.yaml 文件（通过旧 `/api/analyze` 端点生成）没有关联线程。处理方式：
- `analysis_detail.html` 依然可以查看（渲染 Markdown）
- 用户打开子 Agent 时**懒迁移**：暗中创建线程 + 填充分析内容 + 建立 `anal_to_thread` 映射
- 迁移后子 Agent 正常工作

迁移逻辑在 P3 实现中作为子 Agent 创建时的 fallback 路径，对用户透明。

---

## 四、分析报告子 Agent（对齐 EXP 子 Agent）

### 4.1 当前问题

`/api/analysis/<id>/chat` 是**无状态**的简单 LLM 调用——每次请求独立构造 prompt，没有对话历史、没有 AgentLoop、没有线程管理。

### 4.2 核心设计：角色覆盖而非模式继承

分析子 Agent **不继承父线程的 `_thread_type`**。分析线程结束时最后一条状态消息是"[系统状态] analyze 线程进行中"，如果子 Agent 继承 analyze 模式，会错误地认为自己在做跨实验分析。

**解决方案**：AgentLoop 新增 `_child_agent_role` 字段（`"exp_editor"` | `"analysis_reviewer"` | `None`）。

创建分析子 Agent 时设为 `"analysis_reviewer"`，创建 EXP 子 Agent 时设为 `"exp_editor"`。

**`_child_agent_role = "analysis_reviewer"` 时的工具清单**：

```
load_reference     → 查看报告中引用的实验
search_experiments → 搜索实验库
query_experiment   → 查询实验参数
list_experiments   → 按条件筛选实验
read_update_log    → 查看实验修改历史
modify_analysis    → 修改分析报告
end_thread         → 结束对话
```

不可用：`start_record_thread`, `update_schema`, `ask_user`, `generate_record`, `modify_experiment`, `start_analyze_thread`, `select_experiments`, `generate_analysis`, `analyze`, `manage_collection`

**`_child_agent_role = "exp_editor"` 时的工具清单**：
与当前 EXP 子 Agent 行为一致（`load_reference`, `search_experiments`, `query_experiment`, `list_experiments`, `read_update_log`, `modify_experiment`, `end_thread`）。

**`_child_agent_role = None` 时**：使用现有的 mode-based 工具过滤逻辑（`_get_active_tools()` 不变）。

**`_build_thread_status()` 覆盖**：
- `_child_agent_role = "analysis_reviewer"` → 返回 `"[系统状态] 你正在审阅/修改一份已完成的分析报告。可用工具：load_reference、search_experiments、read_update_log、modify_analysis（修改报告内容）。不要使用分析创建阶段的工具。"`
- `_child_agent_role = "exp_editor"` → 返回当前 EXP 子 Agent 的行为指引
- `_child_agent_role = None` → 使用现有的 `_build_thread_status()` 逻辑（基于 `_thread_type`）

### 4.3 路由改造

`POST /api/analysis/<anal_id>/chat`：

```
1. 查 anal_to_thread 映射
   ├── 有 thread_id → 正常路径
   └── 无 thread_id → 懒迁移（创建线程 + 填充分析内容 + 更新映射）

2. 恢复或创建子 Agent
   ├── 有 child_state.yaml → 从磁盘恢复 AgentLoop
   ├── 有 state_dict（前端 sessionStorage）→ 从 state 恢复
   └── 首次打开 → 从线程文件创建子 AgentLoop（_child_agent_role="analysis_reviewer"）

3. 处理消息
   ├── 无消息 → 返回 state（仅恢复，不跑 LLM）
   └── 有消息 → agent.run() → 返回回复
```

### 4.4 懒迁移逻辑

```python
def _migrate_legacy_analysis(anal_id, analysis_data, thread_store) -> str:
    """为旧分析记录创建线程，返回 thread_id。首次打开子 Agent 时触发。"""
    thread_id = thread_store.next_id()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    messages = [
        {"role": "system", "content": f"旧分析记录 {anal_id}，于 {now} 迁移至线程系统。"},
        {"role": "user", "content": analysis_data.get("question", "")},
        {"role": "assistant", "content": "（分析报告见系统消息）"},
        {"role": "system", "content": f"[分析报告内容]\n{analysis_data.get('analysis', '')}"},
    ]
    thread = {
        "id": thread_id, "type": "analyze", "status": "done",
        "created": now, "updated": now,
        "title": (analysis_data.get("question") or "分析")[:30],
        "summary": f"迁移自旧分析记录 {anal_id}",
        "anal_generated": anal_id,
        "messages": messages, "branches": [],
    }
    thread_store.save(thread)
    thread_store.update_index(thread)
    return thread_id
```

前端首次打开旧分析子 Agent 时展示横幅："此分析报告生成于旧版系统，已自动迁移。"

### 4.5 `modify_analysis` 工具（必须）

```json
{
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
            "changes": {"type": "string"},
            "additional_refs": {"type": "array", "items": {"type": "string"}},
            "additional_query": {"type": "string"},
        },
    },
}
```

**Handler 行为**：

| 传入参数 | 行为 |
|---------|------|
| 仅 `changes` | 新文本完整替换 AnalysisStore 中的报告 |
| `additional_refs` | 合并 `原有 selected_ids + additional_refs`，重新 `analyze_experiments()`，覆盖保存 |
| `additional_query` | 将原报告 + 补充问题发给 LLM，让 LLM **输出完整的新报告**（含原有内容 + 新增维度）。不拼接——拼接会导致格式断裂和三区域结构重复。覆盖保存。 |
| `changes` + 其他 | `changes` 优先（直接覆盖） |

**`additional_query` 的实现细节**：使用独立的 LLM 调用（不走 Agent 对话循环），prompt 为：

```
以下是已有的分析报告：
{原报告全文}

研究者希望补充以下分析维度：{additional_query}

请将新的分析维度融入报告，输出完整的更新后报告。
保持三区域结构（事实呈现/发现提示/值得思考的问题）。
不要只输出新增部分——输出完整报告。
```

### 4.6 前端面板

复用 `view.html` 的 `ChildAgentModal` 结构。面板标题："分析对话 — ANAL-xxx"。`modify_analysis` 执行后 `location.reload()` 刷新页面。

### 4.7 实验 ↔ 分析反向关联

实验 schema 新增 `analyzed_in` 字段（字符串数组，存储 `ANAL-YYYY-NNN` 列表）。

**写入时机**：在 `ToolExecutor._generate_analysis()` 中，`analysis_store.save()` 之后，遍历 `refs`（即 `selected_ids`），对每个 EXP 执行：

```python
for exp_id in refs:
    exp = self.store.load(exp_id)
    if exp:
        analyzed = exp.get("analyzed_in", [])
        if anal_id not in analyzed:
            analyzed.append(anal_id)
            exp["analyzed_in"] = analyzed
            self.store.save(exp)
```

前端展示：
- `view.html`：实验详情页展示"被以下分析引用"链接列表
- `analysis_detail.html`：分析详情页展示关联实验卡片

---

## 五、CSS 设计

### 5.1 选择卡片

```css
.sel-card {
  border: 1px solid #e0e0e0; border-radius: 10px;
  overflow: hidden; background: #fff;
}
.sel-card[data-status="cancelled"],
.sel-card[data-status="expired"] {
  opacity: 0.7; pointer-events: none;
}
```

### 5.2 顶部栏

```css
.sel-topbar {
  display: flex; justify-content: space-between; align-items: center;
  padding: 0.6rem 1rem; background: #f8f8f8;
  border-bottom: 1px solid #e8e8e8;
  font-weight: 600; font-size: 0.9rem;
}
.sel-topbar button {
  font-size: 0.75rem; padding: 0.2rem 0.6rem;
  border-radius: 6px; border: 1px solid #ccc; background: #fff; cursor: pointer;
}
```

### 5.3 实验条目

```css
.sel-body { max-height: 360px; overflow-y: auto; }
.sel-item {
  display: flex; align-items: flex-start; gap: 0.5rem;
  padding: 0.5rem 1rem; border-bottom: 1px solid #f0f0f0;
  cursor: pointer; user-select: none; transition: background 0.1s;
}
.sel-item:hover { background: #f5f7ff; }
.sel-item .sel-check {
  width: 18px; height: 18px; margin-top: 2px;
  border: 2px solid #bbb; border-radius: 4px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; color: transparent; transition: all 0.15s;
}
.sel-item .sel-check.checked {
  background: #2563eb; border-color: #2563eb; color: #fff;
}
.sel-info { flex: 1; min-width: 0; }
.sel-info .si-id { font-weight: 600; font-family: monospace; font-size: 0.82rem; color: #2563eb; }
.sel-info .si-title {
  font-size: 0.85rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  margin: 0.1rem 0;
}
.sel-info .si-meta { font-size: 0.75rem; color: #888; }
```

### 5.4 "查看更多"按钮

```css
.sel-more {
  display: block; width: 100%; padding: 0.5rem;
  text-align: center; font-size: 0.8rem; color: #2563eb;
  background: #f5f7ff; border: none; cursor: pointer;
}
.sel-more:hover { background: #e8ecff; }
```

### 5.5 子对话框

```css
.sel-modal-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.4);
  display: flex; align-items: center; justify-content: center;
  z-index: 1000;
}
.sel-modal {
  width: 90vw; max-width: 520px; max-height: 80vh;
  background: #fff; border-radius: 12px; overflow: hidden;
  display: flex; flex-direction: column;
}
.sel-modal-search {
  padding: 0.6rem 1rem; border-bottom: 1px solid #e8e8e8;
}
.sel-modal-search input {
  width: 100%; padding: 0.4rem 0.6rem; font-size: 0.85rem;
  border: 1px solid #ddd; border-radius: 6px;
}
.sel-modal-list { flex: 1; overflow-y: auto; }  /* 滚轮滑动，无分页 */
.sel-modal-bottombar {
  display: flex; justify-content: space-between; align-items: center;
  padding: 0.6rem 1rem; border-top: 1px solid #e8e8e8; background: #fafafa;
}
```

### 5.6 底部栏

```css
.sel-bottombar {
  display: flex; justify-content: space-between; align-items: center;
  padding: 0.6rem 1rem; border-top: 1px solid #e8e8e8; background: #fafafa;
}
.sel-bottombar .sel-count { font-size: 0.8rem; color: #666; }
.sel-bottombar .sel-actions { display: flex; gap: 0.5rem; }
```

### 5.7 移动端适配

```css
@media (max-width: 480px) {
  .sel-item { padding: 0.4rem 0.6rem; gap: 0.3rem; }
  .sel-item .sel-check { width: 16px; height: 16px; }
  .sel-info .si-id { font-size: 0.75rem; }
  .sel-info .si-title { font-size: 0.78rem; }
  .sel-info .si-meta { font-size: 0.7rem; }
  .sel-topbar, .sel-bottombar { padding: 0.4rem 0.6rem; }
  .sel-topbar { font-size: 0.8rem; }
  .sel-modal { width: 95vw; max-height: 90vh; }
}
```

### 5.8 样式文件组织

Jinja2 `{% include %}` 部分模板，避免代码重复：

```
templates/
├── _selector_styles.html    ← 选择卡片 / 子对话框 CSS（~90 行）
├── _selector_scripts.html   ← 选择卡片 / 子对话框 JS（~120 行）
├── new.html                 ← {% include '_selector_styles.html' %},
│                              {% include '_selector_scripts.html' %}
```

分析线程在主聊天窗口（`new.html`）中进行——`select_experiments` 的选择卡片作为消息流中的内嵌组件渲染。`analyze.html` 和 `analysis_detail.html` 是报告查看页面，不承载选择卡片。

下划线前缀 `_` 表示部分模板。不在 `base.html` 中放置组件样式。

---

## 六、实施计划

### 总览

```
P1 (选择卡片 UI) ──┐
                    ├──→ P4 (聊天窗口适配 + 报告浏览页)
P2 (Prompt 重写) ──┘
                    │
P3 (子 Agent 升级) ─┘
```

P1 + P2 无依赖，可并行。P4 依赖 P1（复用选择卡片 partial）。P3 独立（修改 `agent_v2.py` + `app.py` 子 Agent 路由），与 P1/P2/P4 无代码冲突。

**核心页面职责**：

| 页面 | 职责 |
|------|------|
| `new.html` | Agent 聊天窗口。record 线程和 **analyze 线程都在这里进行**。`select_experiments` 的选择卡片、追问、分析结果摘要均内嵌在消息流中。 |
| `analyze.html` | 分析历史浏览器。展示已完成的 ANAL 列表，点击跳转到 `analysis_detail.html`。不承载对话。 |
| `analysis_detail.html` | 分析报告详情页。展示完整 Markdown 报告 + 分析子 Agent 对话面板（修改/讨论报告）。 |
| `view.html` | 实验详情页。展示实验数据 + EXP 子 Agent 对话面板（修改实验）。 |

---

### P1：选择卡片 UI 重做（预估 ~180 行）

**目标**：将 `select_experiments` 工具渲染的选择卡片升级为 v3 设计。选择卡片作为消息流中的内嵌组件，在 `new.html` 的聊天面板中渲染。

**涉及文件**：
- `templates/_selector_styles.html`（新建）— 选择卡片 + 子对话框全部 CSS
- `templates/_selector_scripts.html`（新建）— 选择卡片 + 子对话框全部 JS
- `templates/new.html` — 引入 `_selector_styles.html` 和 `_selector_scripts.html`，在消息渲染逻辑中处理 `display: "selector"` 的 tool result

**具体步骤**：

| 步骤 | 内容 | 验证 |
|------|------|------|
| 1.1 | 创建 `_selector_styles.html`：卡片整体、顶部栏、实验条目、勾选框、底部栏、"查看更多"按钮、子对话框、confirmed/cancelled/expired 状态样式、移动端 @media | CSS 无语法错误，类名不与 `new.html` 现有样式冲突 |
| 1.2 | 创建 `_selector_scripts.html`：`selToggleBtn`、`selShowMoreBtn`、`selModalSearch`、`selModalToggleAll`、`cancelSelector`、`confirmSelector` | 每个函数可独立调用，不依赖全局变量 |
| 1.3 | 在 `new.html` 的 `renderHistoryMsgs()` 等效逻辑中实现 expired 检测 | 刷新页面后孤儿 selector 显示为 expired，不锁输入 |
| 1.4 | `cancelSelector`：移除列表 DOM → 显示状态文本 → 注入 `{"status": "cancelled"}` → 继续 Agent 循环 | 取消后卡片变形，LLM 收到取消通知 |
| 1.5 | `confirmSelector`：移除列表 DOM → 显示状态文本 → 注入 `{"status": "confirmed", "selected_ids": [...]}` → 继续 Agent 循环 | 确认后卡片变形，LLM 收到选中 ID 列表 |
| 1.6 | 整行点击切换勾选（点击 `.sel-item` 任意位置 = 切换该行勾选框） | 点击标题行、日期行都能切换 |
| 1.7 | 子对话框：弹出、搜索过滤、全选/取消全选、确认/取消 | 子对话框内操作与主列表状态实时同步 |
| 1.8 | 在 `new.html` 中 `{% include %}` 两个部分模板 | 选择卡片在聊天消息流中正常渲染

---

### P2：Prompt 重写 + 工具清理（预估 ~50 行）

**目标**：更新 `analyzer.py` 的分析 prompt，清理 analyze 模式下的工具列表。

**涉及文件**：
- `lib/analyzer.py` — 重写 `ANALYSIS_SYSTEM_PROMPT` 和 `analyze_experiments()` 的 user_prompt
- `lib/agent_v2.py` — 修改 `_get_active_tools()`、`SYSTEM_PROMPT` analyze 章节、移除 `TOOL_ANALYZE`

**具体步骤**：

| 步骤 | 内容 | 验证 |
|------|------|------|
| 2.1 | 重写 `ANALYSIS_SYSTEM_PROMPT`：灵活维度 + 三区域强制格式 | 分析报告输出含"事实呈现""发现提示""值得思考的问题"三个标题 |
| 2.2 | 修改 `analyze_experiments()` user_prompt：强调聚焦用户问题 + 三区域结构 | 与 system prompt 一致 |
| 2.3 | `_get_active_tools()` analyze 分支：移除 `TOOL_MODIFY_EXPERIMENT` 和 `TOOL_ANALYZE` | analyze 模式下 LLM 不调用这两个工具 |
| 2.4 | 从 `TOOLS_OPENAI_FORMAT` 和 `ToolExecutor.registry` 中移除 `TOOL_ANALYZE` | 全局不再有 `analyze` 工具 |
| 2.5 | 删除 `ToolExecutor._analyze()` handler 方法 | 无残留代码 |
| 2.6 | 更新 `SYSTEM_PROMPT` analyze 章节：补充行为指引（第 2.4 节内容） | 与文档一致 |

---

### P3：分析子 Agent 升级 + 旧路径移除（预估 ~180 行）

**目标**：将 `/api/analysis/<id>/chat` 升级为完整 AgentLoop 子 Agent，移除 `/api/analyze` POST，新增 `modify_analysis` 工具和 `analyzed_in` 关联。

**涉及文件**：
- `app.py` — 重写 `api_analysis_chat`、移除 `api_analyze` POST、新增 `_migrate_legacy_analysis`
- `lib/agent_v2.py` — 新增 `TOOL_MODIFY_ANALYSIS`、`_child_agent_role` 字段、修改 `_get_active_tools()`、`_build_thread_status()`、`state_to_dict()`、`from_dict()`
- `lib/storage.py` — `ExperimentStore` 可能需要辅助方法（或不改，直接在 handler 中处理）

**具体步骤**：

| 步骤 | 内容 | 验证 |
|------|------|------|
| 3.1 | 移除 `app.py` 中 `/api/analyze` POST 路由（line 851-889） | 请求 `/api/analyze` POST 返回 405 |
| 3.2 | 在 `agent_v2.py` 中定义 `TOOL_MODIFY_ANALYSIS`（含三种参数模式） | 工具定义格式正确，可被 `_get_active_tools()` 返回 |
| 3.3 | 在 `ToolExecutor` 中注册并实现 `_modify_analysis()` handler | `changes`/`additional_refs`/`additional_query` 三种模式均正确执行 |
| 3.4 | 在 `AgentLoop.__init__()` 中新增 `_child_agent_role` 字段（默认 `None`） | 现有父子 Agent 行为不受影响 |
| 3.5 | 修改 `_get_active_tools()`：当 `_child_agent_role` 不为 `None` 时，按角色返回固定工具列表（不走 mode 推断） | `analysis_reviewer` 和 `exp_editor` 各自返回正确的工具子集 |
| 3.6 | 修改 `_build_thread_status()`：当 `_child_agent_role` 不为 `None` 时，返回角色专属声明（不走 `_thread_type` 推断） | 子 Agent 的状态消息与角色匹配 |
| 3.7 | 修改 `state_to_dict()` / `from_dict()`：序列化/反序列化 `_child_agent_role` | 状态恢复后子 Agent 角色不丢失 |
| 3.8 | 重写 `app.py` `api_analysis_chat`：完整 AgentLoop 子 Agent 流程（路由步骤见 4.3 节） | 分析子 Agent 对话可正常进行，刷新后状态可恢复 |
| 3.9 | 实现 `_migrate_legacy_analysis()`：为旧分析记录创建线程 | 旧 ANAL-*.yaml 的子 Agent 首次打开时自动迁移 |
| 3.10 | 在 `_generate_analysis()` handler 末尾添加 `analyzed_in` 写入逻辑 | 生成分析后，对应实验的 `analyzed_in` 字段包含该分析 ID |
| 3.11 | 在 `view.html` 中展示 `analyzed_in` 关联链接 | 实验详情页可看到"被以下分析引用" |
| 3.12 | 在 `analysis_detail.html` 中展示关联实验卡片 | 分析详情页可看到参与分析的实验列表 |

---

### P4：聊天窗口 analyze 线程适配 + 分析报告浏览页完善（预估 ~80 行）

**目标**：确保 `new.html` 的 Agent 聊天面板能正确处理 analyze 线程的所有消息类型；确保 `analyze.html` 的分析历史浏览功能完整。

**背景**：analyze 线程不在独立页面运行——用户在 `new.html` 的聊天窗口中说"分析钙钛矿实验"，Agent 自动开启 analyze 线程，选择卡片、追问、分析结果全部在同一个聊天窗口中流转。`analyze.html` 只负责展示已完成的分析报告列表，不承载对话。

**涉及文件**：
- `templates/new.html` — 确保消息渲染逻辑覆盖 analyze 线程的新增消息类型
- `templates/analyze.html` — 分析历史浏览器（可能仅需微调）
- `templates/analysis_detail.html` — 分析报告详情页（可能仅需微调）

**具体步骤**：

| 步骤 | 内容 | 验证 |
|------|------|------|
| 4.1 | 在 `new.html` 的消息渲染中，确保 `select_experiments` 的 tool result（`display: "selector"`）正确委托给 P1 的选择卡片组件 | analyze 线程中选择卡片正常渲染 |
| 4.2 | 在 `new.html` 的消息渲染中，确保 `generate_analysis` 的 tool result（`display: "analysis_done"`）以摘要卡片形式展示（标题 + 摘要 + "查看详情"链接跳转到 `analysis_detail.html`） | 分析生成后聊天流中显示摘要卡片 |
| 4.3 | 在 `new.html` 的消息渲染中，确保 `ask_user` 的 question list 在 analyze 模式下的样式与 record 模式一致 | 追问列表正确渲染 |
| 4.4 | 确认 analyze 线程的 thread_begin / thread_end 系统消息不产生多余的 UI 渲染（仅作为内部标记） | 聊天流中不显示线程起止的内部标记 |
| 4.5 | 检查 `analyze.html`：分析历史列表是否从 `/api/analysis-history` 正确加载，点击是否跳转到 `analysis_detail.html` | 历史列表正确展示，跳转正常 |
| 4.6 | 检查 `analysis_detail.html`：分析报告的三区域格式（事实呈现/发现提示/值得思考的问题）在 Markdown 渲染中是否可读 | 三区域通过 `---` 或 `###` 分隔，视觉清晰 |
| 4.7 | 移动端：确认 `new.html` 聊天面板中的选择卡片、子对话框在窄屏下可用 | 同 P1 移动端验证 |

---

## 七、验证清单

每个 Phase 完成后，按以下场景手动验证：

| 场景 | 预期行为 | 覆盖 Phase |
|------|---------|-----------|
| 用户输入"分析钙钛矿PCE趋势" | Agent 自动进入 analyze 模式 → search → select_experiments | P4 |
| 选择卡片显示 15 个候选 | 主列表显示 8 条 + "查看更多 (共 15 个)" 按钮 | P1 |
| 点击"查看更多" | 弹出子对话框，搜索框可用，滚轮可滚动全部 15 条 | P1 |
| 勾选 3 个实验 → 确认 | 卡片变形为"已选择 3 个实验: ..."，Agent 继续循环 | P1 |
| 勾选后点取消 | 卡片变形为"已取消选择"，Agent 收到取消通知 | P1 |
| 选择卡片渲染后刷新页面 | 卡片显示 expired 状态，不锁输入 | P1 |
| analyze 线程中 LLM 尝试调 modify_experiment | 工具不可用，LLM 收到错误 | P2 |
| 生成分析报告 | 报告含"事实呈现""发现提示""值得思考的问题"三区域 | P2 |
| 查看分析报告详情页 → 点"对话" | 子 Agent 面板打开，可对话 | P3 |
| 子 Agent 中"把退火温度也纳入分析" | `modify_analysis(additional_query=...)` 更新报告，页面刷新 | P3 |
| 子 Agent 中"加上 EXP-008" | `modify_analysis(additional_refs=["EXP-008"])` 重新分析，页面刷新 | P3 |
| 刷新分析详情页 → 重新打开子 Agent | 对话历史从 child_state.yaml 恢复 | P3 |
| 打开旧分析记录（无线程）的子 Agent | 懒迁移 → 横幅提示 → 正常对话 | P3 |
| 分析生成后查看参与实验的详情页 | `analyzed_in` 字段包含分析 ID 链接 | P3 |
| 手机端在 `new.html` 聊天中进行 analyze 线程 | 选择卡片、子对话框在窄屏下可用 | P1+P4 |
| 请求旧 `/api/analyze` POST | 返回 405 | P3 |

---

## 八、多轮分析（后续详细设计）

本次设计不覆盖多轮分析的完整交互流程，但已通过以下机制为其奠定基础：

- 分析子 Agent + `modify_analysis` 提供了"已有报告 → 迭代修改"的通道
- 线程文件持久化保证了跨会话的对话连续性
- `analyzed_in` 反向关联使多次分析之间的关系可追溯

后续设计中需要细化：多轮分析的线程复用策略、分析版本历史、实验集变更后的差异对比。

---

*本文档 v3 基于审查反馈修订。主要变更：移除旧 `/api/analyze` 表单路径统一为对话流、移除 `TOOL_ANALYZE` 工具、选择卡片增加"查看更多"子对话框、确认/取消后卡片变形并继续 Agent 循环、三区域 prompt 格式、移除 analyze 模式 modify_experiment、modify_analysis 升为必须、`_child_agent_role` 角色覆盖（含完整工具清单和 `_build_thread_status` 覆盖）、analyzed_in 反向关联、样式抽离为部分模板、移动端适配、明确页面职责（聊天在 new.html，报告浏览在 analyze.html）。*
