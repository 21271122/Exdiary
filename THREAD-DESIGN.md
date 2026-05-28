# Exdiary 对话线程系统 — 设计文档

## 一、背景

### 1.1 当前问题

Exdiary 的 Agent 对话系统（`agent_v2.py`）以单次 HTTP 请求为粒度运行：每次 `POST /api/agent/message` 通过 `from_dict()` 恢复状态，`run()` 处理后通过 `state_to_dict()` 返回新状态。前端将状态保存在 `sessionStorage` 中，实现跨请求的连续性。

但这只在**同一浏览器会话内**有效。以下场景全部失效：

1. **刷新页面**：`sessionStorage` 被清除，对话丢失。
2. **关闭再打开**：前一次实验记录对话完全不可恢复。
3. **长期记忆为零**：每次新对话都是空白，Agent 不知道库里有哪些实验、用户常用什么参数、最近做了什么。
4. **多入口修改同一实验**：用户在子 agent、父 agent、手动编辑三处入口修改同一实验时，数据冲突和记忆不同步。

### 1.2 目标

不改变 Agent v2 的核心工作流（tool-calling 循环），在其外围增加**线程持久化、分级记忆、更新日志**三层基础设施，实现：

- 用户始终面对同一个人格（统一 AgentLoop），所有对话在同一个聊天窗口中
- 对话持久化到磁盘，刷新/重启不丢失
- 线程是 AgentLoop 内部自动管理的区间标记，**非显式 API 资源**——用户无需感知线程的存在
- 多入口修改实验数据有冲突检测和合并策略
- 所有修改记录可审计的更新日志，解决记忆不同步问题

### 1.3 与原始 THREAD-DESIGN 的根本差异

```
┌──────────┬──────────────────────────────┬─────────────────────────────────────┐
│          │        原始设计               │              新设计                  │
├──────────┼──────────────────────────────┼─────────────────────────────────────┤
│ 线程是   │ 显式 API 资源                 │ AgentLoop 内部自动标记               │
│ 前端感知 │ 需要知道 thread_id            │ 不需要（子 Agent 入口除外）           │
│ 路由     │ /api/thread/* + /api/agent/*  │ /api/agent/* + /api/exp/<id>/chat   │
│ 线程开始 │ 用户调用 /api/thread/start    │ Agent 检测到记录/分析意图时自动标记    │
│ 线程结束 │ generate_record 成功时        │ generate_record 成功时自动标记        │
│ 取消线程 │ 无此机制                      │ Agent 感知意图变更 → 移除标记         │
└──────────┴──────────────────────────────┴─────────────────────────────────────┘
```

---

## 二、核心概念

### 2.1 统一 AgentLoop

整个用户会话中**只有一个 AgentLoop 实例**（跨 HTTP 请求通过 `state_to_dict` / `from_dict` 恢复）。

AgentLoop 持有一份 `self.history`——一个持续增长的对话消息列表。所有对话（跨 topic、跨线程）都在这份 history 中。

线程不是独立的 Agent 实例。线程是**AgentLoop.history 中由 system 标记消息界定的命名区间**。

### 2.2 线程（Thread）

一条线程 = 统一对话中一段**有单一产出物**的对话区间。

- 线程 ID：`THR-YYYY-NNN`
- 线程类型（由其产出决定）：`record`（产出 EXP） | `analyze`（产出 ANAL）
- 线程是**内部概念**——AgentLoop 检测到产出意图时自动注入边界标记，用户无需感知线程的开始和结束
- **任何时候全局最多一个 active 线程**。新线程开始时，旧 active 自动标记为 done
- 线程无 `paused` 状态——用户随时切走，`_current_state.yaml` 自然保存当前状态，回来时从保存点继续

**什么不创建线程**：查询实验参数、收藏/置顶、修改简单字段、闲聊——这些是对话中的自然操作，不产生持久化线程。

### 2.3 入口模型与子 Agent

用户有两种方式与 Agent 交互：

**主聊天框**（统一 AgentLoop）：
- 位置：`new.html` 对话模式
- 处理一切：记录实验、分析、查询、修改、管理、闲聊
- 当发生有产出的对话时，AgentLoop 在后台注入线程边界标记，用户无感知
- 线程结束后，用户仍在同一聊天窗口继续对话，和线程开始前一样

**实验详情页的子 Agent**（线程续接）：
- 位置：`view.html` 中"与 Agent 对话修改"按钮 → 弹出聊天面板
- 用途：对已完成的实验记录进行补充/修改
- 子 Agent 是一个独立的 AgentLoop 实例，从线程文件加载该实验的完整对话上下文

子 Agent 的设计有两种方案（见 3.5），当前推荐方案 A。

### 2.4 三层记忆

```
L0: 全局摘要（始终注入 Agent，~500 tokens，Python 确定性生成）
    内容: 实验库概况 + 最近线程摘要（含产出物）+ 用户画像 + 近期修改的实验
    生成时机: AgentLoop.__init__ 时生成；from_dict() 时如果距上次生成超过 1 小时则重新生成（应对对话进行期间的外部变更——手动编辑、子 Agent 修改等）；线程 done 时重新生成（实验数量/标签计数变了）

L1: 统一对话历史（AgentLoop.history）
    运行时维护滑动窗口（最近 ~30 轮完整对话 + 压缩历史摘要）
    线程边界通过 system 标记消息界定
    线程的自有 messages 存储在线程文件中，续接时按需加载

L2: 按需加载
    - load_reference → 加载完整实验数据（含最近更新日志摘要）
    - search_parent_history → 在父 Agent 全量 history 中语义检索（仅子 agent 可选工具）
    - read_update_log → 读取实验的完整修改历史
```

---

## 三、详细设计

### 3.1 数据模型

#### 3.1.1 物理存储布局

```
experiments/_threads/
  THR-YYYY-NNN.yaml             ← 线程文件（元数据 + 自包含的完整 messages）
  index.yaml                    ← 线程索引 + 用户画像 + active 标记
  _global_context.yaml          ← 压缩历史摘要（Agent 启动时注入 L1）
  _current_state.yaml           ← 父 AgentLoop 运行时状态（滑动窗口）
  _pending_merges/              ← 待合并队列（子 Agent 完成时写入，父 Agent 恢复时消费）
    THR-YYYY-NNN.yaml
  {thread_id}_child_state.yaml  ← 子 Agent 运行时状态（子 Agent 完成时删除）

experiments/_update_logs/
  EXP-YYYY-NNN.yaml             ← 每个实验的更新日志
```

#### 3.1.2 线程文件（`experiments/_threads/THR-YYYY-NNN.yaml`）

```yaml
id: THR-2026-001
type: record                         # record | analyze
status: done                         # active | done
created: "2026-05-22 20:32:00"
updated: "2026-05-22 20:35:00"
title: "复刻EXP-003钙钛矿实验（ETL替换为富勒烯）"
experiment_type: perovskite-solar    # record 专属
exp_generated: "EXP-2026-015"        # record 专属。产出物被删除时改为 "[已删除] EXP-2026-015"

# analyze 专属
# anal_generated: "ANAL-2026-002"
# selected_exps: ["EXP-003", "EXP-012"]

# 线程完整 messages（含边界标记消息。永不压缩，供子 Agent 续接使用）
messages:
  - role: system
    content: "[线程 THR-2026-001 开始] type=record"
  - role: user
    content: "记录新实验，复刻003钙钛矿，ETL改富勒烯"
  - role: assistant
    content: "已加载 EXP-003 数据。还需要补充实验日期和实验者。"
    tool_calls: [...]
  - role: user
    content: "5月22，刘启繁，生成"
  - role: assistant
    content: "generate_record → EXP-015"
    tool_calls: [...]
  - role: system
    content: "[线程 THR-2026-001 结束] → EXP-2026-015"

# 子 Agent 续接分支（方案 A 下追加，方案 B 下独立记录）
branches:
  - id: THR-2026-001-b1
    created: "2026-05-25 14:20:00"
    summary: "修改退火温度 150→200°C，ETL 厚度 120→200nm"
    messages: [...]                    # 子 Agent 的完整对话

# 摘要（线程结束时确定性生成，不调 LLM）
summary: "复刻EXP-003钙钛矿实验，ETL替换为富勒烯，生成EXP-015。4轮对话。"
```

`messages` 字段**永远是创建时的原始对话**。`branches` 字段记录所有后续续接。原始记录不可变。

#### 3.1.3 线程索引（`experiments/_threads/index.yaml`）

```yaml
active_thread: "THR-2026-003"    # 全局唯一 active 线程，无则为 null

threads:
  - id: THR-2026-001
    type: record
    status: done
    title: "复刻EXP-003钙钛矿实验（ETL替换为富勒烯）"
    summary: "生成EXP-015"
    exp_generated: "EXP-2026-015"
    created: "2026-05-22"
    updated: "2026-05-22"

# 反向映射（线程 done 时写入，O(1) 查找，供子 Agent 入口反查 thread_id）
exp_to_thread:
  EXP-2026-015: THR-2026-001
anal_to_thread:
  ANAL-2026-002: THR-2026-003

user_profile:
  experimenter_counts:              # 计数取最频繁
    刘启繁: 5
    张三: 2
  default_experimenter: "刘启繁"
  tag_counts:                       # 全库实验 tags 归并计数，top 10
    perovskite-solar: 5
    thin-film: 4
    spin-coating: 3
    hydrothermal: 2
  frequent_tags: ["perovskite-solar", "thin-film", "spin-coating", ...]
  last_updated: "2026-05-22"
```

实验者画像在每次 record 线程 done 时更新（从产出 EXP 的 experimenter 字段提取，非空值才计数）。

tag_counts 在每次 record 线程 done 时全量重算（实验量 <1000 时成本可忽略）。暂不做衰减。

#### 3.1.4 全局上下文（`experiments/_threads/_global_context.yaml`）

```yaml
compressed: |
  此前已完成:
  · THR-001→EXP-015：复刻003钙钛矿实验，ETL替换为富勒烯
  · 查询 EXP-003 退火温度（未记录）
  · 批量修改 4 个钙钛矿实验 status→done
  · THR-002→EXP-016：水热合成ZnO纳米棒，200°C

uncompressed_thread_ids: ["THR-003"]  # 当前活跃线程，其 messages 不压缩
recently_modified_exps: ["EXP-015"]   # 最近被修改过的实验
last_compressed_at: "2026-05-25 14:30:00"
```

#### 3.1.5 运行时状态（`experiments/_threads/_current_state.yaml`）

```yaml
# AgentLoop.state_to_dict() 的完整输出
# 包含: L0 摘要 + 压缩摘要 + 最近 30 轮完整 messages + context + references
# 每次 HTTP 请求结束时写入，下次请求通过 from_dict() 恢复
# 此文件不需要备份（丢了就重新初始化，前端 sessionStorage 中的 state 为备份）
```

#### 3.1.6 子 Agent 状态（`experiments/_threads/{thread_id}_child_state.yaml`）

```yaml
# 子 Agent 的 AgentLoop.state_to_dict() 输出
# 每次子 Agent run() return 时写入
# 子 Agent 完成（generate_record + 用户确认）时删除
# 子 Agent 刷新页面 → 重新打开 EXP 详情页 → POST /api/exp/<id>/chat → 从此文件恢复
```

#### 3.1.7 待合并队列（`experiments/_threads/_pending_merges/THR-YYYY-NNN.yaml`）

```yaml
# 子 Agent 完成时写入。父 Agent 下次 from_dict() 时消费并删除。
target: "parent_history"
messages:                          # 数组形式，当前场景只有一个元素
  - role: system
    content: "EXP-015 已被修改（更新: UPD-015-001）。此前关于 EXP-015 的对话陈述可能已过时。获取当前数据请使用 load_reference。"
thread_id: "THR-2026-001"
completed_at: "2026-05-25 15:00:00"
```

#### 3.1.8 更新日志（`experiments/_update_logs/EXP-YYYY-NNN.yaml`）

