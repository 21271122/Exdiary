# Exdiary Branches 系统 — 实施方案

## 一、目标

子 Agent 每次修改已完成的实验时，该次对话作为线程的一个**分支（branch）**持久化到磁盘。用户可：
1. 在 EXP 详情页查看该实验的完整修改历史（谁、何时、改了什么）
2. 点击任意分支查看当时的对话记录
3. 基于任意分支继续修改（续接历史对话上下文）

---

## 二、数据模型

### 2.1 线程文件 branches 字段（改进后）

```yaml
# experiments/_threads/THR-2026-001.yaml
branches:
  - id: THR-2026-001-b1
    created: "2026-05-25 14:20:00"
    exp_id: EXP-2026-019
    summary: "退火温度 150→200°C，ETL 厚度 120→200nm"
    changed_fields: ["退火温度", "ETL 厚度"]
    update_entry_ids: ["UPD-019-003"]
    message_count: 8          # 该分支的对话消息数（用于前端预览）
    child_messages:           # 仅子Agent自身的对话消息
      - role: system
        content: "[修改模式] 你正在修改已完成的实验 EXP-2026-019..."
      - role: assistant
        content: "已加载 EXP-019 当前数据。目前退火温度是 150°C..."
      - role: user
        content: "退火温度改成 200"
      - role: assistant
        content: "好的，已更新退火温度为 200°C。还需要改什么？"
```

### 2.2 与现状的差异

| 字段 | 现状 | 改进后 |
|------|------|--------|
| `messages` | `self.history`（含 L0 + 线程消息 + 子Agent对话）| `child_messages`（仅子Agent自身对话） |
| `summary` | `"修改了 N 个字段"` | 从 update_log 读取实际变更描述 |
| `changed_fields` | 不存在 | 新增：被修改的字段名列表 |
| `exp_id` | 不存在 | 新增：被修改的实验 ID |
| `update_entry_ids` | 不存在 | 新增：关联的更新日志条目 |
| `message_count` | 不存在 | 新增：对话消息数量 |

### 2.3 索引增强（index.yaml）

```yaml
# 新增: 实验 → 线程的完整分支信息（直接在 exp_to_thread 值中扩展）
exp_to_thread:
  EXP-2026-019:
    thread_id: THR-2026-001
    branch_count: 2
    last_branch_at: "2026-05-25 14:20:00"
```

或者保持 `exp_to_thread` 为简单字符串映射，分支信息从线程文件动态读取（推荐——避免索引膨胀）。

---

## 三、实施阶段

### Phase 1: 存储精炼（~40 行，lib/agent_v2.py）

**目标**：让 `_child_cleanup` 写入精炼后的 branch 数据。

**改动 `_child_cleanup`：**

```python
def _child_cleanup(self, exp_id: str) -> None:
    if not self._is_child_agent or not self.thread_store:
        return
    tid = self.thread_id
    if not tid:
        return

    # 1. 写待合并消息（父Agent过期标记）
    self.thread_store.enqueue_merge({...})  # 保持不变

    # 2. 提取子Agent自身消息（不含线程历史）
    start = self._child_initial_history_len
    child_only = self.history[start:]

    # 3. 从更新日志获取实际变更描述
    changed_fields = list(self.modified_values.keys())
    summary_parts = []
    if self.update_log_store:
        recent = self.update_log_store.list_recent(exp_id, limit=1)
        if recent:
            for c in recent[0].get("changes", []):
                summary_parts.append(f"{c.get('field','')} {c.get('old','')}→{c.get('new','')}")

    # 4. 写入分支
    thread = self.thread_store.load(tid)
    if thread:
        branch = {
            "id": f"{tid}-b{len(thread.get('branches', [])) + 1}",
            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "exp_id": exp_id,
            "summary": "; ".join(summary_parts) if summary_parts else f"修改了 {len(changed_fields)} 个字段",
            "changed_fields": changed_fields,
            "message_count": len(child_only),
            "child_messages": child_only,
        }
        thread.setdefault("branches", []).append(branch)
        self.thread_store.save(thread)

    # 5. 清理
    self.thread_store.delete_child_state(tid)
```

**向后兼容**：现有 branch 数据中 `messages` 字段（全量）不会被自动迁移。在 Phase 3 前端渲染时做兼容判断——如果 branch 有 `child_messages` 用新的，否则回退到 `messages`。

