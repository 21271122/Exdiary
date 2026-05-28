# Exdiary Agent 模式隔离 — 设计文档

## 问题

当前 `AgentLoop` 无条件携带 Schema 相关状态（`context`、`references`、`experiment_type`），System Prompt 无条件包含 16 字段定义和 8 类实验优先级清单，所有 12 个工具在任何模式下均可被 LLM 调用。这导致：

1. **认知噪音**：analyze/自由模式每轮浪费 ~1200 token 无效上下文
2. **预设偏差**：record 导向的指引给 LLM 造成隐性预设偏差
3. **状态残留**：线程取消后 Schema 状态可能残留
4. **工具误用**：非 record 模式下 LLM 可能误调 `update_schema` / `generate_record`

## 设计原则

与线程状态修复相同：**不给 LLM 任何推导的机会，从源头（工具可见性 + Prompt 内容）上就杜绝错误路径。**

## 改动文件

`lib/agent_v2.py` — 唯一改动文件，预估 ~214 行

---

## 一、System Prompt 四段式

将当前单一的 `SYSTEM_PROMPT` 拆为 BASE + 3 个模式追加段。

### 1.1 `SYSTEM_PROMPT_BASE`（所有模式，始终缓存命中）

```
你是 Exdiary 实验记录助手。

## 通用工具

- load_reference: 加载引用实验的完整数据。仅接受 EXP ID。
- search_experiments: 语义搜索历史实验（模糊描述）。
- query_experiment: 回答实验参数查询。
- list_experiments: 按条件筛选实验列表。
- modify_experiment: 修改已存在实验的字段。
- read_update_log: 查看实验的修改历史。
- manage_collection: 管理实验的收藏和置顶。

## 行为准则
- 中文回复，友好、具体
- 不要编造任何用户未提及的信息
- 一次追问不超过3项

## 事实获取规则
（保持不变）

## 矛盾检测
（保持不变）

## 消息格式说明
以 "[系统内部]" 开头的系统消息是框架基础设施日志，不反映你当前的行为模式。
你的当前模式由每轮对话顶部的 "[系统状态]" 消息确定。
```

约 1300 token。

### 1.2 `SYSTEM_PROMPT_RECORD`（record 模式追加）

```
## 当前模式：实验记录

你有以下 record 专用工具：
- start_record_thread: 开启实验记录线程
- update_schema: 将确认的信息写入 Schema。增量更新，只传变化的字段。
- ask_user: 向用户提问，一次最多3个
- generate_record: 生成结构化实验记录草稿
- analyze: 跨实验一键轻量分析（不在 analyze 线程中深入讨论）

工作方式：
1. 用户引用了历史实验 → load_reference（或 search_experiments）
2. 用户提供了信息 → update_schema 写入
3. Schema 状态显示缺失关键字段 → ask_user 追问
4. 关键字段基本齐备 → generate_record
5. 用户说"够了""直接生成" → 判断核心字段已填则 generate_record

## 实验 Schema（16 字段）
（保持不变：16 字段完整定义）

## 各实验类型关键参数优先级
（保持不变：8 类实验 P1/P2/P3）
```

约 800 token。

### 1.3 `SYSTEM_PROMPT_ANALYZE`（analyze 模式追加）

```
## 当前模式：跨实验分析

你正在进行深入讨论。多轮对话是预期的。
使用 search_experiments 定位相关实验，
load_reference 加载数据，
然后基于数据推理。
目标：输出分析报告。

注意：analyze 工具在本模式中不可用——
请不要调用它，直接用自身推理能力进行讨论。
```

约 150 token。

### 1.4 `SYSTEM_PROMPT_GENERAL`（自由模式追加）

```
## 当前模式：自由对话

你可回答查询、管理收藏、闲聊。
用户要记录新实验时 → 调用 start_record_thread。
用户要跨实验分析时 → 调用 analyze 工具。
```

约 80 token。

### 1.5 组装方式

在 `run()` 中动态组装，与 `_build_thread_status()` 并列在请求层：

```python
def _build_system_prompt(self) -> str:
    """按当前模式组装 System Prompt。"""
    base = SYSTEM_PROMPT_BASE
    if not self.thread_id:
        return base + SYSTEM_PROMPT_GENERAL
    if self._thread_type == "record":
        return base + SYSTEM_PROMPT_RECORD
    if self._thread_type == "analyze":
        return base + SYSTEM_PROMPT_ANALYZE
    return base + SYSTEM_PROMPT_GENERAL
```

请求层消息结构：