```yaml
experiment_id: EXP-2026-015
entries:
  - id: UPD-015-001
    timestamp: "2026-05-22 20:35:00"
    source: child_agent                # child_agent | parent_agent | manual_edit | system
    thread_id: THR-2026-001            # 修改发生的对话上下文（parent_agent 修改时取当前 AgentLoop.thread_id；无则为 null）
    context:
      summary: "用户在子 agent 中修改退火温度和 ETL 厚度"
      conversation:                    # 产生此修改的对话（2-4 条消息，仅 agent 来源有值）
        - role: user
          content: "退火温度改成 200°C，ETL 厚度 200nm"
        - role: assistant
          content: "已更新。正在重新生成记录..."
    changes:
      - path: process_parameters[0].setpoint
        field: 退火温度
        old: "150°C"
        new: "200°C"
```

`source` 取值：
- `child_agent` — 子 Agent 的 generate_record
- `parent_agent` — 父 Agent 的 modify_experiment 工具
- `manual_edit` — EXP 详情页手动编辑保存 或 YAML 编辑保存
- `system` — 系统自动操作（如产出物被删除时标记）

`thread_id`：
- 子 Agent 的修改 → 子 Agent 的 thread_id
- 父 Agent 的修改 → 当前 AgentLoop 的 thread_id（如果有 active 线程，即使用户修改的不是本线程的产出物——因为"这件事是在这个对话上下文中发生的"）；无则为 null
- 手动编辑 → null

#### 3.1.9 messages 写入时序

```
对话进行中:
  消息只追加到 AgentLoop.history（内存）
  每轮结束 → _current_state.yaml 保存整份 history（含滑动窗口 + 压缩摘要）

线程开始时（首次 update_schema / analyze 的同一轮）:
  在 run() 返回前通过 _maybe_inject_thread_start() 注入 [线程 THR-xxx 开始] 标记（flag 延迟机制）
  紧接着注入模式引导消息（见 3.10 节"模式引导消息"）

线程 done 时:
  1. 注入 [线程 THR-xxx 结束] → EXP-xxx 标记
  2. 提取两个边界标记间的 messages → 一次性写入线程文件的 messages 字段
  3. 生成 title（≥3 轮时调 flash 模型）
  4. 更新用户画像 + L0 摘要
  5. 线程文件之后不再修改（除非子 Agent 续接，续接写入 branches 字段）

线程取消时:
  移除已注入的 [线程开始] 标记 → 不写入线程文件 → 这段对话保留在 history 中作为普通历史

_current_state.yaml 是 AgentLoop.history 的完整快照。
线程文件 messages 是 AgentLoop.history 的真子集（仅含该线程的对话区间）。
两者不是重复——线程文件是"这一段的归档"，_current_state.yaml 是"整本书的草稿"。
```

### 3.2 新增存储类

#### UpdateLogStore（`lib/storage.py`）

```python
class UpdateLogStore:
    """实验更新日志持久化"""

    def __init__(self, path: str):
        """path: experiments/_update_logs/"""

    def append(self, exp_id: str, source: str, changes: list[dict],
               context: dict | None = None, thread_id: str | None = None) -> str:
        """追加一条更新日志，返回 entry_id。
           old 值从磁盘读取 EXP 文件的当前状态获取（调用前由外部执行读盘）。"""

    def list_recent(self, exp_id: str, limit: int = 5) -> list[dict]

    def list_all(self, exp_id: str) -> list[dict]

    def get_entry(self, exp_id: str, entry_id: str) -> dict | None
```

#### ThreadStore（`lib/storage.py`）

```python
class ThreadStore:
    """线程持久化存储"""

    def __init__(self, path: str):
        """path: experiments/_threads/"""

    # -- 线程 CRUD --
    def create(self, thread_type: str, messages: list[dict]) -> dict
    def save(self, thread_data: dict) -> None
    def load(self, thread_id: str) -> dict | None

    # -- 索引管理 --
    def get_index(self) -> dict
    def update_index(self, thread_data: dict) -> None
    def get_active_thread(self) -> dict | None
    def set_active_thread(self, thread_id: str | None) -> None
    def list_recent(self, n: int = 5) -> list[dict]

    # -- 全局摘要（L0） --
    def build_global_summary(self, experiment_store, update_log_store) -> str:
        """Python 确定性生成 L0 摘要。用于 AgentLoop.__init__ 注入。

        输出模板（每项为空时省略该行）：

        当前实验库共 {total} 条实验（已完成: {done}, 进行中: {running}, 失败: {failed}）。
        最近完成: {recent_threads}    ← 格式"THR-001→EXP-015 钙钛矿, THR-002→EXP-016 ZnO水热"
        你的常用标签: {frequent_tags}
        近期被修改的实验: {recently_modified}
        """

    # -- 全局上下文（压缩历史摘要） --
    def get_global_context(self) -> str
    def update_global_context(self, completed_threads: list[dict]) -> None

    # -- 运行时状态 --
    def save_current_state(self, agent_state: dict) -> None
    def load_current_state(self) -> dict | None

    # -- 子 Agent 状态 --
    def save_child_state(self, thread_id: str, agent_state: dict) -> None
    def load_child_state(self, thread_id: str) -> dict | None
    def delete_child_state(self, thread_id: str) -> None

    # -- 待合并队列 --
    def enqueue_merge(self, merge_data: dict) -> None
    def dequeue_all_merges(self) -> list[dict]:
        """扫描 _pending_merges/ → 返回所有待合并消息 → 删除文件。
           由父 Agent 的 from_dict() 在恢复时调用。"""

    # -- 用户画像 --
    def update_user_profile(self, context: dict) -> None

    def get_user_profile(self) -> dict
```

### 3.3 线程边界自动标记

线程由 AgentLoop 内部自动管理，不通过 API 触发。核心机制：**flag 延迟注入**——在 tool handler 内设置 flag，在 `run()` 的 while 循环顶部和 return 前检查并注入标记。

#### 3.3.1 标记开始

触发信号：

1. **首次 update_schema 调用**：Agent 开始向 Schema 写入字段 → 意味着进入了记录状态
2. **首次 analyze 调用**（且当前不在 record 线程内）→ 意味着进入了分析状态。在 record 线程内调用 analyze 不启动新线程

注入机制（flag 延迟，避免在 tool handler 内部操作 history）：

```python
# ToolExecutor._update_schema() 中：
if loop.thread_id is None and loop._pending_thread_start is None:
    loop._pending_thread_start = "record"

# ToolExecutor._analyze() 中（仅在非 record 线程内调用时触发新线程）：
if loop.thread_id is None and loop._pending_thread_start is None:
    loop._pending_thread_start = "analyze"

# AgentLoop.run() 在 while 循环顶部和每个 return 前调用：
def _maybe_inject_thread_start(self):
    if not self._pending_thread_start:
        return
    thread_type = self._pending_thread_start
    thread_id = self.thread_store.next_id()
    self.thread_id = thread_id
    # 注入 thread begin 标记（位于本轮 user 消息之后）
    begin_marker = {"role": "system",
                    "content": f"[线程 {thread_id} 开始] type={thread_type}"}
    self.history.insert(self._current_turn_user_idx + 1, begin_marker)
    # 紧接着注入模式引导消息
    guidance = self._build_thread_guidance(thread_type)
    self.history.insert(self._current_turn_user_idx + 2, guidance)
    self._pending_thread_start = None
```

模式引导消息（注入在 begin 标记之后）：

- record：`[system] 你正在记录一条新实验。优先收集材料、步骤、参数、结果。追问缺失的关键字段。目标：generate_record。`
- analyze：`[system] 你正在进行跨实验分析。深入讨论，多轮对话是预期的。使用 search_experiments 定位相关实验，load_reference 加载数据，然后基于数据推理。目标：输出分析报告。`

注入位置：本轮 user 消息之后。由于在循环顶部先检查一次（后续 LLM 调用看到标记），在每个 return 前再检查一次（返回给前端的 state 含有标记）。`_pending_thread_start` 在第一次注入后被清除，后续调用是空操作。

#### 3.3.2 标记结束并提取

generate_record 或 analyze（不在 record 线程内时）成功执行后：

1. 在 history 中注入 `[线程 THR-xxx 结束] → EXP-xxx` 标记
2. 提取 `[线程开始]` 和 `[线程结束]` 两个边界标记之间的 messages → 写入线程文件
3. 更新 index.yaml 的 `exp_to_thread` 或 `anal_to_thread` 反向映射
4. 生成 title（策略见 3.4）
5. 更新用户画像（record 线程）
6. 更新 L0 摘要

#### 3.3.3 取消线程

取消分两条路径：

**用户明确取消**（"算了""不记了""先不说这个了"）：

System prompt 中增加指引：
```
如果用户明确表示不想继续当前操作（"算了""不记了""取消""先不说这个了"），
用自然语言确认（如"好的，已取消这次的记录"），不要调用 update_schema 或 generate_record。
系统会自动处理清理。
```

LLM 输出纯文本后，`run()` 返回前检测：如果 `self.thread_id` 非空且本轮未调用 `update_schema`/`generate_record`，且上一轮也是同样情况 → 执行取消：移除 `[线程开始]` 标记、清空 `self.thread_id`。

**自动超时取消**：

连续 N 轮（默认 3 轮）在线程内但没有 `update_schema` 或 `analyze` 进展 → Python 端在 `run()` 返回前自动执行取消。不通知用户（用户本来就不知道线程存在）。被取消的线程不创建线程文件，对话保留在 history 中作为普通历史。

#### 3.3.4 _current_state.yaml 恢复时的合并

父 Agent 的 `from_dict()` 在恢复时调用 `ThreadStore.dequeue_all_merges()`，扫描 `_pending_merges/` → 逐一注入 `messages` 数组中的所有消息到 history → 删除文件 → 写入 `_current_state.yaml`。

这里没有并发写入风险——`from_dict()` 在请求处理开始时执行，此时没有其他请求在处理这个 AgentLoop。

### 3.4 线程标题生成

| 时机 | 方法 | 示例 |
|------|------|------|
| 线程 <3 轮就 done | 截取首条用户消息前 30 字 | `"记录新实验，复刻003钙钛矿，ETL改富勒烯"` |
| 线程 ≥3 轮、done 时 | 在 generate_record handler 内调 flash 模型生成 | `"复刻EXP-003钙钛矿实验（ETL替换为富勒烯）"` |

LLM 生成 prompt（temperature=0.1，输出 ~50 tokens）：
```
基于以下对话，为本次实验记录生成一个不超过20字的标题。只返回标题文本，不要引号。
对话首条: "{first_user_message}"
对话最后一条用户消息: "{last_user_message}"
```

调用位置：`generate_record` handler 内（AgentLoop 持有 LLM 引用，可直接调用）。增加延迟约 1-2 秒，但仅在 ≥3 轮线程完成时发生，频率低。

### 3.5 子 Agent 设计（两种方案）

子 Agent 用于"用户从 EXP 详情页进入，修改/补充已完成的实验记录"场景。

#### 3.5.1 共同部分

- 子 Agent 是一个独立的 AgentLoop 实例
- 前端入口：EXP 详情页（`view.html`）→ 按钮"与 Agent 对话修改"→ 弹出聊天面板
- 子 Agent 可调用的工具：全部现有工具 + `read_update_log`
- 请求间连续性：`_threads/{thread_id}_child_state.yaml`（刷新不丢失）
- 子 Agent 内部追踪 `modified_values: dict[str, any]`——记录 `update_schema` 首次触及每个字段时的旧值快照
- **旧实验兼容**：如果 EXP 在线程系统部署前创建（`exp_to_thread` 中无映射），`POST /api/exp/<id>/chat` 创建子 AgentLoop 时初始上下文只含 L0 摘要 + 当前 EXP 的结构化数据（`_summarize_exp` 输出）。前端渲染提示"此实验在对话系统上线前创建，没有历史对话记录。你仍可以与 Agent 对话修改它"。用户确认后正常工作，生成的修改写更新日志但不关联线程。此类修改的 `thread_id` 为 null