---

### Phase 2: API 层（~50 行，app.py）

#### 2.1 列出分支

```
GET /api/exp/<exp_id>/branches
```

**逻辑**：
1. 从 `exp_to_thread` 查 thread_id
2. 如果无线程 → 返回空列表（旧实验无分支）
3. 加载线程文件 → 提取 `branches` 字段
4. 返回精简列表（不含 `child_messages`，仅元数据）

**返回格式**：
```json
{
  "ok": true,
  "thread_id": "THR-2026-001",
  "branches": [
    {
      "id": "THR-2026-001-b2",
      "created": "2026-05-25 14:20:00",
      "summary": "退火温度 150→200，ETL厚度 120→200nm",
      "changed_fields": ["退火温度", "ETL厚度"],
      "message_count": 8
    },
    {
      "id": "THR-2026-001-b1",
      "created": "2026-05-24 10:00:00",
      "summary": "修改了 1 个字段",
      "changed_fields": ["purpose"],
      "message_count": 4
    }
  ]
}
```

#### 2.2 从分支恢复子 Agent

修改 `POST /api/exp/<exp_id>/chat`，新增可选参数 `branch_id`：

```
POST /api/exp/<exp_id>/chat
body: {
  message: "",
  branch_id: "THR-2026-001-b2"   // 可选：恢复指定分支
}
```

**逻辑**（在现有 case 4 之前插入）：
```python
branch_id = data.get("branch_id")
if branch_id and thread_id:
    # 从分支恢复
    thread = thread_store.load(thread_id)
    branch = next((b for b in thread.get("branches", []) if b["id"] == branch_id), None)
    if branch:
        parent = AgentLoop(...)
        agent = AgentLoop.create_child_agent(parent, thread_id)
        # 替换 child_initial_history_len 之后的消息为分支对话
        agent.history = agent.history[:agent._child_initial_history_len]
        agent.history.extend(branch.get("child_messages", branch.get("messages", [])))
        agent._child_initial_history_len = len(agent.history)
        agent._is_child_agent = True
        agent._child_exp_id = exp_id
        # run("") 生成问候
        result = agent.run(user_message if user_message else "")
        ...
```

#### 2.3 获取分支详情（可选，Phase 3 按需实现）

```
GET /api/exp/<exp_id>/branches/<branch_id>
→ 返回完整 child_messages（用于前端渲染对话）
```

前端可以先通过分支列表获取元数据，点击某个分支时再懒加载完整对话。如果分支很少（通常 2-5 个），也可以直接在列表 API 中返回完整消息。

---

### Phase 3: 前端（~200 行，templates/view.html + 新增 CSS）

#### 3.1 EXP 详情页：修改历史折叠区

位置：`view.html` 底部，在现有内容之后。

```
┌─────────────────────────────────────────┐
│  ▶ 修改历史 (3)                          │  ← <details> 折叠区
├─────────────────────────────────────────┤
│  2026-05-25 14:20                        │
│  退火温度 150→200, ETL厚度 120→200nm     │
│  [查看对话]                              │  ← 点击打开子Agent并加载分支消息
│  ─────────────────────────────────────── │
│  2026-05-24 10:00                        │
│  修改了 purpose 字段                      │
│  [查看对话]                              │
│  ─────────────────────────────────────── │
│  2026-05-22 20:35  (原始记录)             │
│  首次创建，4轮对话                        │
│  [查看对话]                              │  ← 点击打开子Agent查看原始记录
└─────────────────────────────────────────┘
```

**实现要点**：
- 页面加载时调 `GET /api/exp/<exp_id>/branches` 获取分支列表
- 无分支时隐藏整个折叠区
- 原始记录始终作为第一条显示（从线程文件的 `messages` 字段获取）
- "查看对话"按钮调用 `openChildAgent()` 的变体，传入 `branch_id`（或 `null` 表示原始记录）

#### 3.2 子 Agent 面板：分支恢复

修改 `openChildAgent(optBranchId)`：

```javascript
function openChildAgent(optBranchId) {
  // ... 现有加载逻辑 ...
  var body = {message: ''};
  if (optBranchId) body.branch_id = optBranchId;
  // 优先从 sessionStorage 恢复（当前会话的修改）
  var saved = loadChildSession();
  if (saved && !optBranchId) { body.state = saved; }
  // ...
}
```

