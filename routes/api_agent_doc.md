# routes/api_agent.py — 说明文档

## 文件作用摘要

父 Agent 对话 API 蓝图 `api_agent_bp`，URL 前缀 `/api/agent`。处理 Agent 对话的启动（`/start`）、消息发送（`/message` 非流式 + `/message/stream` 流式 SSE）、确认保存（`/confirm`）。前端通过 fetch JSON 或 ReadableStream 消费 SSE 与此 API 交互，实现 SPA 式聊天界面。

---

## 代码块详细说明

### 路由函数

- `api_agent_start()` — POST `/api/agent/start`: 开始或恢复 Agent 会话
  - 无请求体。先通过 `g.thread_repo.load_current_state()` 检查磁盘是否有历史状态。
  - **恢复路径**: `_current_state.yaml` 存在 → `get_or_create_agent` 从磁盘恢复 → 直接返回 state（`type: "resumed"`），不调 `run("")` 避免 LLM 对已有 history 重复回复
  - **新建路径**: `_current_state.yaml` 不存在 → 新建 AgentLoop → `agent.run("")` 获取 greeting → 返回 `{ok, state, type, message, greeting, context}`
  - 使用 `g.get_agent_llm()`, `g.exp_repo`, `g.thread_repo`, `g.update_log_repo`, `g.favorites_repo`, `g.analysis_repo`, `g.analysis_svc`, `g.extraction_svc`

- `api_agent_message()` — POST `/api/agent/message`: 发送用户消息
  - 请求体: `{message: str, state: dict}` (message 不能为空，state 必须提供)
  - `get_or_create_agent(state_dict=state)` → `agent.run(message)`
  - **返回分两种**:
    1. `type ∈ {extract, generate}`: preview 和 notes 直接来自 Agent 的 `_generated_preview` / `_generated_notes` → `g.exp_repo.save()` 保存 → `g.experiment_svc.update_referenced_by()` → `g.experiment_svc.move_draft_images()` → 返回 `{ok, type: "saved", exp_id, state, message}`
    2. 其他 type: 返回 `{ok, state, type, message, context}`

- `api_agent_message_stream()` — POST `/api/agent/message/stream`: 流式发送用户消息（SSE）
  - 请求体: `{message: str, state: dict}`（同 `/message`）
  - `get_or_create_agent(state_dict=state)` → `agent.run_stream(message)` 生成器
  - 通过 `stream_with_context` 逐事件产出 SSE (`text/event-stream`)
  - 事件类型: `{"event": "text", "content": "..."}` → `{"event": "tool", "name": "..."}` → `{"event": "tool_done", "name": "..."}` → `{"event": "done", "type": "reply"|"generate", "state": ..., "message": ...}`
  - done 事件时自动检查 `agent._generated_preview`，有则自动保存实验并返回 `type: "saved"` + `exp_id`

- `api_agent_confirm()` — POST `/api/agent/confirm`: 确认生成实验。直接委托给 `routes/api_experiment.py` 的 `api_parse_confirm()`