写入合并策略：
1. 子 Agent 创建时 → `modified_values` = {}（空）
2. 每次 `update_schema` → 对被修改的每个字段，如果不在 `modified_values` 中，快照 context 旧值写入 `modified_values`
3. `generate_record` 时：
   a. 从磁盘读取 EXP 当前状态 → `disk_values`
   b. 对 `modified_values` 中的每个字段：对比 `disk_values[field]` 和 context 最终值 → 生成 diff
   c. 对不在 `modified_values` 中的字段：保留 `disk_values` 的值（保护父 Agent / 手动编辑的并发修改）
   d. 写更新日志 → `store.save()`
4. 子 Agent 完成时：
   - 更新 EXP 文件 + 更新日志
   - 写入 `_pending_merges/THR-xxx.yaml`
   - 删除 `_threads/{thread_id}_child_state.yaml`
   - 更新线程文件的 `branches` 字段

#### 3.5.2 方案 A：连续体模型（推荐）

**设计理念**：用户始终面对同一个人。子 Agent 是这个人**专注处理某个实验**的状态。

子 Agent 初始上下文：
- L0 全局摘要
- 线程文件的完整 messages（含边界标记）
- **不自动注入**线程前/后的父 Agent 对话

可用工具：全部工具 + `search_parent_history`（方案 A 专属）

`search_parent_history`：
- 子 Agent 调用时 → 在父 Agent 的 `_current_state.yaml` history 中语义搜索
- 内部为一次轻量 LLM 调用（flash 模型），同步阻塞
- 只在用户使用模糊跨线程指代（"上次说的""后来讨论的"）且本线程找不到时触发——低频操作
- **降级为可选工具**：Phase 5 先跳过，等实际使用确认需求后再实现

#### 3.5.3 方案 B：平行宇宙/分支模型

**设计理念**：从历史产出物进入的是**当时对话的快照**。修改在一个独立分支中进行。

子 Agent 初始上下文：
- L0 全局摘要
- 线程创建时的世界快照（Python 确定性生成：实验库状态 + 引用实验的快照）
- 线程文件的完整 messages

可用工具：全部工具（不含 `search_parent_history`）

#### 3.5.4 方案对比

| 维度 | 方案 A（连续体） | 方案 B（平行宇宙） |
|------|---------------|-----------------|
| 用户感觉 | 始终同一个人 | 回到历史快照 |
| 跨线程指代 | 支持（可选工具） | 不支持 |
| 初始上下文体积 | 低 | 低 + 世界快照 |
| 新增工具 | `search_parent_history`（可选） | 无 |
| 推荐度 | **推荐** | 备选 |

### 3.6 L0 摘要

#### 生成模板

```
当前实验库共 {total} 条实验（已完成: {done}, 进行中: {running}, 失败: {failed}）。
最近完成: THR-001→EXP-015 复刻003钙钛矿实验, THR-002→EXP-016 水热合成ZnO纳米棒
你的常用标签: perovskite-solar(5), thin-film(4), spin-coating(3)
近期被修改的实验: EXP-015（退火温度、ETL厚度）
```

每项为空时省略该行。"最近完成"一行同时涵盖"最近实验"和"最近完成的对话"（避免信息重复）。

#### 生成时机

| 时机 | 行为 |
|------|------|
| AgentLoop.__init__（首次使用） | 全新生成 |
| AgentLoop.from_dict() 恢复 | 如果距上次生成超过 1 小时 → 重新生成（应对对话进行期间的外部变更——手动编辑、子 Agent 修改等）；否则保持原样 |
| 线程 done | 重新生成（实验数量/标签计数/线程列表变了） |

生成函数 `build_global_summary()` 是纯 Python 确定性函数，耗时 < 1ms。

### 3.7 更新日志与 diff 机制

#### old 值获取规则

**所有入口统一规则：在修改操作执行前，从磁盘读取 EXP 文件的当前状态作为 old 值来源。**

| 入口 | 读取时机 |
|------|---------|
| 子 Agent generate_record | store.save() 之前立即读盘 |
| 父 Agent modify_experiment | 修改逻辑执行之前立即读盘 |
| 手动编辑保存 | store.update() 之前立即读盘 |

永远不从 Agent 记忆或前端数据中取 old 值。磁盘是 truth。

#### 更新日志写入时机

| 修改入口 | source 字段 | context |
|---------|------------|---------|
| 子 Agent generate_record | child_agent | 产生此修改的对话轮次 |
| 父 Agent modify_experiment | parent_agent | 产生此修改的对话轮次 |
| EXP 详情页手动编辑保存 | manual_edit | 仅 summary |
| YAML 编辑保存 | manual_edit | 仅 summary |
| 产出物被删除 | system | "实验记录 EXP-xxx 已被删除" |

#### old 值一致性问题

子 Agent 的场景：`modified_values` 中记录的是"子 Agent 创建时 context 的快照"，而磁盘当前值可能在子 Agent 进行期间被手动编辑改过。此时以**磁盘当前值**为准生成 diff——因为 diff 要回答"子 Agent 做了什么"，不应该包含中间发生的、与子 Agent 无关的变更。

```
子 Agent modified_values: {"退火温度": "150°C"}    ← 创建时的快照
磁盘当前值: 退火温度 = "180°C"                       ← 被手动编辑改过
子 Agent context 最终值: 退火温度 = "200°C"          ← 子 Agent 修改的目标

diff:
  old: "180°C"  ← 磁盘当前值（不是 modified_values 中的 150°C）
  new: "200°C"  ← 子 Agent 最终值
```

### 3.8 记忆同步机制（四层防御）

#### 第一层：事实优先 System Prompt

Agent system prompt 中植入：

```
## 事实获取规则

对话历史中关于实验参数的陈述可能是过时的（实验可能被子 Agent 或手动编辑修改过）。
当回答关于某个实验的具体数据时，遵循以下优先级：

1. 如果对话中出现了 [EXP-xxx 已被修改] 的标记 → 必须调用 load_reference 重新加载
2. 如果你本轮刚通过 modify_experiment 自己修改了该实验 → 可以信任自己的操作
3. 其他情况 → 优先从 load_reference 的结果中获取，而非依赖对话记忆

回答数据性问题时注明来源：
  "EXP-015 当前退火温度是 200°C（已从文件确认）"
```

#### 第二层：过期标记注入

子 Agent 完成或 modify_experiment 执行后，注入：

```
[system] EXP-015 已被修改（更新: UPD-015-001）。
         此前关于 EXP-015 字段值的对话陈述可能已过时。
         获取当前数据请使用 load_reference。
```

只标注"已过期"，不列新旧值——避免 LLM 困惑于该信哪个。

#### 第三层：load_reference 增强

`_summarize_exp` 返回中追加 `_recent_updates`（最近 3 条更新日志摘要）。

#### 第四层：read_update_log 工具

Agent 在需要更多细节时可主动调用。

### 3.9 压缩策略

当 AgentLoop.history 超过 30 轮时触发压缩。Python 确定性执行，不调 LLM。

**压缩规则**：
- 已完成线程 → 替换为"THR-xxx→EXP-xxx：标题（summary 保留关键细节）"
- 无产出对话 → 按操作类型归类
- 当前 active 线程的最近 15 轮 → **永不压缩**
- 当前 active 线程的早期轮次（超过 15 轮历史）→ 仍然压缩，但保留关键决策点（load_reference 结果、用户确认的参数值），压缩掉纯追问/确认的冗余轮次
- 线程文件中的 messages **永不压缩**

**压缩后的结果写入**：
- `_global_context.yaml`：更新 `compressed` 字段
- `_current_state.yaml`：更新 history 为压缩后的版本

### 3.10 工具集

#### 现有工具（保持不变）

`load_reference`, `search_experiments`, `update_schema`, `ask_user`, `generate_record`

#### 新增工具

**read_update_log**：

```json
{
  "name": "read_update_log",
  "description": "读取某个实验的更新日志。",
  "parameters": {
    "exp_id": { "type": "string" },
    "since": { "type": "string", "description": "可选" },
    "limit": { "type": "integer", "description": "默认 5" }
  }
}
```

**modify_experiment**：

```json
{
  "name": "modify_experiment",
  "description": "修改实验字段。changes 中未出现的字段保持磁盘现有值不变（增量语义）。嵌套数组字段（materials/sop/process_parameters/results.key_data 等）的值是完整的数组替换——LLM 需给出该字段的完整新数组。所有修改自动写入更新日志。",
  "parameters": {
    "refs": { "type": "array", "items": {"type": "string"} },
    "changes": {
      "type": "object",
      "description": "扁平字段名→新值映射。如 {\"experimenter\": \"刘启繁\", \"status\": \"done\", \"process_parameters\": [{\"parameter\": \"退火温度\", \"setpoint\": \"200°C\"}]}"
    },
    "description": {
      "type": "string",
      "description": "自然语言修改描述。与 changes 二选一。"
    }
  }
}
```

`changes` 格式：扁平字段名 → 新值。简单字段（title/date/status/tags/experimenter/purpose/conclusion）覆盖。嵌套数组字段（materials/sop/process_parameters/results.key_data/results.figures 等）完整替换。changes 中未出现的字段 → 保持磁盘现有值不变。

**query_experiment**：

```json
{
  "name": "query_experiment",
  "description": "回答实验参数查询。用户提问模糊时可能需要多轮确认。",
  "parameters": {
    "question": { "type": "string" },
    "refs": { "type": "array", "items": {"type": "string"} }
  }
}
```

Python handler 分两条路径：
1. **目标实验已在当前 messages 中被 load_reference 加载过** → 从 messages 中确定性提取答案，不调 LLM
2. **目标实验未加载** → handler 调 store.load() 获取 YAML → 调一次 flash 模型总结 answer

返回格式：
```json
{
  "status": "ok",
  "display": "answer",
  "question": "003 的退火温度是多少",
  "answer": "EXP-2026-003 的退火温度未记录。其 process_parameters 中记录了银电极厚度 120 nm。",
  "exp_ids": ["EXP-2026-003"],
  "source": "file"
}
```

**analyze**：

```json
{
  "name": "analyze",
  "description": "跨实验分析。在 record 线程中可调用（一键式轻量分析）。在 analyze 线程中不可用——analyze 线程中 Agent 通过 search_experiments + load_reference + 自身推理进行深入多轮讨论。",
  "parameters": {
    "query": { "type": "string" },
    "refs": { "type": "array", "items": {"type": "string"}, "description": "空数组表示按 query 自动筛选" }
  }
}
```

handler 行为：
- 调用 `lib/analyzer.py` 的 `analyze_experiments()`（同步 LLM 请求，可能耗时 5-15 秒。前端应展示专用分析等待状态）
- 无论是否在线程内调用，分析报告同时写入 `AnalysisStore`（`experiments/_analysis_history/`），确保分析历史页面始终可检索到所有分析
- 如果不在任何线程内调用（用户直接说"分析钙钛矿PCE趋势"，Agent 首次调用 analyze）→ `_maybe_inject_thread_start()` 启动 analyze 线程

返回格式：
```json
{
  "display": "report",
  "markdown": "## PCE 趋势\n...",
  "charts": [
    {
      "type": "scatter",
      "title": "PCE vs 退火温度",
      "x_label": "退火温度 (°C)",
      "y_label": "PCE (%)",
      "series": [{
        "label": "富勒烯 ETL",
        "points": [{"x": 150, "y": 15.2, "exp_id": "EXP-015"}]
      }]
    }
  ]
}
```

`charts` 当前仅支持 `scatter`。前端用原生 Canvas 渲染，不引入图表库。

**manage_collection**：

```json
{
  "name": "manage_collection",
  "description": "管理实验的收藏和置顶。",
  "parameters": {
    "action": { "enum": ["pin", "unpin", "favorite", "unfavorite"] },
    "refs": { "type": "array", "items": {"type": "string"} },
    "collection": { "type": "string", "description": "默认'默认收藏夹'" }
  }
}
```

