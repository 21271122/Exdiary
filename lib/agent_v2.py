"""
Exdiary Agent v2 — 基于 Tool Calling 的对话式实验记录系统

LLM 自主决策流程，Python 仅执行工具和注入 Schema 状态。
"""

import json, os, re, sys, traceback
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

from lib.llm import LLMResponse
from lib.logger import get_logger
from lib.core.agent_tools import (
    TOOL_LOAD_REFERENCE, TOOL_SEARCH_EXPERIMENTS, TOOL_UPDATE_SCHEMA,
    TOOL_ASK_USER, TOOL_GENERATE_RECORD, TOOL_START_RECORD_THREAD,
    TOOL_END_THREAD, TOOL_START_ANALYZE_THREAD, TOOL_SELECT_EXPERIMENTS,
    TOOL_GENERATE_ANALYSIS, TOOL_MODIFY_ANALYSIS, TOOL_READ_UPDATE_LOG,
    TOOL_MODIFY_EXPERIMENT, TOOL_MANAGE_COLLECTION, TOOL_QUERY_EXPERIMENT,
    TOOL_LIST_EXPERIMENTS, TOOLS_OPENAI_FORMAT,
)
from lib.core.prompts import build_system_prompt
from lib.core.schema import DEFAULT_CONTEXT


def _cleanup_old_debug_dirs(debug_root: Path, max_age_days: int = 30) -> None:
    """删除超过 max_age_days 天的旧调试目录。"""
    if not debug_root.exists():
        return
    cutoff = datetime.now().timestamp() - max_age_days * 86400
    for subdir in debug_root.iterdir():
        if subdir.is_dir():
            try:
                if subdir.stat().st_mtime < cutoff:
                    import shutil
                    shutil.rmtree(subdir, ignore_errors=True)
            except OSError:
                pass



class ChildContext:
    """子 Agent 标记。仅子 Agent 实例时有效。"""
    __slots__ = ('is_child', 'is_legacy', 'exp_id', 'initial_history_len', 'agent_role')
    def __init__(self):
        self.is_child = False
        self.is_legacy = False
        self.exp_id = None
        self.initial_history_len = 0
        self.agent_role = None


class ThreadState:
    """线程状态。"""
    __slots__ = ('id', 'type', 'pending_start', 'current_turn_user_idx', 'last_ended_id')
    def __init__(self):
        self.id = None
        self.type = None
        self.pending_start = None
        self.current_turn_user_idx = -1
        self.last_ended_id = None


# Tool definitions, SYSTEM_PROMPT, and DEFAULT_CONTEXT migrated to lib/core/

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


def _build_preview(loop: "AgentLoop") -> dict:
    """确定性构建：从 context 直接构造实验记录预览，不调 LLM。
    parse_notes 失败时的退化兜底——保证输出结构完整，但不做语义推断和补全。"""
    ctx = loop._schema_context or {}
    return {
        "id": loop.store.next_id(),
        "title": ctx.get("title", ""),
        "date": ctx.get("date", ""),
        "experimenter": ctx.get("experimenter", ""),
        "status": ctx.get("status", "planned"),
        "tags": ctx.get("tags", []),
        "purpose": ctx.get("purpose", ""),
        "materials": ctx.get("materials", []),
        "equipment": ctx.get("equipment", []),
        "experimental_plan": ctx.get("experimental_plan", []),
        "sop": ctx.get("sop", []),
        "process_parameters": ctx.get("process_parameters", []),
        "observations": ctx.get("observations", {"no_anomalies": True, "items": []}),
        "characterization": ctx.get("characterization", []),
        "results": ctx.get("results", {"qualitative": "", "key_data": [], "figures": []}),
        "conclusion": ctx.get("conclusion", ""),
        "next_steps": ctx.get("next_steps", []),
        "original_notes": "",
        "references": list(loop.references),
    }


def _extract_thread_dialogue(loop: "AgentLoop") -> str:
    """从当前线程 history 中提取用户与助手的纯文本对话。
    过滤系统消息、工具调用、工具结果——只保留自然语言往返。"""
    lines: list[str] = []
    for m in loop.history:
        role = m.get("role", "")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if role == "system":
            continue
        if m.get("tool_calls"):
            continue
        if role == "tool":
            continue
        label = "用户" if role == "user" else "助手"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


#============================================================================
# 工具日志摘要
# ============================================================================

def _tool_log_summary(name: str, args: dict, result: dict) -> dict:
    """从工具名称、参数和结果中提取关键信息用于日志。"""
    kw = {}
    if name == "load_reference":
        kw["refs"] = args.get("refs", [])
        loaded = result.get("loaded", {}) if isinstance(result, dict) else {}
        kw["loaded_count"] = sum(1 for v in loaded.values() if isinstance(v, dict) and "error" not in v)
    elif name == "search_experiments":
        kw["query"] = args.get("query", "")
        kw["hits"] = len(result.get("candidates", [])) if isinstance(result, dict) else 0
    elif name == "update_schema":
        kw["fields"] = list((args.get("fields") or {}).keys())
    elif name == "ask_user":
        kw["questions"] = len(args.get("questions", []))
    elif name == "generate_record":
        kw["preview_id"] = result.get("id", "")
    elif name == "modify_experiment":
        kw["refs"] = args.get("refs", [])
        kw["fields"] = list((args.get("changes") or {}).keys())
    elif name == "manage_collection":
        kw["action"] = args.get("action", "")
        kw["refs"] = args.get("refs", [])
    elif name == "query_experiment":
        kw["question"] = args.get("question", "")[:100]
        kw["refs"] = args.get("refs", [])
    elif name == "analyze":
        kw["query"] = args.get("query", "")[:100]
    elif name == "list_experiments":
        kw.update({k: v for k, v in args.items() if v})
    elif name == "read_update_log":
        kw["exp_id"] = args.get("exp_id", "")
    if "error" in result:
        kw["error"] = str(result.get("message", result["error"]))[:200]
    return kw


# ============================================================================
# Step 1.2: ToolExecutor
# ============================================================================

