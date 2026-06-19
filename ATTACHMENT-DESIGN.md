# 附件功能 — 设计与实施文档

## 概述

新增实验附件字段 `attachments`，支持上传表征数据文件（.txt/.xlsx/.csv）和图片（.png/.jpg 等）。Attachments 存储元数据，分析线程内通过 `read_attachment` 工具按需读取文件内容，LLM 进行数据对比和趋势分析。

## Schema

### EXPERIMENT_SCHEMA 新增字段

```yaml
attachments:
  type: array
  items:
    type: object
    properties:
      path:        {type: string}   # 文件路径，如 "uploads/EXP-028/xrd_pattern.txt"
      note:        {type: string}   # 用户备注或 LLM 生成的描述
      added_at:    {type: string}   # 上传时间 YYYY-MM-DD HH:MM:SS
```

### DEFAULT_CONTEXT 新增默认值

```python
"attachments": [],
```

## 上传流程

### 扩展文件类型

`routes/api_upload.py` — `ALLOWED_EXTENSIONS` 追加：

```python
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
                      ".txt", ".csv", ".xlsx", ".json", ".pdf"}
```

### 上传 API 返回

```json
{"ok": true, "url": "/uploads/EXP-028/xrd_pattern.txt"}
```

### ExperimentService 新增

```python
def add_attachment(exp_id, path, note):
    exp = self.exp_repo.load(exp_id)
    exp.setdefault("attachments", []).append({
        "path": path,
        "note": note,
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    self.exp_repo.save(exp)
```

```
POST /api/experiments/<exp_id>/attachments
Body: {"path": "...", "note": "..."}
```

## read_attachment 工具

### 工具定义 (agent_tools.py)

```
TOOL_READ_ATTACHMENT:
  name: read_attachment
  description: 读取实验附件的文件内容。仅 analyze 模式可用。
              文本类文件(.txt/.csv)返回内容，表格(.xlsx)返回sheet概览和样本数据，图片返回文件信息。
  parameters:
    exp_id: string (required) — 实验 ID
    filename: string (required) — 文件名（不含路径）
```

### 工具实现 (agent_v2.py ToolExecutor)

```python
def _read_attachment(self, args, loop):
    exp_id = args["exp_id"]
    filename = args["filename"]
    exp = self.store.load(exp_id)

    # 1. 从 attachments 中找到匹配项
    att = next((a for a in exp.get("attachments", [])
                if Path(a["path"]).name == filename), None)
    if not att:
        return {"error": "not_found", "message": f"{exp_id} 中未找到附件 {filename}"}

    filepath = BASE_DIR / att["path"]
    ext = Path(filename).suffix.lower()

    # 2. 按扩展名处理
    if ext == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(filepath, data_only=True)
        sheets = {}
        for name in wb.sheetnames:
            ws = wb[name]
            headers = [cell.value for cell in ws[1]]
            rows = [[cell.value for cell in row] for row in ws.iter_rows(min_row=2, max_row=11)]
            sheets[name] = {"columns": headers, "preview": rows, "total_rows": ws.max_row - 1}
        return {"type": "excel", "sheets": sheets}

    elif ext in (".txt", ".csv"):
        content = filepath.read_text(encoding="utf-8")
        lines = content.split("\n")
        return {"type": "text", "lines": len(lines), "preview": lines[:200]}

    elif ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        return {"type": "image", "note": att.get("note", ""),
                "message": f"这是一个图片文件 ({ext})，无法直接读取内容。"}

    elif ext == ".pdf":
        return {"type": "pdf", "note": att.get("note", ""),
                "message": "PDF 文件当前不支持内容提取。"}

    else:
        return {"type": "unknown", "message": f"不支持此文件类型 ({ext})"}
```

### 权限范围

- 仅在 **analyze 模式**和**子 Agent analysis_reviewer** 角色可用
- 不可用于 record 模式、general 模式、exp_editor 子 Agent

### _get_active_tools 变更

analyze 模式追加 `TOOL_READ_ATTACHMENT`，analysis_reviewer 子 Agent 同样追加。

## Agent 对话集成

### System Prompt 补充 (prompts.py)

analyze 工作方式中新增：

```
分析师可以通过 read_attachment 读取实验附件的原始数据。
支持的附件类型：
- .txt / .csv：返回全文内容（最多200行预览）
- .xlsx：返回全部 sheet 的列名、前10行数据和总行数
- 图片：返回文件信息和备注，当前不支持直接图像分析

使用场景：
- 对比不同实验的 JV 曲线数据（jv_curve.xlsx）
- 分析 XRD 图谱特征峰（xrd_pattern.txt）
- 查看 SEM 图像的备注描述
```

### _summarize_exp 补充

```python
attachments = exp.get("attachments", [])
if attachments:
    lines = ["附件:"]
    for a in attachments:
        fname = Path(a["path"]).name
        note = a.get("note", "")[:60]
        lines.append(f"  {fname}: {note}" if note else f"  {fname}")
    result["attachments"] = "\n".join(lines)
```

### 文件改动清单

| 文件 | 改动 |
|------|------|
| `lib/core/schema.py` | `EXPERIMENT_SCHEMA` 新增 `attachments` 字段；`DEFAULT_CONTEXT` 加 `attachments: []` |
| `lib/core/agent_tools.py` | 新增 `TOOL_READ_ATTACHMENT` 和 `TOOLS_OPENAI_FORMAT` 追加 |
| `lib/core/prompts.py` | SYSTEM_PROMPT analyze 模式新增 `read_attachment` 说明 |
| `lib/agent_v2.py` | `_get_active_tools()` 追加；`ToolExecutor` 注册 + 实现 `_read_attachment`；`_summarize_exp` 追加 attachments 摘要 |
| `lib/services/experiment.py` | 新增 `add_attachment` 方法 |
| `routes/api_upload.py` | 扩展允许的文件类型 |
| `routes/api_experiment.py` | 新增 `POST /api/experiments/<exp_id>/attachments`（可选，先手动编辑 YAML 也可以） |

### 实施顺序

1. Schema + 工具定义（不依赖前端）
2. 上传 API + `add_attachment`（前端配合）
3. Agent 工具实现 + Prompt 补充（不依赖前端）
4. 前端附件管理 UI（后续）