**list_experiments**：

```json
{
  "name": "list_experiments",
  "description": "按条件筛选实验列表。确定性执行，不调 LLM。",
  "parameters": {
    "status": { "type": "string", "enum": ["planned", "running", "done", "failed", "repeated"] },
    "tags": { "type": "array", "items": {"type": "string"} },
    "experimenter": { "type": "string" },
    "since": { "type": "string", "description": "起始日期 YYYY-MM-DD" }
  }
}
```

**search_parent_history**（方案 A 专属，可选工具，Phase 5 跳过）：

```json
{
  "name": "search_parent_history",
  "description": "在主线对话历史中语义搜索与当前问题相关的内容。",
  "parameters": {
    "query": { "type": "string" }
  }
}
```

#### 模糊引用解析器（`lib/resolver.py`）

抽取为共享函数，被接受 `refs` 参数的工具（`modify_experiment`、`manage_collection`、`query_experiment`）复用：

```python
def resolve_refs(refs: list[str], store, llm) -> dict:
    """
    输入: ["EXP-2026-003", "上次的ZnO实验", "老张的钙钛矿"]
    输出: {"EXP-2026-003": {"status": "loaded", "data": {...}},
            "上次的ZnO实验": {"status": "ambiguous", "candidates": [...]}}

    规则:
    1. EXP ID 格式 → 直接加载，标记 loaded
    2. 非 EXP 格式 → 本地关键词搜索（复用 _fuzzy_search 逻辑）
       - 得分 ≥0.8 且唯一 → 自动解析
       - 候选多/得分低 → 标记 ambiguous，返回 candidates 让 LLM 向用户确认
    3. 本地匹配差 → 调 LLM 语义搜索（复用 _llm_semantic_search 逻辑）
    """
```

`load_reference` 保持不变（严格只接受 EXP ID 格式）。

### 3.11 产出物删除语义

**线程不可删除**。线程是对话的永久记录。不提供线程删除 API。

**产出物（EXP/ANAL）被删除时**：
- 线程文件保留，`exp_generated` / `anal_generated` 改为 `"[已删除] EXP-2026-015"`
- 线程仍出现在索引中，仍可查看对话
- 更新日志追加 `source: system`

**产出物被删除后的子 Agent 续接**：
- 子 Agent 仍可打开（线程 messages 保留）
- `generate_record` 时因目标 EXP 不存在 → 提示"该实验已被删除，是否作为新实验重新生成？"
- 用户确认 → 新 EXP ID → 线程标记 `restored_from: THR-xxx`

### 3.12 保存频率

```
触发事件                       → 写入什么                         延迟
────────────────────────────────────────────────────────────────────────
每轮 run() return              → _current_state.yaml             毫秒级
线程 create                    → 线程文件（初始 messages+标记）    仅一次
线程 done (generate_record)    → 线程文件（最终 messages+标记）    仅一次
线程 done                      → L0 摘要更新（_current_state 更新）仅一次
用户画像变更                   → index.yaml                       每次 record done
压缩触发 (>30轮)               → _global_context.yaml             每 15-20 轮
                                + _current_state.yaml
子 Agent 每轮 return           → {thread_id}_child_state.yaml     每轮
子 Agent done                  → EXP 文件 + 更新日志 +            仅一次
                                _pending_merges + 线程 branches
                               → 删除 child_state.yaml
modify_experiment 完成         → 更新日志 + EXP 文件              每次调用
手动编辑保存                   → 更新日志 + EXP 文件              每次保存
```

### 3.13 AgentLoop 改动

#### __init__ 注入 L0 摘要和全局上下文

```python
def __init__(self, llm_client, experiment_store,
             thread_store=None, update_log_store=None, debug_dir=None):
    ...
    self.thread_store = thread_store
    self.update_log_store = update_log_store
    self.thread_id = None                    # 当前 active 线程 ID
    self._pending_thread_start = None        # "record" 或 "analyze"（待分配 ID），或 None
    self._current_turn_user_idx = -1         # 本轮 user 消息在 history 中的索引
    self.modified_values = {}                # {field: old_value_before_first_touch}

    if thread_store:
        l0 = thread_store.build_global_summary(experiment_store, update_log_store)
        self.history.append({
            "role": "system", "content": f"[全局上下文]\n{l0}"
        })
        global_ctx = thread_store.get_global_context()
        if global_ctx:
            self.history.append({
                "role": "system", "content": f"[历史摘要]\n{global_ctx}"
            })
```

#### run() 中集成 flag 检查

```python
def run(self, user_message=""):
    if user_message:
        self._current_turn_user_idx = len(self.history)
        self.history.append({"role": "user", "content": user_message})
        self.turn_count += 1

    consecutive_no_progress = 0

    while True:
        self._maybe_inject_thread_start()   # 循环顶部检查

        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *self.history]
        response = llm.chat(messages, tools, ...)
        ...

        if 纯文本回复:
            self._maybe_inject_thread_start()  # return 前检查
            self._check_thread_cancellation(consecutive_no_progress)
            return {"type": "reply", ...}
        if ask_user:
            self._maybe_inject_thread_start()
            self._check_thread_cancellation(consecutive_no_progress)
            return {"type": "reply", ...}
        if generate_record:
            self._maybe_inject_thread_start()
            self._maybe_inject_thread_end(exp_id)
            return {"type": "generate", ...}

def _maybe_inject_thread_start(self):
    if not self._pending_thread_start:
        return
    thread_id = self.thread_store.next_id()
    self.thread_id = thread_id
    self.thread_store.set_active_thread(thread_id)
    begin = {"role": "system", "content": f"[线程 {thread_id} 开始] type=record"}
    self.history.insert(self._current_turn_user_idx + 1, begin)
    guidance = self._build_thread_guidance()
    self.history.insert(self._current_turn_user_idx + 2, guidance)
    self._pending_thread_start = None

def _maybe_inject_thread_end(self, produced_id):
    if not self.thread_id:
        return
    end = {"role": "system", "content": f"[线程 {self.thread_id} 结束] → {produced_id}"}
    self.history.append(end)
    self._extract_and_save_thread(produced_id)
    self.thread_store.set_active_thread(None)
    self.thread_id = None
```

#### from_dict 消费待合并队列

```python
@classmethod
def from_dict(cls, llm_client, store, data, thread_store=None, update_log_store=None):
    loop = cls(llm_client, store, thread_store=thread_store,
               update_log_store=update_log_store, ...)
    loop.context = data.get("context", ...)
    loop.history = data.get("history", [])
    ...

    # 消费待合并队列
    if thread_store:
        merges = thread_store.dequeue_all_merges()
        for m in merges:
            for msg in m["messages"]:
                loop.history.append(msg)

    # L0 摘要超过 1 小时 → 重新生成
    if loop._l0_stale():
        loop._refresh_l0()

    return loop
```

#### state_to_dict 扩展

```python
def state_to_dict(self) -> dict:
    return {
        ...
        "thread_id": self.thread_id,
        "_pending_thread_start": self._pending_thread_start,
        "_current_turn_user_idx": self._current_turn_user_idx,
        "modified_values": dict(self.modified_values),
    }
```

### 3.14 API 路由

```
─────────────────────────────────────────────────────────────
主聊天框（统一 AgentLoop）：
─────────────────────────────────────────────────────────────
POST /api/agent/start
  初始化 Agent：
  - 如有 _current_state.yaml → from_dict() 恢复，返回 {state, greeting: null}
  - 如无 → 创建新 AgentLoop → run("") → 返回 {state, greeting: "你好！我是你的实验记录助手..."}
  不存在"新会话 vs 旧会话"的区分——只有一个连续对话。greeting 只在真正首次使用时才生成。

POST /api/agent/message
  发送消息。{message, state} → Agent 处理 → {reply, state, type?}
  后端在 run() 内部自动管理线程标记。
  state 参数保留：正常工作流中前端 sessionStorage 为主路径（避免每次读盘），
  _current_state.yaml 为服务端备份（sessionStorage 丢失时恢复）。

─────────────────────────────────────────────────────────────
EXP 详情页（子 Agent）：
─────────────────────────────────────────────────────────────
POST /api/exp/<exp_id>/chat
  子 Agent 入口。{message} → 子 Agent 处理 → {reply, preview?}
  后端从 index.yaml 的 exp_to_thread 映射反查 thread_id（O(1)）。
  - 如 exp_to_thread 中存在且 {thread_id}_child_state.yaml 存在 → 恢复子 Agent
  - 如 exp_to_thread 中存在但无 child_state.yaml → 从线程文件 messages 创建新子 Agent
  - 如 exp_to_thread 中不存在（旧实验，线程系统部署前创建）：
    返回 {is_legacy: true, exp_data: _summarize_exp(...)}
    → 前端渲染提示后用户确认 → 再次 POST body 中带 {message, is_legacy: true}
    → 创建子 AgentLoop（初始上下文: L0 + EXP 数据，无线程关联）
  子 Agent 状态写 {thread_id}_child_state.yaml（刷新可恢复）。

─────────────────────────────────────────────────────────────
只读接口：
─────────────────────────────────────────────────────────────
GET /api/threads
  返回线程列表（从 index.yaml 读取）。

GET /api/threads/<thread_id>
  加载线程完整数据（含 messages）。

GET /api/updates/<exp_id>
  返回实验的更新日志。

─────────────────────────────────────────────────────────────
废弃：
─────────────────────────────────────────────────────────────
/api/analyze 端点          → 不再提供。analyze 通过 Agent 的 analyze 工具调用。
/analyze 独立页面           → 不再提供。分析入口统一到对话窗口。
```

注：API 端点名称不变。`/api/agent/start` 和 `/api/agent/message` 后端行为增强——集成 `_current_state.yaml` 持久化、线程自动标记、待合并队列消费。无"新旧路由"之分。

---

## 四、不变量约束

**保持不变**：
- `agent_v2.py` 的 `run()` tool-calling 主循环逻辑（while True 结构）
- 现有 5 个工具的定义和行为（新增工具以追加方式加入）
- `_build_schema_status` / `_core_fields_filled` / 所有调试日志方法
- 前端 `new.html` 的双模式（对话/Quill）布局
- YAML 文件存储体系（不迁移到数据库）
- API 端点名称不变（`/api/agent/start`、`/api/agent/message`），后端行为增强

**删除或废弃**：
- 显式线程 API（`/api/thread/*`）
- `/analyze` 独立页面 + `/api/analyze` 端点
- `paused` 线程状态
- 临时 AgentLoop 概念
- `GET /api/agent/status` 端点（不存在"新会话 vs 旧会话"的区分）

---

## 五、实施计划

### 总览

| Phase | 内容 | 预估代码量 | 依赖 | 验证方式 |
|-------|------|-----------|------|---------|
| 1 | UpdateLogStore | ~180 行 | 无 | 单元测试 |
| 2 | ThreadStore | ~400 行 | Phase 1（弱依赖） | 单元测试 |
| 3 | AgentLoop 改造 | ~300 行 | Phase 2 | 集成测试 + 真实对话 |
| 4 | 子 Agent + 路由 | ~300 行 | Phase 3 | 集成测试 |
| 5 | 工具扩展 | ~550 行 | Phase 3 | 单元测试 + Mock 对话 |
| 6 | 前端 | ~900 行 | Phase 4 + Phase 5 | 手动交互测试 |
| 7 | 压缩 + 生命周期 | ~150 行 | Phase 3 | 单元测试 |

**总预估**：~2,780 行（后端 ~1,730 行 + 前端 ~900 行 + 测试 ~150 行）


### Phase 1: UpdateLogStore（~180 行）

**文件**：`lib/storage.py` 新增 `UpdateLogStore`
**依赖**：无
**新增依赖**：无（纯 Python 标准库 + yaml）