#### 3.3 分支恢复时的面板标题

正常打开：`修改 EXP-2026-019`
从分支恢复：`修改 EXP-2026-019 · 分支 b2（2026-05-25）`

在面板 header 中动态设置。

#### 3.4 CSS 新增

```css
.branch-list { margin-top: 1.5rem; }
.branch-item { padding: 0.6rem 0; border-bottom: 1px solid #eee; }
.branch-item:last-child { border-bottom: none; }
.branch-meta { font-size: 0.8rem; color: #888; margin-bottom: 0.2rem; }
.branch-summary { font-size: 0.9rem; margin-bottom: 0.3rem; }
.branch-fields { display: flex; gap: 0.3rem; flex-wrap: wrap; margin-bottom: 0.3rem; }
.branch-field-tag { font-size: 0.7rem; background: #e8f0fe; color: #1a73e8; padding: 0.1rem 0.4rem; border-radius: 4px; }
```

---

### Phase 4: 集成测试（手动）

| 测试场景 | 预期行为 |
|---------|---------|
| 新实验无分支 | EXP 详情页不显示"修改历史"折叠区 |
| 子Agent修改并确认 | 分支列表新增一条 |
| 关闭子Agent后再打开 | 恢复当前会话（不被分支列表影响） |
| 点击分支"查看对话" | 子Agent面板展示该分支的历史对话 |
| 在分支对话中继续修改 | 新消息追加到当前会话，确认后创建新分支 |
| Legcy 实验（无线程） | 不显示修改历史（无法关联分支） |

---

## 四、与现有机制的配合

```
用户手动编辑保存  ──→ UpdateLogStore ──→ 出现在 L0 "近期被修改的实验"
                                        │
子Agent修改并确认  ──→ UpdateLogStore ──→ 同上
                   │
                   ├─→ _pending_merges/  ──→ 父Agent过期标记
                   │
                   └─→ thread.branches   ──→ 修改历史UI（本次新增）
                        (child_messages)
```

三层各司其职：
- **UpdateLogStore**：字段级别的 old→new diff，用于 L0 摘要
- **_pending_merges/**：父 Agent 的过期通知，触发 load_reference
- **branches**：对话级别的完整记录，用于人工回溯和理解修改上下文

---

## 五、向后兼容

1. **现有 branch 数据**：`_child_cleanup` 此前从未被调用（已修复），所以实际上没有旧的 branch 数据需要迁移。
2. **旧实验（无线程）**：`exp_to_thread` 中无映射 → 无 thread → 无 branch。不显示修改历史。
3. **Legcy 子 Agent 修改**：无线程关联，修改写入了 `UpdateLogStore` 但无法关联到 branch。这些修改在"修改历史"UI 中不可见（但更新日志仍可通过 API 查询）。
4. **父 Agent 的 `modify_experiment` 工具修改**：同样写 `UpdateLogStore`，但当前不创建 branch。未来可考虑父 Agent 修改也创建 branch。

---

## 六、代码量估算

| Phase | 文件 | 新增 | 修改 | 删除 |
|-------|------|------|------|------|
| 1 | `lib/agent_v2.py` | ~25 行 | ~15 行 | 0 |
| 2 | `app.py` | ~45 行 | ~5 行 | 0 |
| 3 | `templates/view.html` | ~150 行 JS + ~40 行 HTML | ~10 行 | 0 |
| 3 | `templates/view.html` CSS | ~20 行 | 0 | 0 |
| **合计** | | **~240 行** | **~30 行** | **0** |

---

## 七、优先级与风险

**优先级**：P1 — branches 是子 Agent 系统的自然延伸。没有它，修改历史不可追溯。

**风险**：
- `child_messages` 可能很大（20+ 轮对话）。对策：前端懒加载分支详情（`GET /api/exp/<id>/branches/<bid>`），列表 API 只返回元数据。
- 线程文件的 YAML 体积增长。对策：分支消息中截断过长的 tool 结果（>5000 chars 的内容用 `...[截断]` 替代）。

**暂不做**：
- 分支删除（线程不可变原则）
- 分支合并（一个线程→一个产出物，不需要合并）
- 分支 diff 可视化（diff 数据已在 UpdateLogStore，前端直接读更新日志即可）