```python
messages = [
    {"role": "system", "content": SYSTEM_PROMPT_BASE},          # ~1300 token, 始终缓存命中
    {"role": "system", "content": self._build_mode_prompt()},   # ~150-800 token, 模式切换时变化
    {"role": "system", "content": self._build_thread_status()}, # ~30 token, 模式切换时变化
    ...
]
```

缓存效果：模式切换时仅 `messages[1]` 和 `messages[2]` 未命中（~200-830 token），BASE 始终命中。

---

## 二、Schema 状态按模式存在

### 2.1 用 `mode` 属性统一判断

```python
@property
def mode(self) -> str:
    """当前对话模式: 'general' | 'record' | 'analyze'"""
    if not self.thread_id:
        return "general"
    return self._thread_type or "general"
```

### 2.2 Schema dict 仅在 record 模式下存在

**仅提取 `context`（16 字段 dict）**。`references` 和 `experiment_type` 保留在 AgentLoop 上——因为 `load_reference` 在所有模式下都需要去重，属于跨模式能力。

```python
# __init__
self._schema_context = None   # dict | None — 仅 record 模式时非 None
```

进入 record 模式时初始化：

```python
def _enter_record_mode(self):
    self._schema_context = deepcopy(DEFAULT_CONTEXT)
```

退出 record 模式时清理：

```python
def _exit_record_mode(self):
    self._schema_context = None
```

### 2.3 所有读写 `self.context` 的地方改用 `self._schema_context`

涉及位置（全部在 record 专属工具 handler 或 record 专属方法内）：

- `_update_schema` handler：`merge_context(loop._schema_context, fields)` — 加 None 守卫
- `_build_schema_status`：`loop._schema_context.get(key)` — 只能被 `_update_schema` 触发
- `_core_fields_filled`：从 `loop._schema_context` 读取 — 只被 `generate_record` 逻辑调用
- `_build_notes_from_context`：从 `loop._schema_context` 读取 — 只被 `_generate_record` 调用
- `_fallback_preview`：从 `loop._schema_context` 读取
- `run()` 返回值的 `context` 字段：仅在 record 模式返回

**不变的部分**（保留在 AgentLoop 上，跨模式可用）：

- `self.references` — `_load_reference` 在所有模式下去重
- `self.experiment_type` — `_load_reference`/`_update_schema` 从 tags 推断
- `self.modified_values` — `_update_schema` 追踪首次触及的旧值

---

## 三、工具按模式可见

### 3.1 工具分三组

```python
TOOLS_ALL_MODES = [
    TOOL_LOAD_REFERENCE, TOOL_SEARCH_EXPERIMENTS, TOOL_QUERY_EXPERIMENT,
    TOOL_LIST_EXPERIMENTS, TOOL_MANAGE_COLLECTION, TOOL_READ_UPDATE_LOG,
    TOOL_MODIFY_EXPERIMENT,
]

TOOLS_RECORD_ONLY = [
    TOOL_START_RECORD_THREAD, TOOL_UPDATE_SCHEMA,
    TOOL_ASK_USER, TOOL_GENERATE_RECORD, TOOL_ANALYZE,
]
```

`TOOL_ANALYZE` 在 record 模式和 general 模式都可用，但在 analyze 模式不可用。

### 3.2 `_get_active_tools()` 按模式过滤

```python
def _get_active_tools(self) -> list[dict]:
    """返回当前模式可用的工具列表。"""
    tools = list(TOOLS_ALL_MODES)
    if self.mode == "record":
        tools.extend(TOOLS_RECORD_ONLY)
    elif self.mode == "general":
        tools.extend([TOOL_START_RECORD_THREAD, TOOL_ANALYZE])
    # analyze 模式：只有 TOOLS_ALL_MODES
    return tools
```

### 3.3 `run()` 中使用动态工具列表

```python
# 改前：tools=TOOLS_OPENAI_FORMAT,
# 改后：tools=self._get_active_tools(),
```

### 3.4 工具 handler 保留模式守卫（防御性编程）

```python
def _update_schema(self, args, loop):
    if loop._schema_context is None:
        return {"error": "not_in_record_mode",
                "message": "update_schema 只在记录实验时可用。"}
    ...

def _generate_record(self, args, loop):
    if loop._schema_context is None:
        return {"error": "not_in_record_mode",
                "message": "generate_record 只在记录实验时可用。"}
    ...
```

工具不可见 + handler 守卫 = 两道防线。

---

## 四、线程生命周期中的 Schema 状态管理

### 4.1 线程开始 → 初始化 Schema

在 `_start_record_thread` 和 `_maybe_inject_thread_start`（record type）中调用 `_enter_record_mode()`。