| 步骤 | 内容 | 产出 | 验证 |
|------|------|------|------|
| 1.1 | `UpdateLogStore` 类骨架 | `__init__`、`next_id()`、路径创建 | `python -c "from lib.storage import UpdateLogStore; s = UpdateLogStore('/tmp/test'); assert s.path.exists()"` |
| 1.2 | `append(exp_id, source, changes, context, thread_id)` | YAML 追加一条 entry，自动生成 `UPD-xxx-NNN` ID | 追加后读盘验证 YAML 结构 |
| 1.3 | `list_recent(exp_id, limit)` | 按时间倒序返回最近 N 条 | 追加 5 条 → list_recent(3) 返回 3 条 |
| 1.4 | `list_all(exp_id)` / `get_entry(exp_id, entry_id)` | 全量列表 + 单条查询 | 边界：exp 无日志 → 返回空 / None |
| 1.5 | 集成到手动编辑保存路径 | `POST /experiments/<id>/save-json` 和 `/edit` POST 中，在 `store.update()` 之前读磁盘旧值、计算 diff、调 `append()` | 手动编辑一个字段 → 更新日志出现对应 entry |
| 1.6 | 处理产出物被删除时的 system 日志 | `DELETE /experiments/<id>/delete` 中追加 `source: system` 日志 | 删除实验 → 更新日志记录删除事件 |


### Phase 2: ThreadStore（~400 行）

**文件**：`lib/storage.py` 新增 `ThreadStore`
**依赖**：Phase 1（弱——L0 摘要中"近期修改的实验"行在 UpdateLogStore 未就绪时省略）

| 步骤 | 内容 | 产出 | 验证 |
|------|------|------|------|
| 2.1 | 目录初始化 + `next_id()` | `experiments/_threads/` 目录结构 + `THR-YYYY-NNN` 编号 | 目录自动创建，ID 递增正确 |
| 2.2 | 线程文件读写 | `create(thread_type, messages)` → 写入 YAML（含边界标记 + metadata）；`load(thread_id)` → 返回完整线程 dict | 创建 → 读回 → 字段一致 |
| 2.3 | `index.yaml` 读写 | `get_index()` / `update_index(thread_data)` —— 读写线程索引列表 | 创建线程 → index 中新增条目 |
| 2.4 | active 线程管理 | `get_active_thread()` / `set_active_thread(thread_id)` —— 全局唯一 active 约束 | set A → get 返回 A；set B → A 自动 done → get 返回 B |
| 2.5 | `exp_to_thread` / `anal_to_thread` 反向映射 | 线程 done 时写入映射；子 Agent 入口 O(1) 反查 | 给定 exp_id → 找到 thread_id |
| 2.6 | `_global_context.yaml` 读写 | `get_global_context()` / `update_global_context(compressed_text)` | 压缩后的文本正确读写 |
| 2.7 | `_current_state.yaml` 读写 | `save_current_state(state_dict)` / `load_current_state()` —— 父 Agent 运行时状态的完整序列化 | state_to_dict → save → load → from_dict → 字段一致 |
| 2.8 | 子 Agent 状态读写 | `save_child_state(thread_id, state)` / `load_child_state(thread_id)` → dict / `delete_child_state(thread_id)` | 写入 → 读回 → 删除 → 读回 None |
| 2.9 | 待合并队列入队/出队 | `enqueue_merge(merge_data)` 写入 `_pending_merges/`；`dequeue_all_merges()` 返回所有待合并消息的 `messages` 数组并删除文件 | 入队 2 个 → 出队返回 2 个 → 文件已删除 |
| 2.10 | `build_global_summary()` L0 生成 | Python 确定性拼接模板（实验库概况 + 最近线程 + 常用标签 + 近期修改）——每项为空时省略该行 | 给定 mock ExperimentStore + UpdateLogStore → 输出匹配模板格式 |
| 2.11 | 用户画像更新 | `update_user_profile(context)` 从 EXP experimenter/tags 更新计数；`get_user_profile()` 返回画像 dict | record 线程 done → experimenter_counts 递增；tag_counts 全量重算 |
| 2.12 | 单元测试 | 覆盖所有方法的正常路径 + 边界（空索引、空线程、active 冲突） | `python -m pytest tests/test_thread_store.py` |


### Phase 3: AgentLoop 改造（~300 行）

**文件**：`lib/agent_v2.py`
**依赖**：Phase 2

| 步骤 | 内容 | 产出 | 验证 |
|------|------|------|------|
| 3.1 | `__init__` 注入 L0 + 全局上下文 | 构造函数接受 `thread_store`/`update_log_store` 参数；注入 L0 摘要为 `history[0]`（system 消息）；注入压缩历史摘要为 `history[1]` | 创建 AgentLoop → history 前两条为 system 消息 |
| 3.2 | `from_dict` 消费待合并队列 | 恢复后调用 `dequeue_all_merges()` → 逐一注入 `messages` 数组到 history → 写入 `_current_state.yaml` | Mock pending merge 文件 → from_dict → history 含注入消息 → 文件已删除 |
| 3.3 | L0 过期检测 + 刷新 | `_l0_stale()` 检查距上次生成是否超过 1 小时；`_refresh_l0()` 重新生成并替换 `history[0]` | 修改系统时间 → from_dict → L0 被刷新 |
| 3.4 | `state_to_dict` / `from_dict` 扩展 | 持久化新增字段：`thread_id`、`_pending_thread_start`、`_current_turn_user_idx`、`modified_values` | 序列化 → 反序列化 → 字段值一致 |
| 3.5 | `_pending_thread_start` flag 机制 | `ToolExecutor._update_schema()` 和 `_analyze()` 中设置 flag（`"record"` 或 `"analyze"`） | 首次 update_schema → `_pending_thread_start == "record"` |
| 3.6 | `_maybe_inject_thread_start()` | 在 `run()` 的 while 循环顶部和每个 return 前调用；分配 thread_id → 注入 `[线程开始]` 标记 → 注入模式引导消息 → 清除 flag | 真实对话：用户开始记录 → history 中出现 begin 标记 + 引导消息 |
| 3.7 | `_build_thread_guidance(thread_type)` | 按 type 返回对应引导消息文本（record / analyze） | record → "你正在记录一条新实验..."；analyze → "你正在进行跨实验分析..." |
| 3.8 | `_maybe_inject_thread_end(produced_id)` | generate_record 成功 / analyze 成功（不在 record 线程内）后注入结束标记 → 调 `_extract_and_save_thread()` → 清空 `self.thread_id` | 线程 done → history 含结束标记 → 线程文件存在 |
| 3.9 | `_extract_and_save_thread(produced_id)` | 提取 begin-end 标记间的 messages → 写入线程文件 → 更新反向映射 → 生成 title（≥3 轮调 flash 模型）→ 更新用户画像 + L0 | 线程文件 messages 完整（含边界标记） |
| 3.10 | `_check_thread_cancellation()` | 检测用户明确取消（"算了"→ LLM 不调记录工具 + Python 端检测连续两轮无进展）→ 移除 begin 标记、清空 thread_id；自动超时取消（连续 3 轮无 update_schema/analyze 进展） | 用户说"算了不记了" → Agent 回复确认 → 下一轮检查 → 线程取消 |
| 3.11 | `modified_values` 追踪 | `_update_schema()` handler 中：对被修改的每个字段，如果在 `modified_values` 中不存在 → 快照 context 旧值写入；子 Agent `generate_record` 时读磁盘对比生成 diff（不在 Phase 3 实现，仅预留字典） | update_schema 修改 → modified_values 含首次旧值 |
| 3.12 | `_summarize_exp` 追加 `_recent_updates` | 在返回的 loaded 数据中追加最近 3 条更新日志摘要 | load_reference → 结果含 `_recent_updates` 字段 |
| 3.13 | System Prompt 更新 | 增加取消指引 + 事实获取规则（四层防御的第一层） | 新 prompt 包含"如果用户明确表示不想继续当前操作"相关指引 |
| 3.14 | 集成测试 | 真实 API 对话：完整 record 流程（开始→追问→生成）+ 取消流程 + analyze 流程 | `python test_agent_v2.py` 全部通过 |


### Phase 4: 子 Agent + 路由（~300 行）

**文件**：`lib/agent_v2.py` + `app.py`
**依赖**：Phase 3

| 步骤 | 内容 | 产出 | 验证 |
|------|------|------|------|
| 4.1 | `create_child_agent(parent_loop, thread_id)` | 从线程文件加载完整 messages → 创建子 AgentLoop 实例（含 L0 + 线程上下文）；子 Agent `self.modified_values = {}` | 创建子 Agent → history 含线程 messages |
| 4.2 | 子 Agent `generate_record` 合并写入逻辑 | 读磁盘 EXP 当前值 → 对比 `modified_values` 中的字段 → 生成 diff → 写更新日志 → `store.save()`；保护未修改字段（保留磁盘值） | 子 Agent 只改退火温度 → EXP 文件中其他字段不变 |
| 4.3 | 子 Agent 完成时的清理 | 写 `_pending_merges/` → 更新线程文件 `branches` → 删除 `child_state.yaml` | 子 Agent done → merge 文件存在 → child_state 已删除 |
| 4.4 | `POST /api/exp/<id>/chat` 路由 | 接收 `{message, state?}` → 从 `exp_to_thread` 反查 thread_id → 恢复或创建子 Agent → `run()` → 返回 `{reply/type, state, preview?}` | curl POST → 收到子 Agent 回复 |
| 4.5 | 旧实验兼容分支 | `exp_to_thread` 中无映射 → 返回 `{is_legacy: true, exp_data: _summarize_exp(...)}`；前端确认后再次 POST 带 `{message, is_legacy: true}` → 创建无线程关联的子 Agent | 对 EXP-2026-001（无线程）调子 Agent → legacy 提示 → 确认后正常工作 |
| 4.6 | 子 Agent 状态持久化 | 每次 `run()` return → 写 `{thread_id}_child_state.yaml`；刷新重新打开 → 恢复 | 子 Agent 对话到第 3 轮 → 刷新 → 重新打开 → 前 3 轮消息还在 |
| 4.7 | `POST /api/agent/start` 集成 `_current_state.yaml` | 有文件 → `from_dict()` 恢复 → `greeting: null`；无文件 → 全新 → `greeting` 有值 | 首次访问 → 问候语；刷新 → 无问候语但历史消息保留 |
| 4.8 | 集成测试 | 完整子 Agent 流程：创建 → 对话 → 修改 → 确认 → 父 Agent 下一轮收到过期标记 | 手动测试全部路径 |


### Phase 5: 工具扩展（~550 行）

**文件**：`lib/agent_v2.py` + 新增 `lib/resolver.py`
**依赖**：Phase 3（不依赖 Phase 4。Phase 5 增加工具到 ToolExecutor.registry，Phase 4 修改 generate_record/update_schema handler——两者改 `agent_v2.py` 的不同区域）

