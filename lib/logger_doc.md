# lib/logger.py — 说明文档

## 文件作用摘要

Exdiary 统一日志系统。通过 4 个 JSONL 文件覆盖父子 Agent 的全部运行时行为。提供模块级 `init_logger()` 初始化全局单例、`get_logger()` 获取全局单例。日志写入失败静默忽略，不影响主流程。被 `app.py` 和 `lib/agent_v2.py` 导入使用。

---

## 代码块详细说明

### 模块级私有变量

- `_logger: ExdiaryLogger | None = None` — 全局日志单例，初始为 None

### 模块级函数

#### `init_logger(base_dir: str | Path) -> ExdiaryLogger`
- **作用**: 初始化全局日志系统单例，创建 `{base_dir}/_logs/` 目录
- **输入**: `base_dir` — 日志目录的父级路径（日志实际写入 `{base_dir}/_logs/`）
- **输出**: `ExdiaryLogger` 实例
- **被调用情况**: `app.py:120` — 模块级代码中 `init_logger(BASE_DIR / "experiments")` 在应用启动时调用

#### `get_logger() -> ExdiaryLogger | None`
- **作用**: 获取全局日志单例
- **输入**: 无
- **输出**: `ExdiaryLogger` 实例；如果未调用 `init_logger()` 则返回 `None`
- **被调用情况**:
  - `lib/agent_v2.py:13` — `from lib.logger import get_logger`
  - `lib/agent_v2.py` 内部所有日志调用点均通过 `log = get_logger(); if log: log.xxx()` 模式:
    - `AgentLoop.run()` 中: line 1084 (获取 log), line 1090 (log.agent), line 1145 (log.system), line 1148 (log.agent), line 1169 (log.agent), line 1182 (log.agent), line 1205 (log.tool)
    - `ToolExecutor._start_record_thread()` 中: line 276 (log.operation)
    - `ToolExecutor._start_analyze_thread()` 中: line 314 (log.operation)
    - `AgentLoop._maybe_inject_thread_end()` 中: line 1527 (log.operation)
    - `AgentLoop._maybe_inject_thread_start()` 中: line 1511 (log.operation)
    - `AgentLoop._check_thread_cancellation()` 中: line 1609 (log.operation)
  - `app.py:204` — `log = get_logger(); if log: log.system("info", "startup", port=port, gui=...)`

### 类

#### `ExdiaryLogger`
- **作用**: 日志写入器。管理 4 个 JSONL 日志文件
- **构造参数**: `base_dir: Path` — 日志父目录。自动创建 `{base_dir}/_logs/` 子目录（`self.dir`）
- **实例属性**: `self.dir: Path` — 即 `{base_dir}/_logs/`

##### 内部方法

- `_write(filename: str, entry: dict) -> None`: 追加一条 JSON 记录到指定日志文件。自动添加 `ts` 时间戳（ISO 8601 格式 `YYYY-MM-DDTHH:MM:SS`）。所有写入异常静默忽略（`except Exception: pass`）
- `_agent_type(loop) -> str`: 从 AgentLoop 实例推断 agent 类型。`loop is None → "?"`, `loop.child.is_child → "child"`, 否则 `"parent"`
- `_agent_exp(loop) -> str | None`: 从子 Agent 实例提取关联的实验 ID。`loop is None → None`, `loop.child.is_child → loop.child.exp_id`, 否则 `None`

##### agent.log 系列 — 对话消息日志

写入文件: `_logs/agent.log`

- `agent(agent: str, role: str, content: str, tool_calls: list[str] | None = None, exp: str | None = None) -> None`: 记录一条对话消息。content 截断至 2000 字符。tool_calls 存为工具名列表。exp 附加实验 ID
  - **被调用**: `AgentLoop.run()` 中记录 user 和 assistant 消息
- `agent_user(loop, content: str) -> None`: 自动推断 agent 类型 + exp ID 的记录 user 消息便捷方法
- `agent_assistant(loop, content: str, tool_calls: list[str] | None = None) -> None`: 自动推断的记录 assistant 消息便捷方法

##### tools.log 系列 — 工具调用日志

写入文件: `_logs/tools.log`

- `tool(agent: str, tool_name: str, ok: bool, exp: str | None = None, **summary) -> None`: 记录工具调用摘要。summary 中的所有 falsy 值（None、空字符串、0、False 等）**均被过滤**（源码: `if v`）。注意这与 `operation()`/`system()` 的 `if v is not None` 过滤逻辑不同
  - **被调用**: `AgentLoop.run()` 中 line 1205 — `log.tool(ag, name, ok, exp=..., **kw)` 其中 kw 来自 `_tool_log_summary()`
- `tool_from_loop(loop, tool_name: str, ok: bool, **summary) -> None`: 自动推断 agent 类型的便捷方法

##### operations.log 系列 — 文件/状态变更日志

写入文件: `_logs/operations.log`

- `operation(op: str, agent: str | None = None, **kwargs) -> None`: 记录操作事件。值为 None 的 kwargs 被过滤（注意：空字符串 `""`、数字 `0`、布尔 `False` 等非 None 的 falsy 值**不会被过滤**，这与 `tool()` 方法不同）
  - **被调用**: `AgentLoop` 的线程生命周期各阶段记录 thread_start / thread_end / thread_cancelled 等
- `op_from_loop(loop, op: str, **kwargs) -> None`: 自动推断 agent 类型的便捷方法

##### system.log 系列 — 系统事件日志

写入文件: `_logs/system.log`

- `system(level: str, event: str, **kwargs) -> None`: 记录系统级事件（info/warning/error），值为 None 的 kwargs 被过滤（与 `operation()` 相同的 `v is not None` 逻辑）
  - **被调用**: `AgentLoop.run()` 的 LLM 异常兜底中 `log.system("error", "llm_call_failed", error=str(e)[:200])`；`app.py` 启动时 `log.system("info", "startup", ...)`
- `exception(event: str, **kwargs) -> None`: 自动附带完整 traceback（截断至 2000 字符），level 固定为 "error"