注意：analyze 线程开始不初始化 Schema（analyze 没有 Schema）。

### 4.2 线程正常结束 → 清理 Schema

在 `_maybe_inject_thread_end` 中调用 `_exit_record_mode()`。

### 4.3 线程取消 → 清理 Schema + modified_values

在 `_check_thread_cancellation` 中增加：

```python
self._schema_context = None
self.modified_values = {}
```

### 4.4 `_update_schema` 中 analyze→record 切换 → 清理 + 初始化

```python
if loop.thread_id:
    for m in loop.history:
        if f"thread_begin id={loop.thread_id} type=analyze" in (...):
            # 结束 analyze
            ...
            loop.thread_id = None
            loop._thread_type = None
            break
# 之后 _pending_thread_start flag 会在下一轮触发 record 线程开始
# 届时 _enter_record_mode() 被调用
```

---

## 五、状态序列化改造

### 5.1 `state_to_dict()`

```python
def state_to_dict(self) -> dict:
    result = {
        "context": self._schema_context,      # None when not in record mode
        "references": self.references,         # 跨模式，始终保留
        "experiment_type": self.experiment_type,
        ...
    }
    return result
```

`context` 为 `None` 时序列化为 `null`。前端/子 Agent 读 `null` 时不展示 Schema 相关 UI。

### 5.2 `from_dict()`

```python
loop._schema_context = data.get("context")  # None 表示非 record 模式
loop.references = data.get("references", [])
loop.experiment_type = data.get("experiment_type", "other")
```

### 5.3 向后兼容

旧的 `_current_state.yaml` 中 `context` 字段始终存在（即使是全空 dict）。`from_dict` 检测空 context 处理为 None：

```python
ctx = data.get("context")
if ctx and any(_is_filled(v) for v in ctx.values()):
    loop._schema_context = ctx
else:
    loop._schema_context = None
```

---

## 六、`run()` 返回值中的 context

```python
# 改前：始终返回 self.context
return {"type": "reply", "message": ..., "context": self.context}

# 改后：仅在 record 模式返回
ctx = self._schema_context if self.mode == "record" else None
return {"type": "reply", "message": ..., "context": ctx}
```

---

## 改动汇总

| # | 类型 | 说明 | 预估行数 |
|---|------|------|---------|
| 1 | Prompt 拆分 | BASE + RECORD + ANALYZE + GENERAL 四个常量 | +120 |
| 2 | 组装方法 | `_build_mode_prompt()` + `run()` 中注入 | +15 |
| 3 | `mode` 属性 | 统一模式判断入口 | +5 |
| 4 | Schema 生命周期 | `_enter_record_mode()` / `_exit_record_mode()` | +15 |
| 5 | 变量替换 | `self.context` → `self._schema_context`（约 15 处） | ~20 |
| 6 | 工具分组 | `_get_active_tools()` + 用动态列表 | +15 |
| 7 | 工具守卫 | `update_schema`/`generate_record` 增加模式检查 | +6 |
| 8 | 线程调用 | 开始/结束/取消注入 Schema 生命周期调用 | +8 |
| 9 | 序列化 | `state_to_dict`/`from_dict` 条件化 | +10 |
| **总计** | | | **~214 行** |

---

## 缓存效果对比

| 场景 | 改前 | 改后 |
|------|------|------|
| 自由模式 per-request | 2500 token SYSTEM_PROMPT（始终缓存命中） | 1300 token BASE（缓存命中）+ 80 token GENERAL（命中）|
| record 模式 per-request | 同上 | 1300（命中）+ 800 RECORD（命中）+ 30 状态（命中）|
| 模式切换 miss | 无（Prompt 不变） | 仅 mode_prompt + status ~200-830 token |
| **无效上下文** | analyze 下 ~1200 token 噪音 | **0** — LLM 只看模式相关内容 |

## 验证方案

1. 启动应用，打开对话面板
2. 自由闲聊 2 轮 → 检查 `call_*_request.json` 中 messages[1] 仅含 GENERAL prompt，工具列表不含 `update_schema`
3. 说 "记录新实验" → 检查 Prompt 切换到 RECORD，工具列表出现 `update_schema`/`generate_record`
4. `generate_record` 后 → 检查 Prompt 回到 GENERAL，`_schema_context` 为 None
5. 启动 analyze → 检查 Prompt 切换到 ANALYZE，工具列表不含 `update_schema`/`generate_record`/`analyze`
6. 检查 `_current_state.yaml` → analyze/自由模式下 `context` 为 null