| 步骤 | 内容 | 产出 | 验证 |
|------|------|------|------|
| 5.1 | `lib/resolver.py`：`resolve_refs()` | 输入混合 refs 列表 → 规则：① EXP ID 格式 → 直接加载 ② 非 EXP 格式 → 本地关键词搜索 → 得分 ≥0.8 且唯一 → 自动解析 ③ 多候选/低分 → 返回 ambiguous + candidates | `resolve_refs(["EXP-003", "上次的ZnO"])` → 两个结果分属 loaded/ambiguous |
| 5.2 | `read_update_log` 工具 | 工具 schema + handler：调 `UpdateLogStore.list_recent()` → 返回更新条目列表 | Mock 更新日志 → LLM 调 read_update_log → 返回条目 |
| 5.3 | `modify_experiment` 工具 | 工具 schema（`refs` + `changes` 扁平字段→新值映射 + 可选 `description`）+ handler：`resolve_refs` → 读磁盘旧值 → 应用 changes（完整替换数组字段）→ 写更新日志 + EXP 文件 → 注入过期标记到 history | 修改退火温度 → EXP 文件更新 + 更新日志写入 + history 含过期标记 |
| 5.4 | `manage_collection` 工具 | 工具 schema（`action` + `refs` + 可选 `collection`）+ handler：调 `FavoritesStore` 对应方法 | pin/unpin → 置顶列表变化；favorite/unfavorite → 收藏夹变化 |
| 5.5 | `query_experiment` 工具 | 工具 schema（`question` + `refs`）+ handler：两条路径——① 目标在 messages 中已加载 → 确定性从 messages 提取 ② 未加载 → `store.load()` + 调 flash 模型总结 → 返回 `{display: "answer", ...}` | 查 EXP-003 退火温度 → answer 卡片出现在消息流 |
| 5.6 | `analyze` 工具 | 工具 schema（`query` + `refs`）+ handler：调 `analyze_experiments()` → 同时写入 `AnalysisStore` → 返回 `{display: "report", markdown, charts}`；不在 record 线程内 → 设置 `_pending_thread_start = "analyze"`；handler 内部检测当前线程类型：analyze 线程内调用 → 返回 error | record 线程调 analyze → 报告卡片；analyze 线程调 analyze → 错误提示 |
| 5.7 | `list_experiments` 工具 | 工具 schema（`status`/`tags`/`experimenter`/`since` 可选筛选）+ handler：确定性筛选 → 返回 `{display: "list", experiments, count}` | 按 status=done 筛选 → 返回 done 实验列表卡片 |
| 5.8 | `search_parent_history` 工具 | 仅定义 schema，handler 留空返回 `{status: "not_implemented"}`（降级为可选，Phase 5 跳过实现） | 定义存在但不阻塞 |
| 5.9 | System Prompt 全面更新 | 集成现有 5 工具 + 新增 6 工具的完整指引（各工具用途、调用时机、参数说明）；analyze 工具可用性规则（record 线程内可用，analyze 线程内不可用）；事实获取规则 + 取消指引 | 新 prompt 全部工具均有描述 |
| 5.10 | **输出：display 字段 Schema 规范** | 完整 JSON 合同文档（6 种 display 类型：diff / answer / report / selector / toast / list）——这是 Phase 6 前端渲染的接口合同 | 前端实现者可按此文档独立开发 |


### Phase 6: 前端（~900 行）

**文件**：`templates/view.html` + `templates/new.html` + `templates/base.html`（少量）
**依赖**：Phase 4（路由就绪）+ Phase 5（display schema 合同）

| 步骤 | 内容 | 产出 | 验证 |
|------|------|------|------|
| **6.1 主聊天框增强** | | | |
| 6.1.1 | `renderNewMessages()` 增量渲染 | 对比 `_renderedHistoryLen` 和 `state.history.length`，只追加新消息到 DOM（避免重复渲染已展示的消息） | 发 3 轮消息 → 刷新 → 从 state 恢复 → 消息不重复 |
| 6.1.2 | `showAnalyzing()` 分析等待状态 | 检测最新 tool_call 为 `analyze` → 展示 `#chat-analyzing`（spinner + "正在分析你选中的 N 个实验...可能需要 5-15 秒"）→ 收到 tool result 后隐藏 | 调 analyze → 等待状态出现 → 报告返回后消失 |
| 6.1.3 | Schema 状态条渲染 | `[Schema状态]` system 消息 → 内联进度条（`已填充 11/16 ████████░░░░░░░░`） | 对话中每次 update_schema → 状态条更新 |
| **6.2 Tool 结果渲染器** | | | |
| 6.2.1 | `renderToolResult(result)` 分发函数 | 根据 `result.display` 字段分发到对应子组件 | 各 display 类型正确路由 |
| 6.2.2 | `renderDiffCard(result)` | HTML：变更项列表（field + old→new）+ [撤销]/[确认] 按钮；CSS：删除线旧值、绿色新值、左主色边框 | modify_experiment 返回 diff → 卡片正确渲染 |
| 6.2.3 | `renderAnswerCard(result)` | HTML：Q 行 + A 行 + 来源标注 + EXP 跳转链接 | query_experiment 返回 answer → 答案卡片渲染 |
| 6.2.4 | `renderReportPanel(result)` | Markdown → `marked.parse()` 渲染；Canvas 散点图渲染（原生 Canvas 无依赖） | analyze 返回 report → 报告 + 图表正确展示 |
| 6.2.5 | `renderScatterChart(canvas, chartData)` | 原生 Canvas：坐标轴 + 多系列散点 + 图例 + 点击散点跳转 EXP 详情 | 散点位置正确，点击跳转正常 |
| 6.2.6 | `renderSelectorCard(result)` | HTML：多选实验列表（checkbox + id + title + date + tags）+ [确认选择] 按钮；用户勾选确认 → 选中 ID 作为 tool_result 回传 | 选择 2 个实验 → 确认 → Agent 收到 selected_ids |
| 6.2.7 | `renderExperimentListCard(result)` | HTML：复用 `.experiment-card` 样式的实验列表 + 总数 | list_experiments 返回 → 列表正确渲染 |
| 6.2.8 | `showToast(message, duration)` | 固定定位顶部居中滑动出现，2 秒后淡出 | manage_collection 成功后 → toast 提示 |
| **6.3 子 Agent 模态面板** | | | |
| 6.3.1 | "与 Agent 对话修改" 按钮 | `view.html` EXP 详情页操作栏新增按钮 | 按钮可见，点击打开模态 |
| 6.3.2 | 模态面板 HTML + CSS | 520px 宽 × 70vh 高、聊天区域 flex:1 overflow-y、输入区域底部固定 | 模态正确弹出，布局不溢出 |
| 6.3.3 | `openChildAgent()` / `sendChildMessage()` | 调 `POST /api/exp/<id>/chat` → 渲染回复；支持 state 持久化（子 Agent 独立 sessionStorage key） | 打开 → 对话 → 关闭 → 重新打开 → 对话恢复 |
| 6.3.4 | 旧实验兼容提示 | `is_legacy: true` → 展示警告 → 用户确认 → 重新 POST 带 `is_legacy: true` | 对旧实验打开子 Agent → 警告出现 → 确认后工作 |
| 6.3.5 | `showChildPreview(data)` | 复用预览面板组件，确认按钮文案为"确认修改"，保存后刷新详情页而非跳转 | 子 Agent generate_record → 预览 → 确认 → 详情页刷新 |
| **6.4 更新日志 UI** | | | |
| 6.4.1 | 更新日志折叠区 | `view.html` 详情页底部 `<details>` 折叠区，按时间倒序列出条目 | 折叠区正确展示所有更新条目 |
| 6.4.2 | `source` 彩色徽章 | `child_agent` 蓝、`parent_agent` 绿、`manual_edit` 灰、`system` 橙 | 各 source 对应颜色正确 |
| **6.5 导航调整** | | | |
| 6.5.1 | 导航栏 [分析] → [对话] | `base.html` 顶层导航栏，`/analyze` 入口改为 `/new` | 导航栏显示 [对话]，点击跳转到对话页 |
| **6.6 状态持久化** | | | |
| 6.6.1 | sessionStorage 状态读写 | `saveState()` / `loadState()` 在主聊天框和子 Agent 中各自独立 key | 刷新 → 对话历史保留 |
| **6.7 移动端适配** | | | |
| 6.7.1 | 对话面板 ≤576px 适配 | 气泡宽度自适应、输入区域不遮挡键盘、模态面板全屏 | Chrome DevTools 手机模式各页面无水平溢出 |


### Phase 7: 压缩 + 生命周期（~150 行）

**文件**：`lib/agent_v2.py` + `lib/storage.py`
**依赖**：Phase 3

| 步骤 | 内容 | 产出 | 验证 |
|------|------|------|------|
| 7.1 | 压缩触发检测 | `run()` return 前检查 `len(self.history) > 30` → 触发压缩 | 对话超过 30 轮 → 自动触发 |
| 7.2 | 已完成线程压缩 | 将 `[线程开始]...[线程结束]` 区间替换为"THR-xxx→EXP-xxx：标题"单行摘要（保留 load_reference 结果和用户确认的关键参数值） | 已完成线程在 history 中变为一行 |
| 7.3 | 无产出对话压缩 | 按操作类型归类（查询 / 修改 / 管理 / 闲聊），每类合并为一行摘要 | 查询类对话 → "查询 EXP-003 退火温度（未记录）" |
| 7.4 | active 线程保护 | 当前 active 线程最近 15 轮永不压缩；超过 15 轮的早期轮次压缩但保留关键决策点 | active 线程 20 轮 → 前 5 轮压缩、后 15 轮完整 |
| 7.5 | 压缩结果写入 | `_global_context.yaml` 更新 `compressed` 字段 + `_current_state.yaml` 更新 history | 压缩后重启 → history 为压缩版 |
| 7.6 | 线程取消逻辑实现 | Phase 3 的 `_check_thread_cancellation()` 完整实现（用户明确取消 + 自动超时取消） | 连续 3 轮无 update_schema → 线程自动取消，不创建线程文件 |


### 实施依赖图

```
Phase 1 (UpdateLogStore)
      │
      ▼
Phase 2 (ThreadStore)
      │
      ▼
Phase 3 (AgentLoop 改造)
      │
      ├──────────────────────┐
      ▼                      ▼
Phase 4 (子 Agent + 路由)  Phase 5 (工具扩展)
      │                      │
      └──────────┬───────────┘
                 ▼
           Phase 6 (前端)
                 │
                 ▼
           Phase 7 (压缩)
```

Phase 4 和 Phase 5 **可并行**——两者依赖的都是 Phase 3，且修改 `agent_v2.py` 的不同区域：
- Phase 4 修改 `generate_record` / `update_schema` handler 内部逻辑 + 新增 `create_child_agent()` 函数
- Phase 5 在 `ToolExecutor.registry` 中追加新工具 + 新增 `lib/resolver.py`

如果单人依次实施，建议先 Phase 4 再 Phase 5（Phase 4 改动更底层，完成后 Phase 5 的工具可以直接使用新的 handler 能力）。

---

## 六、关键设计决策

**Q: 为什么线程不是 API 资源而是内部标记？**

因为用户的心智模型是"跟助手对话"，不是"管理线程"。线程作为 API 资源迫使前端维护 thread_id、处理冲突、做线程切换 UI——所有这些复杂度对用户透明时体验更好。AgentLoop 在后台自动管理线程边界，用户只需要说话。

**Q: 为什么 L0 摘要不调 LLM？**

L0 是事实性语境（"库里有 15 条实验"、"上次生成了 EXP-015"），不需要 LLM 的润色。Python 确定性生成保证零 hallucination、零 token 浪费、零延迟。

**Q: 为什么线程文件自包含 messages 而不是只存指针？**

因为子 Agent 续接时需要完整对话，而父 AgentLoop 的 history 可能已被压缩。线程文件自包含保证续接时总是有完整上下文。

**Q: 为什么保留前端传 state？**

正常工作流中前端 sessionStorage 为主路径（避免每次 HTTP 请求读磁盘）。`_current_state.yaml` 作为服务端备份（sessionStorage 丢失时的恢复源）。两者互为备份，不冲突。

**Q: 为什么线程不可删除？**

线程是对话的永久记录。没有典型需求场景会要求删除"讨论的痕迹"。产出物被删除时线程保留，`exp_generated` 标记为 `[已删除]`。

**Q: analyze 功能为什么不再有独立页面？**

独立的 `/analyze` 页面是另一种交互模式（选实验 → 填问题 → 看报告），与统一对话模型不一致——它没有记忆、没有上下文、没有对话连贯性。统一到 analyze 工具后，分析成为对话的一部分：用户可以在分析后追问、展开、修正，Agent 记住分析过程和结论。

**Q: 为什么前端用原生 Canvas 渲染图表而不引入图表库？**