class ToolExecutor:
    """注册、校验、执行 LLM 调用的工具"""

    def __init__(self, store, update_log_store=None, favorites_store=None, analysis_store=None):
        self.store = store
        self.update_log_store = update_log_store
        self.favorites_store = favorites_store
        self.analysis_store = analysis_store
        self.registry = {
            "load_reference": self._load_reference,
            "search_experiments": self._search_experiments,
            "start_record_thread": self._start_record_thread,
            "update_schema": self._update_schema,
            "ask_user": self._ask_user,
            "generate_record": self._generate_record,
            "read_update_log": self._read_update_log,
            "modify_experiment": self._modify_experiment,
            "manage_collection": self._manage_collection,
            "query_experiment": self._query_experiment,
            "list_experiments": self._list_experiments,
            "end_thread": self._end_thread,
            "start_analyze_thread": self._start_analyze_thread,
            "select_experiments": self._select_experiments,
            "generate_analysis": self._generate_analysis,
            "modify_analysis": self._modify_analysis,
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

    # -- start_record_thread --

    def _start_record_thread(self, args: dict, loop: "AgentLoop") -> dict:
        """LLM 判断要开始记录时调用，在当前 user 消息之后插入线程开始标记。"""
        if not loop.thread_store:
            return {"error": "no_thread_store", "message": "线程存储未配置"}
        if loop.thread.id:
            if loop.thread.type == "analyze":
                loop.history.append({"role": "system",
                    "content": f"[系统内部] thread_end id={loop.thread.id}"})
                loop.thread_store.set_active_thread(None)
                loop.thread.id = None
                loop.thread.type = None
            else:
                return {"status": "already_started", "thread_id": loop.thread.id}
        thread_id = loop.thread_store.next_id()
        loop.thread.id = thread_id
        loop.thread.type = "record"
        loop.thread_store.set_active_thread(thread_id)
        loop._enter_record_mode()
        begin = {"role": "system", "content": f"[系统内部] thread_begin id={thread_id} type=record"}
        pos = loop.thread.current_turn_user_idx + 1
        loop.history.insert(pos, begin)
        guidance = {"role": "system", "content": "你正在记录一条新实验。优先收集材料、步骤、参数、结果。追问缺失的关键字段。目标：generate_record。"}
        loop.history.insert(pos + 1, guidance)
        loop.thread_store.create("record", [begin, guidance])
        log = get_logger()
        if log:
            log.operation("thread_start", agent="parent", thread=thread_id, type="record")
        return {"status": "started", "thread_id": thread_id}

    # -- end_thread --

    def _end_thread(self, args: dict, loop: "AgentLoop") -> dict:
        """结束当前线程（record 或 analyze），归档并回到自由模式。"""
        if not loop.thread.id:
            return {"status": "no_active_thread",
                    "message": "当前没有活跃线程。"}
        tid = loop.thread.id
        loop._maybe_inject_thread_end("")
        return {"status": "ended", "thread_id": tid,
                "message": f"线程 {tid} 已结束，回到自由模式。"}

    # -- start_analyze_thread --

    def _start_analyze_thread(self, args: dict, loop: "AgentLoop") -> dict:
        """开启跨实验分析线程。与 start_record_thread 对称。"""
        if not loop.thread_store:
            return {"error": "no_thread_store", "message": "线程存储未配置"}
        if loop.thread.id:
            if loop.thread.type == "record":
                return {"error": "in_record_thread",
                        "message": "当前在 record 线程中。如需分析，请在 record 线程中使用 analyze 工具，或结束 record 线程后再开启 analyze 线程。"}
            if loop.thread.type == "analyze":
                return {"status": "already_started", "thread_id": loop.thread.id}
        thread_id = loop.thread_store.next_id()
        loop.thread.id = thread_id
        loop.thread.type = "analyze"
        loop.thread_store.set_active_thread(thread_id)
        begin = {"role": "system", "content": f"[系统内部] thread_begin id={thread_id} type=analyze"}
        pos = loop.thread.current_turn_user_idx + 1
        loop.history.insert(pos, begin)
        guidance = loop._build_thread_guidance("analyze")
        loop.history.insert(pos + 1, guidance)
        loop.thread_store.create("analyze", [begin, guidance])
        log = get_logger()
        if log:
            log.operation("thread_start", agent="parent", thread=thread_id, type="analyze")
        return {"status": "started", "thread_id": thread_id}

    # -- select_experiments --

    def _select_experiments(self, args: dict, loop: "AgentLoop") -> dict:
        """返回选择面板数据，由前端渲染为实验勾选卡片。"""
        return {
            "display": "selector",
            "pause": True,
            "title": args.get("title", "选择实验"),
            "items": args.get("candidates", []),
            "preselected": args.get("preselected", []),
        }

    # -- generate_analysis --

    def _generate_analysis(self, args: dict, loop: "AgentLoop") -> dict:
        """执行分析 → 写 AnalysisStore → 自动结束线程 → 返回标题+摘要。"""
        query = args["query"]
        refs = args.get("refs", [])
        if len(refs) < 2:
            return {"error": "too_few_experiments",
                    "message": "至少需要2个实验才能分析。"}
        try:
            if loop.analysis_svc:
                result = loop.analysis_svc.run_analysis(query, refs)
                anal_id = result["anal_id"]
                title = result["title"]
                excerpt = result["analysis"][:200]
            else:
                summary = self.store.summarize_all(exp_ids=refs)
                from lib.analyzer import analyze_experiments
                analysis = analyze_experiments(summary, query, loop.llm)
                anal_id = ""
                if self.analysis_store:
                    anal_id = self.analysis_store.save({
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "question": query,
                        "selected_ids": refs,
                        "analysis": analysis,
                    })
                    for exp_id in refs:
                        exp = self.store.load(exp_id)
                        if exp:
                            analyzed = exp.get("analyzed_in", [])
                            if anal_id not in analyzed:
                                analyzed.append(anal_id)
                                exp["analyzed_in"] = analyzed
                                self.store.save(exp)
                title = query[:40]
                for line in analysis.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#"):
                        title = line[:60]
                        break
                plain = re.sub(r'[#*>`\-\[\]\(\)]', '', analysis)
                plain = re.sub(r'\s+', ' ', plain).strip()
                excerpt = plain[:200]
            tid = loop.thread.id
            if tid and not loop.child.is_child:
                loop._maybe_inject_thread_end(anal_id)
            return {
                "display": "analysis_done",
                "anal_id": anal_id,
                "title": title,
                "summary": excerpt,
                "refs": refs,
            }
        except Exception as e:
            return {"error": "analysis_failed", "message": str(e)[:300]}

    # -- modify_analysis --

    def _modify_analysis(self, args: dict, loop: "AgentLoop") -> dict:
        """修改分析报告。支持 changes / additional_refs / additional_query。"""
        if not self.analysis_store:
            return {"error": "no_analysis_store", "message": "分析存储未配置"}

        # 从 thread 或 analysis_store 推断当前 anal_id
        anal_id = None
        if loop.child.exp_id:
            # analysis child agent 通过 _child_exp_id 传递 anal_id
            anal_id = loop.child.exp_id
        elif loop.thread_store and loop.thread.id:
            thread = loop.thread_store.load(loop.thread.id)
            if thread:
                anal_id = thread.get("anal_generated", "")

        if not anal_id:
            return {"error": "no_anal_id", "message": "无法确定要修改的分析报告"}

        a = self.analysis_store.load(anal_id)
        if not a:
            return {"error": "not_found", "message": f"分析报告 {anal_id} 不存在"}

        changes = args.get("changes")
        additional_refs = args.get("additional_refs", [])
        additional_query = args.get("additional_query")

        try:
            if changes:
                # 模式 1：直接覆盖
                a["analysis"] = changes
                self.analysis_store.save(a)
                return {"status": "modified", "mode": "replace",
                        "message": f"分析报告 {anal_id} 已更新。"}

            if additional_refs:
                # 模式 2：合并实验重新分析
                merged_refs = list(set((a.get("selected_ids") or []) + additional_refs))
                if len(merged_refs) < 2:
                    return {"error": "too_few_experiments",
                            "message": "合并后实验仍不足2个，无法分析。"}
                if loop.analysis_svc:
                    result = loop.analysis_svc.run_analysis(a.get("question", ""), merged_refs)
                    a["analysis"] = result["analysis"]
                    a["selected_ids"] = merged_refs
                    self.analysis_store.save(a)
                else:
                    summary = self.store.summarize_all(exp_ids=merged_refs)
                    from lib.analyzer import analyze_experiments
                    a["analysis"] = analyze_experiments(summary, a.get("question", ""), loop.llm)
                    a["selected_ids"] = merged_refs
                    self.analysis_store.save(a)
                    for exp_id in additional_refs:
                        exp = self.store.load(exp_id)
                        if exp:
                            analyzed = exp.get("analyzed_in", [])
                            if anal_id not in analyzed:
                                analyzed.append(anal_id)
                                exp["analyzed_in"] = analyzed
                                self.store.save(exp)
                return {"status": "modified", "mode": "expand_refs",
                        "message": f"分析报告 {anal_id} 已更新，纳入 {len(merged_refs)} 个实验。"}

            if additional_query:
                # 模式 3：在原报告基础上追加分析维度
                append_prompt = f"""以下是已有的分析报告：
{a.get('analysis', '')}

研究者希望补充以下分析维度：{additional_query}

请将新的分析维度融入报告，输出完整的更新后报告。
保持三区域结构（事实呈现/发现提示/值得思考的问题）。
不要只输出新增部分——输出完整报告。"""
                expanded = loop.llm.analyze(
                    system_prompt="你是 Exdiary 分析助手。请将补充分析维度融入已有报告，输出完整的更新后报告。中文回复。",
                    user_prompt=append_prompt,
                    temperature=0.3
                )
                a["analysis"] = expanded
                self.analysis_store.save(a)
                return {"status": "modified", "mode": "expand_query",
                        "message": f"分析报告 {anal_id} 已追加新的分析维度。"}

            return {"error": "no_action",
                    "message": "请提供 changes / additional_refs / additional_query 之一。"}

        except Exception as e:
            return {"error": "modify_failed", "message": str(e)[:300]}

    # -- ask_user（占位，实际由前端处理）--

    def _ask_user(self, args: dict, loop: "AgentLoop") -> dict:
        return {"status": "asked", "pause": True}

    # -- generate_record --

    def _generate_record(self, args: dict, loop: "AgentLoop") -> dict:
        # 子Agent 不允许 generate_record → 使用 modify_experiment 直接修改
        if loop.child.is_child:
            return {"error": "use_modify_experiment",
                    "message": "子Agent请使用 modify_experiment 工具直接修改实验字段。修改会自动保存。"}
        if loop._schema_context is None:
            return {"error": "not_in_record_mode",
                    "message": "generate_record 只在记录实验时可用。"}

        notes = loop._build_notes_from_context()

        # 构建增强 prompt: 四段式 = RAW SCHEMA + DIALOGUE + NOTES + REFERENCES
        import json as _json
        prompt_parts = []

        # 段 1: 原始 Schema JSON —— 让提取 LLM 精确知道哪些字段已填、哪些缺失
        raw_schema = _json.dumps(
            loop._schema_context, ensure_ascii=False, indent=2)
        prompt_parts.append(
            "---RAW SCHEMA (current field values, empty means unfilled)---\n"
            + raw_schema)

        # 段 2: 线程纯文本对话 —— 保留用户原始措辞（近似值、事后补充等细节）
        dialogue = _extract_thread_dialogue(loop)
        if dialogue:
            prompt_parts.append(
                "---DIALOGUE (original conversation for nuance)---\n"
                + dialogue)

        # 段 3: 自然语言实验描述 —— 帮助 LLM 理解语义连贯性
        prompt_parts.append(
            "---NOTES TEXT (structured summary of the experiment)---\n"
            + notes)

        # 段 4: 已加载引用实验的结构化摘要 —— 帮助校验和恢复漏掉的字段
        if loop.references:
            ref_parts = []
            for ref_id in loop.references:
                ref_exp = loop.store.load(ref_id)
                if ref_exp:
                    ref_parts.append(
                        _json.dumps(self._summarize_exp(ref_exp),
                                    ensure_ascii=False, indent=2))
            if ref_parts:
                prompt_parts.append(
                    "---REFERENCES (loaded experiments for comparison, "
                    "do NOT copy conclusion/results directly)---\n"
                    + "\n".join(ref_parts))

        enhanced_notes = "\n\n".join(prompt_parts)

        try:
            from lib.parser import parse_notes
            result = parse_notes(enhanced_notes, loop.llm)
            result["original_notes"] = notes
            # 子 Agent: 使用现有 EXP ID（修改已有实验）
            if loop.child.is_child and loop.child.exp_id:
                result["id"] = loop.child.exp_id
            else:
                result["id"] = loop.store.next_id()
            result["references"] = list(loop.references)
            loop._generated_preview = result
            loop._generated_notes = notes
            card_summary = result.get("title", "")
            conclusion = (result.get("conclusion") or "").strip()
            if conclusion:
                card_summary += " — " + conclusion[:40]
            return {"status": "generated", "pause": True,
                    "display": "record_generated",
                    "exp_id": result["id"],
                    "summary": card_summary,
                    "response_type": "generate", "include_state": True,
                    "id": result["id"],
                    "title": result.get("title", ""),
                    "fields_count": sum(1 for v in result.values() if v)}
        except Exception:
            preview = _build_preview(loop)
            # 子 Agent 回退也使用现有 EXP ID
            if loop.child.is_child and loop.child.exp_id:
                preview["id"] = loop.child.exp_id
            loop._generated_preview = preview
            loop._generated_notes = notes
            card_summary = preview.get("title", "")
            conclusion = (preview.get("conclusion") or "").strip()
            if conclusion:
                card_summary += " — " + conclusion[:40]
            return {"status": "generated", "pause": True,
                    "display": "record_generated",
                    "exp_id": preview["id"],
                    "summary": card_summary,
                    "response_type": "generate", "include_state": True,
                    "id": preview["id"],
                    "title": preview.get("title", ""),
                    "note": "LLM 提取失败，使用了确定性回退，部分字段可能需手动补全"}

    # -- read_update_log --

    def _read_update_log(self, args: dict, loop: "AgentLoop") -> dict:
        if not self.update_log_store:
            return {"error": "no_update_log_store", "message": "更新日志存储未配置"}
        exp_id = args["exp_id"]
        limit = args.get("limit", 5)
        entries = self.update_log_store.list_recent(exp_id, limit=limit)
        return {"status": "ok", "exp_id": exp_id, "entries": entries}

    # -- modify_experiment --

    def _modify_experiment(self, args: dict, loop: "AgentLoop") -> dict:
        refs = args.get("refs", [])
        changes = args.get("changes", {})
        if not refs:
            return {"error": "no_refs", "message": "请指定要修改的实验编号"}
        if not changes:
            return {"error": "no_changes", "message": "请指定要修改的字段"}

        results = {}
        for ref in refs:
            exp = self.store.load(ref)
            if not exp:
                results[ref] = {"error": "not_found", "message": f"实验 {ref} 不存在"}
                continue
            # 读磁盘旧值
            old_exp = dict(exp)
            # 应用 changes
            for key, value in changes.items():
                if key in ("materials", "equipment", "experimental_plan",
                          "process_parameters", "characterization"):
                    exp[key] = value  # 完整替换
                elif key in ("results", "observations"):
                    if isinstance(value, dict):
                        exp.setdefault(key, {}).update(value)
                elif key == "tags":
                    exp[key] = list(value)
                elif key == "sop" or key == "next_steps":
                    exp[key] = list(value)
                else:
                    exp[key] = value
            # 写更新日志
            from lib.services.experiment import compute_experiment_diff
            entries = compute_experiment_diff(old_exp, exp)
            if entries and self.update_log_store:
                self.update_log_store.append(
                    exp_id=ref, source="parent_agent",
                    changes=entries,
                    thread_id=loop.thread.id,
                    context={"summary": f"修改了 {len(entries)} 个字段"},
                )
            # 保存
            self.store.save(exp)
            # 注入过期标记到 history
            loop.history.append({
                "role": "system",
                "content": f"{ref} 已被修改。此前关于 {ref} 的对话陈述可能已过时。获取当前数据请使用 load_reference。"
            })
            results[ref] = {
                "status": "modified",
                "display": "diff",
                "changes": entries,
            }
        return {"modified": results}

    # -- manage_collection --

    def _manage_collection(self, args: dict, loop: "AgentLoop") -> dict:
        if not self.favorites_store:
            return {"error": "no_favorites_store", "message": "收藏存储未配置"}
        action = args["action"]
        refs = args.get("refs", [])
        collection = args.get("collection", "默认收藏夹")
        results = {}
        for ref in refs:
            if action == "pin":
                results[ref] = self.favorites_store.toggle_pin(ref)
            elif action == "unpin":
                self.favorites_store.toggle_pin(ref)  # unpin via toggle
                results[ref] = {"ok": True, "pinned": False}
            elif action == "favorite":
                results[ref] = self.favorites_store.toggle_favorite(ref, collection)
            elif action == "unfavorite":
                self.favorites_store.toggle_favorite(ref, collection)
                results[ref] = {"ok": True, "favorited": False}
        return {"status": "ok", "display": "toast",
                "message": f"已完成 {action} 操作", "results": results}

    # -- query_experiment --

    def _query_experiment(self, args: dict, loop: "AgentLoop") -> dict:
        question = args["question"]
        refs = args.get("refs", [])
        answers = []
        for ref in refs:
            # 路径 1: 检查 messages 中是否已加载
            already_loaded = False
            for m in loop.history:
                if m.get("role") == "tool":
                    try:
                        content = json.loads(m.get("content", "{}"))
                        loaded = content.get("loaded", {})
                        if ref in loaded:
                            already_loaded = True
                            break
                    except (json.JSONDecodeError, AttributeError):
                        pass
            if already_loaded:
                answers.append({
                    "exp_id": ref,
                    "answer": f"{ref} 的数据已在对话中，请参考已加载的实验信息。",
                    "source": "memory",
                })
            else:
                # 路径 2: 从磁盘加载
                exp = self.store.load(ref)
                if exp:
                    answers.append({
                        "exp_id": ref,
                        "answer": (
                            f"标题: {exp.get('title','')}。"
                            f"状态: {exp.get('status','')}。"
                            f"目的: {(exp.get('purpose') or '')[:200]}。"
                            f"结论: {(exp.get('conclusion') or '')[:200]}。"
                        ),
                        "source": "file",
                    })
                else:
                    answers.append({
                        "exp_id": ref, "answer": f"实验 {ref} 不存在", "source": "error",
                    })
        return {
            "status": "ok",
            "display": "answer",
            "question": question,
            "answer": "\n\n".join(a["answer"] for a in answers),
            "exp_ids": refs,
            "source": answers[0]["source"] if answers else "file",
        }

    # -- list_experiments --

    def _list_experiments(self, args: dict, loop: "AgentLoop") -> dict:
        all_exps = self.store.list_all_full()
        filtered = []
        status = args.get("status")
        tags = args.get("tags", [])
        experimenter = args.get("experimenter")
        since = args.get("since")

        for exp in all_exps:
            if status and exp.get("status") != status:
                continue
            if tags:
                exp_tags = [t.lower() for t in exp.get("tags", [])]
                if not any(t.lower() in exp_tags for t in tags):
                    continue
            if experimenter and exp.get("experimenter") != experimenter:
                continue
            if since and exp.get("date", "") < since:
                continue
            filtered.append({
                "id": exp.get("id"),
                "title": exp.get("title", ""),
                "date": exp.get("date", ""),
                "status": exp.get("status", ""),
                "tags": exp.get("tags", []),
            })
        return {
            "display": "list",
            "experiments": filtered[:20],
            "count": len(filtered),
        }

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
            exp = self.store.load(exp_id)
            if exp:
                if exp_id not in loop.references:
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
        result = {
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
        # 追加最近更新日志摘要
        if self.update_log_store:
            try:
                recent = self.update_log_store.list_recent(exp.get("id", ""), limit=3)
                if recent:
                    result["_recent_updates"] = [
                        {"timestamp": r.get("timestamp", ""),
                         "source": r.get("source", ""),
                         "summary": r.get("context", {}).get("summary", ""),
                         "changed_fields": [c.get("field", "") for c in r.get("changes", [])]}
                        for r in recent
                    ]
            except Exception:
                pass
        return result

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
        if loop._schema_context is None:
            return {"error": "not_in_record_mode",
                    "message": "update_schema 只在记录实验时可用。"}
        fields = args.get("fields", {})

        # 追踪 modified_values：首次触及的字段记录旧值
        for key in fields:
            if key not in loop.modified_values:
                old_val = loop._schema_context.get(key)
                if isinstance(old_val, (list, dict)):
                    loop.modified_values[key] = deepcopy(old_val)
                else:
                    loop.modified_values[key] = old_val

        merge_context(loop._schema_context, fields)

        # 推断 experiment_type（从 tags 中）
        if loop.experiment_type == "other":
            tags = loop._schema_context.get("tags", [])
            for tag in tags:
                if tag in ("photocatalysis", "hydrothermal", "sol-gel",
                           "spin-coating", "ball-milling",
                           "electrochemistry", "xrd", "perovskite-solar"):
                    loop.experiment_type = tag
                    break

        # 如果当前在 analyze 线程中，先结束它再开始 record
        if loop.thread.id:
            for m in loop.history:
                if f"thread_begin id={loop.thread.id} type=analyze" in (m.get("content") or ""):
                    loop.history.append({"role": "system",
                        "content": f"[系统内部] thread_end id={loop.thread.id}"})
                    loop.thread_store.set_active_thread(None)
                    loop.thread.id = None
                    loop.thread.type = None
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

    def __init__(self, llm_client, experiment_store, *,
                 tool_executor: "ToolExecutor | None" = None,
                 debug_dir: str | Path | None = None,
                 thread_store=None, update_log_store=None,
                 favorites_store=None, analysis_store=None,
                 analysis_svc=None, extraction_svc=None):
        self.llm = llm_client
        self.store = experiment_store
        self._schema_context = None  # 16-field dict — only non-None in record mode
        self.history = []           # [{role, content, tool_calls?, tool_call_id?}]
        self.references = []        # 已加载的引用实验 ID
        self.experiment_type = "other"
        self.turn_count = 0
        if tool_executor is not None:
            self.tools = tool_executor
        else:
            self.tools = ToolExecutor(experiment_store, update_log_store=update_log_store,
                                      favorites_store=favorites_store,
                                      analysis_store=analysis_store)
        self._generated_preview = None   # generate_record 工具产出
        self._generated_notes = None
        self._llm_call_seq = 0      # LLM 调用全局序号（跨 turn 递增）

        # 会话ID：全新启动时生成，重启时从 _current_state.yaml 恢复
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 冷存储：被压缩裁掉的消息写入 _history/{session_id}.jsonl
        _cold_dir = Path(experiment_store.path).parent / "_history"
        _cold_dir.mkdir(parents=True, exist_ok=True)
        self._cold_store_path = _cold_dir / f"{self.session_id}.jsonl"

        # 线程系统 + 子Agent 标记 + 服务引用
        self.thread_store = thread_store
        self.update_log_store = update_log_store
        self.thread = ThreadState()
        self.child = ChildContext()
        self.modified_values = {}
        self._l0_generated_at = None
        self.analysis_svc = analysis_svc
        self.extraction_svc = extraction_svc

        # 注入 L0 全局摘要（实验库概况、常用标签等）
        if thread_store:
            l0 = thread_store.build_global_summary(experiment_store, update_log_store)
            self._l0_generated_at = getattr(thread_store, 'l0_generated_at', None)
            self.history.append({
                "role": "system", "content": f"[全局上下文]\n{l0}"
            })

        # 调试目录：新会话创建新目录，恢复会话复用已有路径
        if debug_dir:
            self.debug_dir = Path(debug_dir)
        else:
            self.debug_dir = (
                Path(experiment_store.path) / "_debug" /
                datetime.now().strftime("%Y%m%d_%H%M%S")
            )
            os.makedirs(self.debug_dir, exist_ok=True)

        # 清理超过 30 天的旧 _debug 目录
        _cleanup_old_debug_dirs(Path(experiment_store.path) / "_debug")

    # -- 模式管理 --

    @property
    def mode(self) -> str:
        """当前对话模式: 'general' | 'record' | 'analyze'"""
        if not self.thread.id:
            return "general"
        return self.thread.type or "general"

    def _enter_record_mode(self) -> None:
        """初始化 Schema 上下文（仅 record 模式）。"""
        self._schema_context = deepcopy(DEFAULT_CONTEXT)

    def _exit_record_mode(self) -> None:
        """清理 Schema 上下文。"""
        self._schema_context = None

    def _get_active_tools(self) -> list[dict]:
        """返回当前模式或子 Agent 角色可用的工具列表。"""
        # 子 Agent 角色优先 —— 返回固定工具清单，不走 mode 推断
        if self.child.agent_role == "analysis_reviewer":
            return [
                TOOL_LOAD_REFERENCE, TOOL_SEARCH_EXPERIMENTS,
                TOOL_QUERY_EXPERIMENT, TOOL_LIST_EXPERIMENTS,
                TOOL_READ_UPDATE_LOG, TOOL_MODIFY_ANALYSIS, TOOL_END_THREAD,
            ]
        if self.child.agent_role == "exp_editor":
            return [
                TOOL_LOAD_REFERENCE, TOOL_SEARCH_EXPERIMENTS,
                TOOL_QUERY_EXPERIMENT, TOOL_LIST_EXPERIMENTS,
                TOOL_READ_UPDATE_LOG, TOOL_MODIFY_EXPERIMENT, TOOL_END_THREAD,
            ]

        # 父 Agent —— 按模式返回
        common = [
            TOOL_LOAD_REFERENCE, TOOL_SEARCH_EXPERIMENTS, TOOL_QUERY_EXPERIMENT,
            TOOL_LIST_EXPERIMENTS, TOOL_MANAGE_COLLECTION, TOOL_READ_UPDATE_LOG,
            TOOL_END_THREAD,
        ]
        if self.mode == "record":
            common.extend([TOOL_START_RECORD_THREAD, TOOL_UPDATE_SCHEMA,
                          TOOL_ASK_USER, TOOL_GENERATE_RECORD, TOOL_MODIFY_EXPERIMENT])
        elif self.mode == "general":
            common.extend([TOOL_START_RECORD_THREAD, TOOL_START_ANALYZE_THREAD,
                          TOOL_MODIFY_EXPERIMENT])
        elif self.mode == "analyze":
            # analyze 模式不包含 modify_experiment —— 分析者不应修改实验
            common.extend([TOOL_START_ANALYZE_THREAD, TOOL_SELECT_EXPERIMENTS,
                          TOOL_ASK_USER, TOOL_GENERATE_ANALYSIS])
        return common

    # -- 主循环 --

    def run(self, user_message: str = "") -> dict:
        """处理一条用户消息。返回 {type, message?, context}"""
        log = get_logger()
        if user_message:
            self.thread.current_turn_user_idx = len(self.history)
            self.history.append({"role": "user", "content": user_message})
            self.turn_count += 1
            if log:
                agent = "child" if self.child.is_child else "parent"
                log.agent(agent, "user", user_message, exp=self.child.exp_id)

        consecutive_errors = 0
        last_tool = None
        _no_progress_count = 0  # Track rounds without update_schema/analyze

        while True:
            self._maybe_inject_thread_start()   # 循环顶部检查 flag

            # 构建 LLM 消息：静态 Prompt → 历史摘要 → history → 请求层状态
            messages = [
                {"role": "system", "content": build_system_prompt()},
            ]
            if self.thread_store:
                summary = self.thread_store.get_global_context()
                if summary:
                    messages.append({"role": "system", "content": f"[历史摘要]\n{summary}"})
            messages.extend(self.history)
            # 请求层：record 模式下追加实时 Schema 状态
            if self.mode == "record" and self._schema_context is not None:
                messages.append({"role": "system",
                                "content": self._build_schema_status()})
            # 请求层：追加线程状态（始终在末尾）
            messages.append({"role": "system",
                            "content": self._build_thread_status()})
            self._llm_call_seq += 1
            seq = self._llm_call_seq

            # ---- 日志: LLM 请求 ----
            self._log_llm_request(seq, messages)

            try:
                response = self.llm.chat(
                    messages=messages,
                    tools=self._get_active_tools(),
                    temperature=0.3,
                    reasoning_effort="max",
                )
            except Exception as e:
                self._log_llm_response(seq, LLMResponse(content=f"[ERROR] {e}"), "")
                # 回退 history 到最近一条 user 消息，清理其后的所有残留
                cut = len(self.history)
                for i in range(len(self.history) - 1, -1, -1):
                    if self.history[i].get("role") == "user":
                        cut = i + 1
                        break
                del self.history[cut:]
                self.turn_count = sum(1 for m in self.history if m.get("role") == "user")
                self.history.append({"role": "system",
                    "content": f"[系统内部] LLM 调用失败（已重试3次）: {str(e)[:200]}"})
                if log:
                    log.system("error", "llm_call_failed", error=str(e)[:200])
                    agent = "child" if self.child.is_child else "parent"
                    log.agent(agent, "assistant",
                        "抱歉，AI 服务暂时不可用（已自动重试3次）。请稍后重试。",
                        exp=self.child.exp_id)
                self._save_runtime_state()
                return {"type": "reply",
                        "message": "抱歉，AI 服务暂时不可用（已自动重试3次）。请稍后重试。",
                        "context": self._schema_context}

            resp_content = response.content
            resp_tool_calls = response.tool_calls
            _reasoning = response.reasoning

            # ---- 日志: LLM 响应 ----
            self._log_llm_response(seq, response, _reasoning)

            # 纯文本 → 不再调工具，直接返回
            if resp_content and not resp_tool_calls:
                entry = {"role": "assistant", "content": resp_content}
                if _reasoning:
                    entry["reasoning_content"] = _reasoning
                self.history.append(entry)
                if log:
                    agent = "child" if self.child.is_child else "parent"
                    log.agent(agent, "assistant", resp_content, exp=self.child.exp_id)
                self._maybe_inject_thread_start()   # return 前检查
                self._check_thread_cancellation(_no_progress_count)
                self._save_runtime_state()
                return {"type": "reply", "message": resp_content,
                        "context": self._schema_context}

            # 调用了工具
            # 记录 assistant 文本（工具调用前的说明文字，只记一次）
            if log and resp_content:
                ag = "child" if self.child.is_child else "parent"
                tc_names = [tc["function"]["name"] for tc in (resp_tool_calls or [])]
                log.agent(ag, "assistant", resp_content, tool_calls=tc_names, exp=self.child.exp_id)

            has_record_tool = False
            for tc in (resp_tool_calls or []):
                name = tc["function"]["name"]
                if name in ("update_schema", "analyze"):
                    has_record_tool = True
                raw_args_str = tc["function"]["arguments"]

                # ---- 日志: tool 调用入参 ----
                self._log_tool_call(seq, name, raw_args_str)

                args = json.loads(raw_args_str)
                result = self.tools.execute(name, args, self)

                # ---- 日志: tool 执行结果 ----
                self._log_tool_result(seq, name, result)

                # 统一日志系统：记录工具调用
                if log:
                    ag = "child" if self.child.is_child else "parent"
                    ok = "error" not in result
                    kw = _tool_log_summary(name, args, result)
                    log.tool(ag, name, ok, exp=self.child.exp_id, **kw)

                # 将工具调用转为可序列化的 dict
                tc_dict = {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": raw_args_str,
                    },
                }
                entry = {
                    "role": "assistant",
                    "content": resp_content or None,
                    "tool_calls": [tc_dict],
                }
                if _reasoning:
                    entry["reasoning_content"] = _reasoning
                self.history.append(entry)
                self.history.append({
                    "role": "tool", "tool_call_id": tc["id"],
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
                        self._maybe_inject_thread_start()
                        self._save_runtime_state()
                        return {"type": "reply",
                                "message": "抱歉，处理请求时遇到技术问题。请换个方式描述。",
                                "context": self._schema_context}
                else:
                    consecutive_errors = 0
                    last_tool = None

                # 统一暂停检查：工具返回 pause 标记时停止循环
                if result.get("pause"):
                    self._maybe_inject_thread_start()
                    if not has_record_tool and self.thread.id:
                        _no_progress_count += 1
                    self._check_thread_cancellation(_no_progress_count)

                    if name == "generate_record":
                        if self.thread.id and not self.child.is_child:
                            exp_id = self._generated_preview.get("id", "") if self._generated_preview else ""
                            self._maybe_inject_thread_end(exp_id)
                        if self._generated_preview is None:
                            return {"type": "reply",
                                    "message": "生成失败，请重试或补充更多信息。",
                                    "context": self._schema_context}
                        self._save_runtime_state()
                        return {"type": result.get("response_type", "generate"),
                                "message": "实验记录已生成，请在预览中确认。",
                                "state": self.state_to_dict() if result.get("include_state") else None,
                                "preview": self._generated_preview,
                                "notes": self._generated_notes,
                                "context": self._schema_context}

                    self._save_runtime_state()
                    message = result.get("message") or resp_content or ""
                    if name == "ask_user":
                        questions = "\n".join(
                            f"{i+1}. {q}" for i, q in enumerate(args.get("questions", []))
                        )
                        message = (resp_content + "\n\n" + questions) if resp_content else questions
                    return {"type": "reply",
                            "message": message or "请在面板中选择实验。",
                            "context": self._schema_context}

            # 更新无进展计数
            if not has_record_tool and self.thread.id:
                _no_progress_count += 1
            else:
                _no_progress_count = 0

            # 其他工具执行完 → 继续循环

    # -- 流式主循环 --

    def run_stream(self, user_message: str = "") -> Generator[dict[str, Any], None, None]:
        """和 run() 逻辑相同，但通过 Generator yield SSE 事件实现流式输出。"""
        from lib.llm import StreamEvent

        log = get_logger()
        if user_message:
            self.thread.current_turn_user_idx = len(self.history)
            self.history.append({"role": "user", "content": user_message})
            self.turn_count += 1
            if log:
                agent = "child" if self.child.is_child else "parent"
                log.agent(agent, "user", user_message, exp=self.child.exp_id)

        consecutive_errors = 0
        last_tool = None
        _no_progress_count = 0

        while True:
            self._maybe_inject_thread_start()

            messages = [{"role": "system", "content": build_system_prompt()}]
            if self.thread_store:
                summary = self.thread_store.get_global_context()
                if summary:
                    messages.append({"role": "system", "content": f"[历史摘要]\n{summary}"})
            messages.extend(self.history)
            if self.mode == "record" and self._schema_context is not None:
                messages.append({"role": "system", "content": self._build_schema_status()})
            messages.append({"role": "system", "content": self._build_thread_status()})
            self._llm_call_seq += 1
            seq = self._llm_call_seq
            self._log_llm_request(seq, messages)

            try:
                stream = self.llm.chat_stream(
                    messages=messages,
                    tools=self._get_active_tools(),
                    temperature=0.3,
                    reasoning_effort="max",
                )
            except Exception as e:
                self._log_llm_response(seq, LLMResponse(content=f"[ERROR] {e}"), "")
                cut = len(self.history)
                for i in range(len(self.history) - 1, -1, -1):
                    if self.history[i].get("role") == "user":
                        cut = i + 1
                        break
                del self.history[cut:]
                self.turn_count = sum(1 for m in self.history if m.get("role") == "user")
                self.history.append({"role": "system",
                    "content": f"[系统内部] LLM 调用失败: {str(e)[:200]}"})
                self._save_runtime_state()
                yield {"event": "error", "message": "LLM 调用失败，请稍后重试。"}
                return

            resp_content = ""
            resp_tool_calls = None
            _reasoning = ""
            current_tool = ""

            # 消费流事件
            try:
                while True:
                    try:
                        event = next(stream)
                    except StopIteration as exc:
                        resp = exc.value
                        resp_content = resp.content
                        resp_tool_calls = resp.tool_calls
                        _reasoning = resp.reasoning
                        break

                    if event.type == "text":
                        yield {"event": "text", "content": event.content}
                    elif event.type == "tool_call":
                        if event.tool_name and event.tool_name != current_tool:
                            current_tool = event.tool_name
                            yield {"event": "tool", "name": current_tool}
            except Exception as e:
                yield {"event": "error", "message": str(e)[:200]}
                return

            self._log_llm_response(seq,
                LLMResponse(content=resp_content, reasoning=_reasoning, tool_calls=resp_tool_calls), _reasoning)

            # 纯文本 → 返回
            if resp_content and not resp_tool_calls:
                entry = {"role": "assistant", "content": resp_content}
                if _reasoning:
                    entry["reasoning_content"] = _reasoning
                self.history.append(entry)
                self._check_thread_cancellation(_no_progress_count)
                self._save_runtime_state()
                yield {"event": "done", "type": "reply", "message": resp_content,
                       "context": self._schema_context}
                return

            # 调用了工具
            if log and resp_content:
                ag = "child" if self.child.is_child else "parent"
                tc_names = [tc["function"]["name"] for tc in (resp_tool_calls or [])]
                log.agent(ag, "assistant", resp_content, tool_calls=tc_names, exp=self.child.exp_id)

            has_record_tool = False
            for tc in (resp_tool_calls or []):
                name = tc["function"]["name"]
                if name in ("update_schema", "analyze"):
                    has_record_tool = True
                raw_args_str = tc["function"]["arguments"]
                args = json.loads(raw_args_str)
                result = self.tools.execute(name, args, self)
                yield {"event": "tool_done", "name": name}

                if log:
                    ag = "child" if self.child.is_child else "parent"
                    ok = "error" not in result
                    kw = _tool_log_summary(name, args, result)
                    log.tool(ag, name, ok, exp=self.child.exp_id, **kw)

                tc_dict = {
                    "id": tc["id"], "type": "function",
                    "function": {"name": name, "arguments": raw_args_str},
                }
                entry = {"role": "assistant", "content": resp_content or None, "tool_calls": [tc_dict]}
                if _reasoning:
                    entry["reasoning_content"] = _reasoning
                self.history.append(entry)
                self.history.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })

                if "error" in result:
                    if name == last_tool:
                        consecutive_errors += 1
                    else:
                        consecutive_errors = 1
                        last_tool = name
                    if consecutive_errors >= 3:
                        self.history.append({"role": "assistant",
                            "content": "抱歉，处理请求时遇到技术问题。"})
                        self._save_runtime_state()
                        yield {"event": "done", "type": "reply",
                               "message": "抱歉，处理请求时遇到技术问题。"}
                        return
                else:
                    consecutive_errors = 0
                    last_tool = None

                # pause 处理
                if result.get("pause"):
                    if name == "generate_record":
                        if self.thread.id and not self.child.is_child:
                            exp_id = self._generated_preview.get("id", "") if self._generated_preview else ""
                            self._maybe_inject_thread_end(exp_id)
                        if self._generated_preview is None:
                            self._save_runtime_state()
                            yield {"event": "done", "type": "reply",
                                   "message": "生成失败，请重试或补充更多信息。"}
                            return
                        self._save_runtime_state()
                        yield {"event": "done",
                               "type": "generate",
                               "message": "实验记录已生成，请在预览中确认。",
                               "preview": self._generated_preview,
                               "notes": self._generated_notes}
                        return

                    self._save_runtime_state()
                    message = result.get("message") or resp_content or ""
                    if name == "ask_user":
                        questions = "\n".join(
                            f"{i+1}. {q}" for i, q in enumerate(args.get("questions", [])))
                        message = (resp_content + "\n\n" + questions) if resp_content else questions
                    yield {"event": "done", "type": "reply",
                           "message": message or "请在面板中选择实验。"}
                    return

            if not has_record_tool and self.thread.id:
                _no_progress_count += 1
            else:
                _no_progress_count = 0

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
            val = self._schema_context.get(key) if self._schema_context else None
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
        ctx = self._schema_context or {}
        parts = []
        if ctx.get("title"):
            parts.append(f"实验标题: {ctx['title']}")
        if ctx.get("date"):
            parts.append(f"日期: {ctx['date']}")
        if ctx.get("experimenter"):
            parts.append(f"实验者: {ctx['experimenter']}")
        tags = ctx.get("tags", [])
        if tags:
            parts.append(f"标签: {', '.join(str(t) for t in tags)}")
        status_val = ctx.get("status", "")
        if status_val and status_val != "planned":
            status_cn = {"planned": "计划中", "running": "进行中", "done": "已完成",
                         "failed": "失败", "repeated": "重复"}.get(status_val, status_val)
            parts.append(f"状态: {status_cn}")
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
        exp_plan = ctx.get("experimental_plan", [])
        if exp_plan:
            lines = ["实验方案:"]
            for i, p in enumerate(exp_plan, 1):
                if isinstance(p, dict):
                    group = p.get("group", "")
                    condition = p.get("condition", "")
                    expected = f", 预期{p.get('expected', '')}" if p.get("expected") else ""
                    lines.append(f"  {i}. 组'{group}': {condition}{expected}")
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
        return all(_is_filled((self._schema_context or {}).get(f)) for f in core)

    # -- 线程系统 --

    def _l0_stale(self) -> bool:
        """L0 摘要是否过期（距上次生成超过 1 小时）。"""
        if self._l0_generated_at is None:
            return True
        if not isinstance(self._l0_generated_at, datetime):
            try:
                self._l0_generated_at = datetime.fromisoformat(str(self._l0_generated_at))
            except (ValueError, TypeError):
                return True
        return (datetime.now() - self._l0_generated_at).total_seconds() > 3600

    def _refresh_l0(self) -> None:
        """重新生成 L0 摘要并替换 history[0]（如果 history[0] 是 L0）。"""
        if not self.thread_store:
            return
        l0 = self.thread_store.build_global_summary(self.store, self.update_log_store)
        self._l0_generated_at = getattr(self.thread_store, 'l0_generated_at', datetime.now())
        # 替换或插入 L0
        if self.history and "[全局上下文]" in (self.history[0].get("content") or ""):
            self.history[0]["content"] = f"[全局上下文]\n{l0}"
        else:
            self.history.insert(0, {"role": "system", "content": f"[全局上下文]\n{l0}"})

    def _build_thread_guidance(self, thread_type: str) -> dict:
        """生成线程模式引导消息。"""
        if thread_type == "record":
            return {"role": "system",
                    "content": "你正在记录一条新实验。优先收集材料、步骤、参数、结果。追问缺失的关键字段。目标：generate_record。"}
        elif thread_type == "analyze":
            return {"role": "system",
                    "content": "你正在进行跨实验分析。先了解用户需求，用 search_experiments 或 list_experiments 缩小范围，用 select_experiments 让用户勾选实验，用 load_reference 加载数据，用 ask_user 确认分析角度。需求明确后调用 generate_analysis 执行分析并归档。"}
        return {"role": "system", "content": ""}

    def _build_thread_status(self) -> str:
        """生成当前线程状态声明。每轮 LLM 请求注入，不入 history。"""
        # 子 Agent 角色覆盖 —— 不依赖 _thread_type
        if self.child.agent_role == "analysis_reviewer":
            return (
                "[系统状态] 你正在审阅/修改一份已完成的分析报告。"
                "可用工具：load_reference（查看报告中引用的实验）、search_experiments、"
                "read_update_log、modify_analysis（修改报告内容）。"
                "不要使用 start_analyze_thread、select_experiments、generate_analysis"
                "——这些属于分析创建阶段，不是报告审阅阶段。"
            )
        if self.child.agent_role == "exp_editor":
            return (
                "[系统状态] 你正在修改已完成的实验。"
                "修改前先用 load_reference 加载磁盘最新数据（不要依赖对话记忆）。"
                "修改用 modify_experiment 工具直接执行，会自动保存和记录日志。"
                "不要用 update_schema 或 generate_record。"
            )

        if not self.thread.id:
            return (
                "[系统状态] 自由模式。"
                "你可回答查询、管理收藏、闲聊。"
                "用户要记录新实验时调用 start_record_thread，"
                "要跨实验分析时调用 start_analyze_thread。"
            )
        if self.thread.type == "record":
            return (
                "[系统状态] record 线程进行中。"
                "持续收集实验信息，缺失关键字段时追问。目标：generate_record。"
            )
        if self.thread.type == "analyze":
            return (
                "[系统状态] analyze 线程进行中。"
                "深入讨论，使用 search_experiments + load_reference + 自身推理。"
                "目标：输出分析报告。"
            )
        return "[系统状态] 自由模式。"

    def _maybe_inject_thread_start(self) -> None:
        """analyze 工具触发时注入线程标记。record 线程由 start_record_thread 工具直接处理。"""
        if not self.thread.pending_start or not self.thread_store:
            return
        thread_type = self.thread.pending_start
        self.thread.pending_start = None
        thread_id = self.thread_store.next_id()
        self.thread.id = thread_id
        self.thread.type = thread_type
        self.thread_store.set_active_thread(thread_id)
        if thread_type == "record":
            self._enter_record_mode()
        begin = {"role": "system", "content": f"[系统内部] thread_begin id={thread_id} type={thread_type}"}
        insert_pos = self.thread.current_turn_user_idx + 1
        self.history.insert(insert_pos, begin)
        guidance = self._build_thread_guidance(thread_type)
        if guidance.get("content"):
            self.history.insert(insert_pos + 1, guidance)
        self.thread_store.create(thread_type, [begin, guidance] if guidance.get("content") else [begin])
        log = get_logger()
        if log:
            log.operation("thread_start", agent="parent", thread=thread_id, type=thread_type)

    def _maybe_inject_thread_end(self, produced_id: str) -> None:
        """注入线程结束标记 + 提取 messages → 写线程文件 + 更新索引 + 重置上下文。"""
        if not self.thread.id or not self.thread_store:
            return
        end = {"role": "system",
               "content": f"[系统内部] thread_end id={self.thread.id} product={produced_id}"}
        self.history.append(end)
        self._extract_and_save_thread(produced_id)
        self.thread_store.set_active_thread(None)
        # 统一日志
        log = get_logger()
        if log:
            agent = "child" if self.child.is_child else "parent"
            log.operation("thread_end", agent=agent, thread=self.thread.id, produced=produced_id)
        # 记录刚结束的线程ID，压缩时跳过它
        self.thread.last_ended_id = self.thread.id
        self.thread.id = None
        self.thread.type = None
        # 清理 Schema 状态和引用
        self._exit_record_mode()
        self.references = []
        self.experiment_type = "other"
        self.modified_values = {}

    def _extract_and_save_thread(self, produced_id: str) -> None:
        """提取 begin-end 标记间的 messages → 写入线程文件 + 更新索引。"""
        tid = self.thread.id
        # 找到 begin 标记位置
        begin_idx = None
        end_idx = None
        for i, m in enumerate(self.history):
            content = m.get("content") or ""
            if f"thread_begin id={tid}" in content:
                begin_idx = i
            elif f"thread_end id={tid}" in content:
                end_idx = i
                break
        if begin_idx is None or end_idx is None:
            return
        # 提取区间 messages（从触发用户消息开始，它位于 begin 标记之前）
        thread_msgs = self.history[begin_idx - 1:end_idx + 1]
        # 更新线程文件
        thread = self.thread_store.load(tid)
        if thread:
            thread["messages"] = thread_msgs
            thread["status"] = "done"
            thread["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if produced_id.startswith("EXP-") and not thread.get("exp_generated"):
                thread["exp_generated"] = produced_id
                # 从首个 user 消息截取标题
                for m in thread_msgs:
                    if m.get("role") == "user":
                        first_user = m.get("content") or ""[:30]
                        thread["title"] = first_user
                        break
                thread["summary"] = f"生成{produced_id}"
            elif produced_id.startswith("ANAL-"):
                thread["anal_generated"] = produced_id
            # Title: if >= 3 turns, generate with LLM later (simplified for now)
            self.thread_store.save(thread)
            self.thread_store.update_index(thread)
        # 更新用户画像 + L0
        if produced_id.startswith("EXP-") and self.thread_store:
            exp = self.store.load(produced_id)
            if exp:
                self.thread_store.update_user_profile(exp)
                self.thread_store.recalc_tag_counts(self.store)
            self._refresh_l0()

    def _check_thread_cancellation(self, consecutive_no_progress: int) -> None:
        """检测线程是否需要取消。返回更新后的 consecutive_no_progress。"""
        if not self.thread.id:
            return
        # 简单实现：如果连续 3 轮无进展，自动取消
        # 注意：调用方负责维护 consecutive_no_progress 计数
        if consecutive_no_progress >= 3:
            tid = self.thread.id
            self.history.append({"role": "system",
                "content": f"[系统内部] thread_cancelled id={tid}"})
            # 移除 begin 标记
            for i, m in enumerate(self.history):
                if f"thread_begin id={tid}" in (m.get("content") or ""):
                    self.history.pop(i)
                    # 同时移除紧跟的引导消息
                    if i < len(self.history) and self.history[i].get("role") == "system":
                        content = self.history[i].get("content") or ""
                        if "正在记录" in content or "正在进行" in content:
                            self.history.pop(i)
                    break
            self.thread.id = None
            self.thread.type = None
            self._exit_record_mode()
            self.modified_values = {}
            log = get_logger()
            if log:
                agent = "child" if self.child.is_child else "parent"
                log.operation("thread_cancelled", agent=agent, thread=tid)

    # -- 子 Agent --

    @classmethod
    def create_child_agent(cls, parent_loop: "AgentLoop", thread_id: str) -> "AgentLoop":
        """从父 Agent 创建子 Agent，用于续接历史线程（修改已完成的实验）。"""
        thread = parent_loop.thread_store.load(thread_id)
        if not thread:
            raise ValueError(f"Thread {thread_id} not found")

        child = cls(
            parent_loop.llm,
            parent_loop.store,
            debug_dir=parent_loop.debug_dir,
            thread_store=parent_loop.thread_store,
            update_log_store=parent_loop.update_log_store,
            favorites_store=getattr(parent_loop.tools, 'favorites_store', None),
            analysis_store=getattr(parent_loop.tools, 'analysis_store', None),
        )
        # 子 Agent 上下文: L0 + 线程完整 messages（LLM 参考用）
        child.history = list(child.history)  # keep L0
        for m in thread.get("messages", []):
            if m.get("role") != "system" or "[全局上下文]" not in (m.get("content") or ""):
                child.history.append(dict(m))
        # 记录初始 history 长度——前端只渲染此索引之后的消息
        child.child.initial_history_len = len(child.history)
        child.thread.id = thread_id
        child.child.is_child = True
        child.child.agent_role = "exp_editor"
        return child

    @classmethod
    def create_legacy_child_agent(cls, llm_client, store, exp_data: dict,
                                   thread_store=None, update_log_store=None,
                                   favorites_store=None,
                                   analysis_store=None) -> "AgentLoop":
        """为无线程关联的旧实验创建子 Agent。初始上下文: L0 + EXP 结构化数据。"""
        child = cls(llm_client, store,
                    thread_store=thread_store,
                    update_log_store=update_log_store,
                    favorites_store=favorites_store,
                    analysis_store=analysis_store)
        # 注入 EXP 数据作为上下文
        child.history.append({
            "role": "system",
            "content": f"[当前实验数据]\n{json.dumps(exp_data, ensure_ascii=False, indent=2)}"
        })
        child.child.is_child = True
        child.child.is_legacy = True
        child.child.agent_role = "exp_editor"
        return child

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

    def _log_llm_response(self, seq: int, response, reasoning: str = "") -> None:
        """保存 LLM 的原始响应（含 content、reasoning_content 和 tool_calls）"""
        try:
            data = {}
            if reasoning:
                data["reasoning_content"] = reasoning[:3000]
            if response.content:
                data["content"] = response.content[:3000]
            if response.tool_calls:
                data["tool_calls"] = response.tool_calls  # already dict format
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



    def _save_runtime_state(self) -> None:
        """保存 AgentLoop 运行时状态。父 Agent 写 _current_state.yaml；子 Agent 写 child_state.yaml（不碰 _current_state.yaml）。"""
        if not self.thread_store:
            return
        try:
            if self.child.is_child:
                # 子 Agent: 写独立 child_state.yaml，绝不覆盖父 Agent 的 _current_state.yaml
                key = self.thread.id or self.child.exp_id
                if key:
                    self.thread_store.save_child_state(key, self.state_to_dict())
            else:
                # 父 Agent: 写 _current_state.yaml
                self.thread_store.save_current_state(self.state_to_dict())
        except Exception:
            pass
        # 子 Agent 不触发摘要
        if not self.child.is_child:
            self._maybe_summarize()

    # -- 上下文窗口管理 --

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """估算 token 数。中文/CJK ~1 char/token，英文 ~4 char/token。"""
        ideograph = 0
        ascii_chars = 0
        for c in text:
            cp = ord(c)
            if cp < 128:
                ascii_chars += 1
            elif (0x4E00 <= cp <= 0x9FFF       # CJK 基本区
                  or 0x3400 <= cp <= 0x4DBF     # CJK 扩展 A
                  or 0x20000 <= cp <= 0x2A6DF   # CJK 扩展 B
                  or 0xF900 <= cp <= 0xFAFF     # CJK 兼容汉字
                  or 0x3000 <= cp <= 0x303F     # CJK 标点
                  or 0xFF00 <= cp <= 0xFFEF     # 全角标点
                  or 0xAC00 <= cp <= 0xD7AF     # 韩文
                  or 0x3040 <= cp <= 0x309F     # 日文平假名
                  or 0x30A0 <= cp <= 0x30FF     # 日文片假名
            ):
                ideograph += 1
        other = len(text) - ideograph - ascii_chars
        return int(ideograph * 1.2 + ascii_chars * 0.25 + other * 0.8)

    def _maybe_summarize(self) -> None:
        """新增消息超过 30 万 token 时，压缩旧消息、写入冷存储、裁剪 history。"""
        if not self.thread_store:
            return
        new_msgs = self.history
        new_chars = "".join(m.get("content") or "" for m in new_msgs)
        if self._estimate_tokens(new_chars) < 300_000:
            return
        # 保留最近 10 万 token 完整，其余进摘要
        keep = 0; keep_chars_acc = ""
        for m in reversed(new_msgs):
            keep_chars_acc = (m.get("content") or "") + keep_chars_acc
            keep += 1
            if self._estimate_tokens(keep_chars_acc) >= 100_000:
                break
        to_summarize = new_msgs[:-keep] if keep < len(new_msgs) else []
        if not to_summarize:
            return
        # 尝试 LLM 压缩；失败则保留完整 history，下次再试
        import json as _json
        try:
            text = "\n".join(
                f"[{m['role']}] {(m.get('content') or '')[:300]}"
                for m in to_summarize[-600:]
            )
            raw = self.llm.analyze(
                system_prompt="你是对话摘要助手。将以下对话压缩为 500-2000 字的摘要，保留实验记录、修改操作、关键决策。只摘要以下提供的对话，不要引用外部信息。用中文。",
                user_prompt=f"请摘要以下对话：\n\n{text[:15000]}",
                temperature=0.2
            )
            new_summary = raw[:3000]
        except Exception:
            return  # 压缩失败：history 完好，不裁剪，不写冷存储，下次再试
        # 压缩成功：冷存储写入 + 裁剪 history + 追加摘要
        with open(self._cold_store_path, "a", encoding="utf-8") as f:
            for m in to_summarize:
                f.write(_json.dumps(m, ensure_ascii=False) + "\n")
        self.history = new_msgs[-keep:]
        prev = self.thread_store.get_global_context()
        combined = f"{prev}\n\n---\n\n{new_summary}" if prev else new_summary
        self.thread_store.update_global_context(combined,
            uncompressed_thread_ids=[self.thread.id] if self.thread.id else [])

    # -- 状态序列化 --

    def state_to_dict(self) -> dict:
        return {
            "context": self._schema_context,
            "references": self.references,
            "experiment_type": self.experiment_type,
            "turn_count": self.turn_count,
            "llm_call_seq": self._llm_call_seq,
            "history": [
                {k: v for k, v in m.items() if v is not None}
                for m in self.history
            ],
            "debug_dir": str(self.debug_dir),
            "thread_id": self.thread.id,
            "_thread_type": self.thread.type,
            "_pending_thread_start": self.thread.pending_start,
            "_current_turn_user_idx": self.thread.current_turn_user_idx,
            "modified_values": dict(self.modified_values),
            "_l0_generated_at": str(self._l0_generated_at) if self._l0_generated_at else None,
            "_session_id": self.session_id,
            "_is_child_agent": self.child.is_child,
            "_is_legacy": self.child.is_legacy,
            "_child_exp_id": self.child.exp_id,
            "_child_initial_history_len": self.child.initial_history_len,
            "_child_agent_role": self.child.agent_role,
        }

    @classmethod
    def from_dict(cls, llm_client, store, data: dict,
                  thread_store=None, update_log_store=None,
                  favorites_store=None, analysis_store=None,
                  analysis_svc=None, extraction_svc=None) -> "AgentLoop":
        loop = cls(llm_client, store, debug_dir=data.get("debug_dir") or None,
                   thread_store=thread_store, update_log_store=update_log_store,
                   favorites_store=favorites_store, analysis_store=analysis_store,
                   analysis_svc=analysis_svc, extraction_svc=extraction_svc)
        # 向后兼容：旧的 context 可能为空 dict（不是 None），按 None 处理
        ctx = data.get("context")
        if ctx and any(_is_filled(v) for v in ctx.values()):
            loop._schema_context = ctx
        else:
            loop._schema_context = None
        loop.references = data.get("references", [])
        loop.experiment_type = data.get("experiment_type", "other")
        loop.turn_count = data.get("turn_count", 0)
        loop._llm_call_seq = data.get("llm_call_seq", 0)
        loop.history = data.get("history", [])
        loop.thread.id = data.get("thread_id")
        loop.thread.type = data.get("_thread_type")
        # 验证磁盘上线程是否仍活跃（可能已被其他进程或手动操作结束）
        if loop.thread.id and thread_store:
            thread = thread_store.load(loop.thread.id)
            if not thread or thread.get("status") != "active":
                loop.thread.id = None
                loop.thread.type = None
        loop.thread.pending_start = data.get("_pending_thread_start")
        loop.thread.current_turn_user_idx = data.get("_current_turn_user_idx", -1)
        loop.modified_values = data.get("modified_values", {})
        loop._l0_generated_at = data.get("_l0_generated_at")
        loop.session_id = data.get("_session_id", loop.session_id)
        # 冷存储路径：保持和 session_id 一致
        _cold_dir = Path(store.path).parent / "_history"
        _cold_dir.mkdir(parents=True, exist_ok=True)
        loop._cold_store_path = _cold_dir / f"{loop.session_id}.jsonl"
        loop.child.is_child = data.get("_is_child_agent", False)
        loop.child.is_legacy = data.get("_is_legacy", False)
        loop.child.exp_id = data.get("_child_exp_id")
        loop.child.initial_history_len = data.get("_child_initial_history_len", 0)
        loop.child.agent_role = data.get("_child_agent_role")

        # L0 摘要超过 1 小时 → 重新生成
        if thread_store and loop._l0_stale():
            loop._refresh_l0()

        return loop