散点图是当前唯一的图表类型（`type: "scatter"`），且需要交互（点击散点跳转 EXP 详情）。原生 Canvas 实现量约 60 行，引入 Chart.js 等库增加约 200KB 依赖但只用到 5% 的功能。未来扩展更多图表类型时再评估引入。

**Q: 为什么子 Agent 不作为一个独立页面而是模态面板？**

用户在 EXP 详情页时，上下文是该实验的结构化数据。模态面板让用户同时看到"当前实验值"和"Agent 对话"，不需要在页面之间切换。关闭面板后自然回到详情页，修改结果立即可见。

---

## 七、前端设计

### 7.1 页面架构

```
┌────────────────────────────────────────────────────────────────┐
│  Exdiary 前端页面地图                                            │
│                                                                │
│  base.html (共享框架)                                           │
│  ├── 顶层固定导航栏: [仪表盘] [收藏] [时间线] [分析→对话] [设置]    │
│  ├── PicoCSS + Quill.js + marked.js + htmx                    │
│  ├── 导航历史系统 (sessionStorage)                               │
│  └── 全局编辑模态框 .edit-modal-overlay                          │
│                                                                │
│  ┌──────────────┬──────────────────┬─────────────────────────┐ │
│  │   页面        │   路由            │   核心组件               │ │
│  ├──────────────┼──────────────────┼─────────────────────────┤ │
│  │ 仪表盘        │ GET /            │ 实验列表+搜索+筛选+对比   │ │
│  │ 对话页 ★      │ GET /new         │ ChatPanel (主聊天框)     │ │
│  │              │                  │ + Quill 自由书写 (保留)   │ │
│  │ 实验详情      │ GET /exp/<id>    │ 结构化展示 + 内联编辑     │ │
│  │              │                  │ + ChildAgentModal ★      │ │
│  │ 收藏夹        │ GET /api/favorites│ 收藏列表 + 管理          │ │
│  │ 时间线        │ GET /timeline    │ 时间顺序卡片列表          │ │
│  │ 对比视图      │ GET /compare     │ 并排对比 (2-4实验)       │ │
│  │ 设置          │ GET /settings    │ API Key + 模型配置       │ │
│  │ 模板库        │ GET /templates   │ 模板卡片列表              │ │
│  │ 打印页        │ GET /exp/<id>/print│ 打印友好样式             │ │
│  │ YAML编辑      │ GET /exp/<id>/edit│ 文本编辑器                │ │
│  └──────────────┴──────────────────┴─────────────────────────┘ │
│                                                                │
│  ★ = 本次新增/重点改造                                           │
│  /analyze 独立页面 → 废弃（分析入口统一到对话页）                    │
└────────────────────────────────────────────────────────────────┘
```

### 7.2 ChatPanel — 主聊天框核心组件

位置：`new.html` 对话模式。处理一切 Agent 交互（记录、分析、查询、管理）。

#### 7.2.1 HTML 结构

```html
<div id="chat-panel">
  <!-- 消息列表 -->
  <div id="chat-messages">
    <!-- 消息动态追加到此 -->
  </div>

  <!-- 分析等待状态（长时间操作专用） -->
  <div id="chat-analyzing" style="display:none">
    <div class="analyzing-spinner"></div>
    <p id="chat-analyzing-text">正在分析...</p>
  </div>

  <!-- 进度指示器（record 模式专用） -->
  <div id="chat-progress" style="display:none">
    <div class="progress-bar">
      <div class="progress-fill" style="width:0%"></div>
    </div>
    <small></small>
  </div>

  <!-- 快捷回复按钮 -->
  <div id="quick-replies"></div>

  <!-- 输入区域 -->
  <div id="chat-input-area">
    <textarea id="chat-input"
              placeholder="输入消息..."
              rows="2"></textarea>
    <button id="btn-send">发送</button>
  </div>
</div>
```

#### 7.2.2 消息渲染

每条消息在 `#chat-messages` 中为一个 `.chat-msg` 元素。消息分为四种：

| 消息类型 | 来源 | CSS class | 渲染方式 |
|---------|------|-----------|---------|
| 用户消息 | `role: "user"` | `.chat-msg.user` | 纯文本（右对齐气泡） |
| Agent 纯文本 | `role: "assistant"` + `content` 有值 | `.chat-msg.agent` | `marked.parse(content)` 渲染 Markdown |
| Tool 结果 | `role: "tool"` | `.chat-msg.tool` | `renderToolResult()` 分发到子组件 |
| Schema 状态 | `role: "system"` + `[Schema状态]` 前缀 | `.chat-msg.system` | 小字灰底摘要条 |

**Agent 消息中如果包含 `tool_calls`**：在 Agent 气泡下方展示一个小型工具调用指示器（如 `🔧 正在加载 EXP-003...`），之后跟随对应的 tool 结果消息。

#### 7.2.3 状态管理

```javascript
var _agentState = null;       // AgentLoop.state_to_dict()
var _isStreaming = false;     // 防止重复提交
var _lastL0Hash = null;       // 用于判断 L0 是否更新（展示 toast）

// 持久化
function saveState() {
  try {
    sessionStorage.setItem('exdiary_agent_state',
      JSON.stringify(_agentState));
  } catch(e) {}
}

function loadState() {
  try {
    var raw = sessionStorage.getItem('exdiary_agent_state');
    return raw ? JSON.parse(raw) : null;
  } catch(e) { return null; }
}

// 渲染历史消息（恢复时调用）
function renderHistory(history) {
  var container = document.getElementById('chat-messages');
  history.forEach(function(m) {
    if (m.role === 'user')       appendUserMessage(m.content);
    else if (m.role === 'tool')  renderToolResult(JSON.parse(m.content));
    else if (m.role === 'system'){
      if (m.content.indexOf('[Schema状态]') === 0) renderSchemaStatus(m.content);
      // 其他 system 消息不渲染（内部标记）
    }
    else if (m.role === 'assistant'){
      if (m.content) appendAgentMessage(m.content);
      if (m.tool_calls) renderToolCallIndicator(m.tool_calls);
    }
  });
}
```

#### 7.2.4 核心交互流程

```javascript
// 1. 初始化
async function initChat() {
  var r = await fetch('/api/agent/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({})
  });
  var data = await r.json();
  _agentState = data.state;
  saveState();

  // 渲染历史消息
  if (_agentState.history && _agentState.history.length > 1) {
    renderHistory(_agentState.history);
  }
  // 如果 greeting 非空（真正首次使用），展示问候语
  if (data.greeting) {
    appendAgentMessage(data.greeting);
  }
}

// 2. 发送消息
async function sendMessage() {
  var input = document.getElementById('chat-input');
  var msg = input.value.trim();
  if (!msg || _isStreaming) return;
  input.value = '';
  appendUserMessage(msg);
  showTyping();
  _isStreaming = true;

  try {
    var r = await fetch('/api/agent/message', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg, state: _agentState})
    });
    var data = await r.json();
    hideTyping();
    _isStreaming = false;

    if (data.ok) {
      _agentState = data.state;
      saveState();

      // 渲染本轮新增的消息（从 history 尾部 diff）
      renderNewMessages(_agentState.history);

      if (data.type === 'extract' || data.type === 'generate') {
        showPreview(data.preview);
      }
    }
  } catch(e) {
    hideTyping();
    _isStreaming = false;
    appendAgentMessage('抱歉，出了点问题。请重试。');
  }
}

// 3. 增量渲染——避免重复渲染已展示的历史消息
var _renderedHistoryLen = 0;
function renderNewMessages(history) {
  for (var i = _renderedHistoryLen; i < history.length; i++) {
    var m = history[i];
    if (m.role === 'assistant' && m.content) appendAgentMessage(m.content);
    if (m.role === 'tool') renderToolResult(JSON.parse(m.content));
    if (m.role === 'system' && m.content.indexOf('[Schema状态]') === 0)
      renderSchemaStatus(m.content);
  }
  _renderedHistoryLen = history.length;
}
```

### 7.3 Tool 结果渲染器

`renderToolResult(toolResult)` 根据 `display` 字段分发：

```javascript
function renderToolResult(result) {
  if (!result || !result.display) return;

  switch(result.display) {
    case 'diff':     return renderDiffCard(result);
    case 'answer':   return renderAnswerCard(result);
    case 'report':   return renderReportPanel(result);
    case 'selector': return renderSelectorCard(result);
    case 'toast':    return showToast(result.message);
    case 'list':     return renderExperimentListCard(result);
    default:         return renderDefaultToolResult(result);
  }
}
```

#### 7.3.1 Diff 卡片

用于 `modify_experiment` 工具返回的变更展示。

```html
<div class="tool-result diff-card">
  <div class="diff-header">✏️ 修改了 EXP-xxx</div>
  <div class="diff-entries">
    <div class="diff-entry">
      <span class="diff-field">退火温度</span>
      <span class="diff-old">150°C</span>
      <span class="diff-arrow">→</span>
      <span class="diff-new">200°C</span>
    </div>
    <!-- 更多变更项... -->
  </div>
  <div class="diff-actions">
    <button class="outline secondary" onclick="undoDiff(this)">撤销</button>
    <button onclick="confirmDiff(this)">确认</button>
  </div>
</div>
```

CSS：旧值灰色删除线、新值绿色加粗。箭头淡色。卡片浅灰背景，左边框主色。

#### 7.3.2 Answer 卡片

用于 `query_experiment` 返回的问答。

```html
<div class="tool-result answer-card">
  <div class="answer-q">Q: 003 的退火温度是多少</div>
  <div class="answer-a">
    EXP-2026-003 的退火温度未记录。其 process_parameters 中...
  </div>
  <div class="answer-source">
    来源: 已从文件确认 ·
    <a href="/experiments/EXP-2026-003">查看 EXP-2026-003 →</a>
  </div>
</div>
```

#### 7.3.3 Report 面板

用于 `analyze` 工具返回的分析报告。

```html
<div class="tool-result report-panel">
  <div class="report-markdown markdown-content">
    <!-- marked.parse(markdown) 渲染 -->
  </div>
  <div class="report-charts">
    <!-- 每个 chart 渲染为 Canvas -->
    <canvas class="chart-canvas" data-chart='{...}'></canvas>
  </div>
</div>
```

Canvas 散点图渲染逻辑（原生 Canvas，不引入图表库）：

```javascript
function renderScatterChart(canvas, chartData) {
  var ctx = canvas.getContext('2d');
  var W = canvas.width, H = canvas.height;
  var pad = {top: 30, right: 20, bottom: 40, left: 50};

  // 计算 x/y 范围
  var allPoints = chartData.series.flatMap(function(s) { return s.points; });
  var xMin = Math.min.apply(null, allPoints.map(function(p) { return p.x; }));
  var xMax = Math.max.apply(null, allPoints.map(function(p) { return p.x; }));
  var yMin = Math.min.apply(null, allPoints.map(function(p) { return p.y; }));
  var yMax = Math.max.apply(null, allPoints.map(function(p) { return p.y; }));

  // 坐标轴
  // ... 绘制轴、刻度、标签

  // 散点 + 可选趋势线
  var colors = ['#2563eb', '#dc2626', '#16a34a', '#9333ea'];
  chartData.series.forEach(function(serie, si) {
    var color = colors[si % colors.length];
    serie.points.forEach(function(pt) {
      var sx = pad.left + (pt.x - xMin) / (xMax - xMin) * (W - pad.left - pad.right);
      var sy = H - pad.bottom - (pt.y - yMin) / (yMax - yMin) * (H - pad.top - pad.bottom);
      // 绘制散点圆
      ctx.beginPath();
      ctx.arc(sx, sy, 5, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      // 点击区域: pt.exp_id → 跳转
      canvas._hitAreas = canvas._hitArees || [];
      canvas._hitAreas.push({x: sx, y: sy, r: 8, exp_id: pt.exp_id});
    });
  });

  // 点击事件
  canvas.addEventListener('click', function(e) {
    var rect = canvas.getBoundingClientRect();
    var mx = e.clientX - rect.left, my = e.clientY - rect.top;
    (canvas._hitAreas || []).forEach(function(h) {
      if (Math.hypot(mx - h.x, my - h.y) < h.r) {
        navigateToPage('/experiments/' + h.exp_id);
      }
    });
  });
}
```

#### 7.3.4 Selector 卡片

用于 LLM 需要用户从候选列表中选择的场景（分析目标实验、确认模糊引用）。

```html
<div class="tool-result selector-card" data-tool-call-id="xxx">
  <div class="selector-title">选择要分析的实验</div>
  <div class="selector-items">
    <label class="selector-item">
      <input type="checkbox" value="EXP-2026-003">
      <span class="selector-item-id">EXP-2026-003</span>
      <span class="selector-item-title">钙钛矿标准工艺</span>
      <span class="selector-item-date">2026-05-01</span>
      <span class="selector-item-tags">
        <span class="tag-pill">perovskite-solar</span>
        <span class="tag-pill">thin-film</span>
      </span>
    </label>
    <!-- 更多候选... -->
  </div>
  <div class="selector-actions">
    <button onclick="confirmSelector(this)">确认选择 (3)</button>
  </div>
</div>
```

关键交互：用户勾选后点击确认 → 选中的 ID 列表作为后续 `POST /api/agent/message` body 中的 `tool_result` 字段回传：

```javascript
function confirmSelector(btn) {
  var card = btn.closest('.selector-card');
  var checked = card.querySelectorAll('input:checked');
  var ids = Array.from(checked).map(function(cb) { return cb.value; });

  // 构造 tool result 回传
  var result = {
    tool_call_id: card.dataset.toolCallId,
    selected_ids: ids,
    status: "selected"
  };

  // 在消息列表中展示用户的选择
  appendSystemMessage('已选择 ' + ids.join(', '));

  // 继续 agent 循环（作为隐式 user 消息发送）
  sendToolResult(result);
}
```

#### 7.3.5 Toast

极轻量提示（置顶成功、收藏成功等），不打断对话流。

```javascript
function showToast(message, duration) {
  duration = duration || 2000;
  var toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = message;
  document.body.appendChild(toast);
  // CSS: position:fixed, top, left:50%, transform:translateX(-50%),
  //      background:#333, color:#fff, padding, border-radius,
  //      animation: slideDown 0.25s ease
  setTimeout(function() {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s';
    setTimeout(function() { toast.remove(); }, 300);
  }, duration);
}
```

#### 7.3.6 Experiment List 卡片

用于 `list_experiments` 工具返回的实验列表。

```html
<div class="tool-result list-card">
  <div class="list-header">找到 {count} 个实验</div>
  <div class="list-items">
    <!-- 复用 index.html 中的 .experiment-card 样式 -->
    <article class="experiment-card" onclick="navigateToPage('/experiments/EXP-xxx')">
      ...
    </article>
  </div>
</div>
```

### 7.4 Schema 状态指示器

`[Schema状态]` system 消息渲染为轻量进度条：

```html
<div class="schema-status">
  <span class="schema-filled">已填充 11/16</span>
  <span class="schema-bar">
    <span class="schema-bar-fill" style="width:68%"></span>
  </span>
</div>
```

同时更新底部的 `#chat-progress` 进度条（详细版，显示百分比 + 轮次）。

### 7.5 分析等待状态

`analyze` 工具调用时前端展示专用等待状态（区别于普通 typing indicator，"正在分析你选中的 N 个实验..."）：

```html
<div id="chat-analyzing">
  <div class="analyzing-spinner"></div>
  <p id="chat-analyzing-text">正在分析...</p>
  <small>这可能需要 5-15 秒</small>
</div>
```

触发：在发送消息后，如果检测到历史消息中最新 tool 调用为 `analyze`，展示此状态，隐藏普通 typing indicator。收到 tool 结果后隐藏。

### 7.6 ChildAgentModal — 子 Agent 聊天面板

位置：`view.html`（EXP 详情页）。

#### 7.6.1 入口

```html
<!-- EXP 详情页操作栏 -->
<button id="btn-child-agent" onclick="openChildAgent()">
  💬 与 Agent 对话修改
</button>
```

#### 7.6.2 模态面板

```html
<div class="edit-modal-overlay" id="child-agent-modal">
  <div class="edit-modal-box child-agent-box">
    <div class="child-agent-header">
      <span>修改 EXP-xxx</span>
      <button class="close-btn" onclick="closeChildAgent()">✕</button>
    </div>

    <!-- 旧实验兼容提示 -->
    <div id="child-legacy-warning" class="flash" style="display:none">
      此实验在对话系统上线前创建，没有历史对话记录。
      你仍可以与 Agent 对话修改它。
      <button onclick="confirmLegacyChild()">知道了，继续</button>
    </div>

    <!-- 聊天区域 -->
    <div id="child-chat-messages"></div>

    <!-- 更新日志折叠区 -->
    <details id="child-update-log" style="display:none">
      <summary>修改历史</summary>
      <div id="child-update-log-content"></div>
    </details>

    <!-- 输入区域 -->
    <div class="child-chat-input-area">
      <textarea id="child-chat-input"
                placeholder="输入修改内容..."
                rows="2"></textarea>
      <button id="btn-child-send">发送</button>
    </div>
  </div>
</div>
```

CSS：模态面板宽 520px，高 70vh，flex column。聊天区域 flex:1 overflow-y:auto。整体样式与主聊天框一致（气泡、颜色）。

#### 7.6.3 JS 交互

```javascript
var _childState = null;
var _childExpId = null;

async function openChildAgent() {
  _childExpId = '{{ exp.id }}';  // Jinja2 注入
  document.getElementById('child-agent-modal').classList.add('active');

  var r = await fetch('/api/exp/' + _childExpId + '/chat', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({message: ''})  // 空消息 → Agent 问候
  });
  var data = await r.json();

  if (data.is_legacy) {
    showLegacyWarning();
  } else {
    _childState = data.state;
    renderChildHistory(_childState.history);
  }
}

async function sendChildMessage() {
  var input = document.getElementById('child-chat-input');
  var msg = input.value.trim();
  if (!msg) return;
  input.value = '';

  appendChildMessage('user', msg);

  var body = {message: msg};
  if (_childState) body.state = _childState;

  var r = await fetch('/api/exp/' + _childExpId + '/chat', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  var data = await r.json();
  _childState = data.state;

  if (data.type === 'extract' || data.type === 'generate') {
    showChildPreview(data.preview);
  } else {
    appendChildMessage('agent', data.reply);
  }

  // 如果有更新日志，刷新折叠区
  if (data.updates) {
    renderUpdateLog(data.updates);
  }
}

function closeChildAgent() {
  document.getElementById('child-agent-modal').classList.remove('active');
  // 如果有变更，提示刷新 EXP 详情页
  if (_childState && _childState.modified_values &&
      Object.keys(_childState.modified_values).length > 0) {
    showToast('实验已更新，刷新页面查看最新数据');
    // 延迟刷新详情页数据
    setTimeout(function() { location.reload(); }, 1500);
  }
}
```

### 7.7 预览面板（复用现有）

`showPreview(data)` 复用 `new.html` 中现有的预览面板。将 `data`（16 字段结构化数据）渲染为可编辑的虚线框卡片。用户修改字段 → 确认 → `POST /api/agent/confirm` → 保存并跳转详情页。

新增适配：预览面板在子 Agent 场景中也复用（`showChildPreview`）。不同之处——子 Agent 的确认按钮文案为"确认修改"，保存后刷新详情页而非跳转。

### 7.8 Quill 自由书写模式（保留不变）

对话模式标签旁边的"自由书写"标签保留。切换到 Quill 时，对话面板隐藏、编辑器显示。用户在 Quill 中写完 → 点击"生成记录"→ `POST /api/parse` → 复用现有预览面板 → 确认保存。

Quill 模式不走 Agent。是独立于对话系统的快速通道。

### 7.9 更新日志查看 UI

`view.html` 详情页底部折叠区：

```html
<details>
  <summary>修改历史 (3)</summary>
  <div class="update-log">
    <div class="update-entry">
      <div class="update-meta">
        <span class="update-source child_agent">子 Agent</span>
        <span class="update-time">2026-05-25 14:20</span>
      </div>
      <div class="update-changes">
        <span class="diff-field">退火温度</span>
        <span class="diff-old">150°C</span> →
        <span class="diff-new">200°C</span>
      </div>
      <div class="update-context">"退火温度改成 200°C，ETL 厚度 200nm"</div>
    </div>
    <!-- 更多条目... -->
  </div>
</details>
```

`source` 字段渲染为不同颜色徽章：`child_agent` 蓝、`parent_agent` 绿、`manual_edit` 灰、`system` 橙。

### 7.10 CSS 架构

```
base.html 共享样式:
├── 顶层导航栏 .topbar
├── 基础排版: 字体/颜色/间距
├── 状态徽章 .status-badge (done/planned/running/failed/repeated)
├── 标签胶囊 .tag-pill
├── 三线表 table.academic-table
├── 编辑模态框 .edit-modal-overlay / .edit-modal-box
├── Quill 编辑器 .ql-editor
└── 暗色模式适配 @media (prefers-color-scheme: dark)

new.html 对话样式:
├── 双模式标签 .mode-tabs
├── ChatPanel 容器 #chat-panel
├── 消息气泡 .chat-msg (.agent / .user)
├── 进度指示器 #chat-progress
├── Tool 结果卡片 (.diff-card / .answer-card / .report-panel /
│                     .selector-card / .list-card)
├── Schema 状态条 .schema-status
├── 分析等待状态 #chat-analyzing
├── Toast 提示 .toast
├── 移动端适配 @media (max-width: 576px)
└── 暗色模式适配

view.html 子 Agent 样式:
├── 子 Agent 模态框 .child-agent-box
├── 旧实验警告 #child-legacy-warning
├── 聊天区域 #child-chat-messages
├── 更新日志 .update-log / .update-entry
└── 暗色模式适配
```

### 7.11 JS 模块结构

```
base.html (共享):
├── 导航历史系统 (sessionStorage)
├── marked.parse() 自动渲染 .markdown-content
├── 全局模态框工具函数
├── navigateToPage() 内部链接拦截
└── 暗色模式切换逻辑

new.html (主聊天框):
├── ChatPanel 类
│   ├── initChat()          — 初始化 + 恢复历史
│   ├── sendMessage()       — 发送 + 增量渲染
│   ├── renderNewMessages() — 增量渲染 diff
│   ├── appendUserMessage()
│   ├── appendAgentMessage()
│   ├── showTyping() / hideTyping()
│   ├── showAnalyzing()     — 分析等待状态
│   └── updateProgress()
├── ToolResultRenderer
│   ├── renderToolResult()  — 分发函数
│   ├── renderDiffCard()
│   ├── renderAnswerCard()
│   ├── renderReportPanel()
│   ├── renderScatterChart()— Canvas 散点图
│   ├── renderSelectorCard()
│   ├── renderExperimentListCard()
│   └── showToast()
├── SchemaStatus 渲染
├── PreviewPanel (复用现有)
├── QuillMode (保留现有)
└── 状态持久化 (sessionStorage)

view.html (实验详情 + 子 Agent):
├── ChildAgentModal
│   ├── openChildAgent()
│   ├── sendChildMessage()
│   ├── closeChildAgent()
│   ├── showLegacyWarning()
│   └── showChildPreview()
├── UpdateLogViewer
│   └── renderUpdateLog()
└── 内联编辑 (保留现有)
```

### 7.12 导航栏调整

顶层导航栏从五个按钮调整为：

```
[Exdiary]  [仪表盘]  [收藏]  [时间线]  [对话]  [设置]
```

点击"对话"→ `navigateToPage('/new')`。原有"分析"入口取消（analyze 功能统一到对话窗口内通过 Agent 工具调用）。

*本文件随实施进展持续更新。每个 Phase 完成后标注完成时间和实际代码量。*
