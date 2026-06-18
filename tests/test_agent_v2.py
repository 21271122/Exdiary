"""AgentLoop 单元测试 + 模块级函数测试。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from lib.agent_v2 import (
    AgentLoop,
    merge_context,
    _is_filled,
    _brief,
    _build_preview,
    _extract_thread_dialogue,
    _tool_log_summary,
    ChildContext,
    ThreadState,
)
from lib.core.schema import DEFAULT_CONTEXT
from tests.conftest import (
    MockLLMClient,
    make_text_response,
    make_tool_response,
    make_agent_loop,
)


# ============================================================
# Task 2.3: 模块级函数测试
# ============================================================


class TestMergeContext:
    def test_simple_field_overwrite(self) -> None:
        ctx = {"title": "", "purpose": ""}
        merge_context(ctx, {"title": "新标题"})
        assert ctx["title"] == "新标题"
        assert ctx["purpose"] == ""

    def test_list_append_dedup(self) -> None:
        ctx = {"tags": [], "sop": []}
        merge_context(ctx, {"tags": ["a", "b", "a"], "sop": ["s1"]})
        assert ctx["tags"] == ["a", "b"]
        merge_context(ctx, {"tags": ["a", "c"]})
        assert ctx["tags"] == ["a", "b", "c"]

    def test_empty_list_clears(self) -> None:
        ctx = {"tags": ["a", "b"]}
        merge_context(ctx, {"tags": []})
        assert ctx["tags"] == []

    def test_nested_dict_merge(self) -> None:
        ctx = {"results": {"qualitative": "", "key_data": []}}
        merge_context(ctx, {"results": {"qualitative": "good"}})
        assert ctx["results"]["qualitative"] == "good"
        merge_context(ctx, {"results": {"qualitative": "better"}})
        assert ctx["results"]["qualitative"] == "better"

    def test_nested_list_append(self) -> None:
        ctx = {"results": {"key_data": [{"a": 1}]}}
        merge_context(ctx, {"results": {"key_data": [{"b": 2}]}})
        assert len(ctx["results"]["key_data"]) == 2

    def test_empty_dict_clears(self) -> None:
        ctx = {"results": {"qualitative": "old"}}
        merge_context(ctx, {"results": {}})
        assert ctx["results"] == {}

    def test_unknown_key_ignored(self) -> None:
        ctx = {"title": ""}
        merge_context(ctx, {"nonexistent": "value"})
        assert "nonexistent" not in ctx


class TestIsFilled:
    def test_none(self) -> None:
        assert not _is_filled(None)

    def test_empty_list(self) -> None:
        assert not _is_filled([])

    def test_filled_list(self) -> None:
        assert _is_filled(["a"])

    def test_empty_dict(self) -> None:
        assert not _is_filled({})

    def test_dict_with_none_values(self) -> None:
        assert not _is_filled({"a": None, "b": ""})

    def test_dict_with_some_values(self) -> None:
        assert _is_filled({"a": None, "b": "hello"})

    def test_empty_string(self) -> None:
        assert not _is_filled("")

    def test_whitespace_string(self) -> None:
        assert not _is_filled("   ")

    def test_filled_string(self) -> None:
        assert _is_filled("hello")

    def test_bool_true(self) -> None:
        assert _is_filled(True)

    def test_bool_false(self) -> None:
        assert not _is_filled(False)


class TestBrief:
    def test_filled_list(self) -> None:
        assert _brief(["a", "b"]) == "2项"

    def test_empty_list(self) -> None:
        assert _brief([]) == "空"

    def test_filled_dict(self) -> None:
        assert _brief({"a": "x", "b": "", "c": None}) == "1子字段"

    def test_short_string(self) -> None:
        assert _brief("hello") == "hello"

    def test_long_string(self) -> None:
        assert _brief("a" * 20) == ("a" * 15 + "...")

    def test_bool(self) -> None:
        assert _brief(True) == "有"
        assert _brief(False) == "空"


class TestBuildPreview:
    def test_generates_all_fields(self) -> None:
        loop = make_agent_loop()
        loop._schema_context = deepcopy(DEFAULT_CONTEXT)
        loop._schema_context["title"] = "测试"
        loop._schema_context["purpose"] = "目的"
        loop._schema_context["experimental_plan"] = [
            {"group": "A", "condition": "100C", "expected": "high"}
        ]
        loop.references = ["EXP-2026-001"]

        preview = _build_preview(loop)
        assert preview["title"] == "测试"
        assert preview["purpose"] == "目的"
        assert preview["status"] == "planned"
        assert preview["references"] == ["EXP-2026-001"]
        assert "id" in preview
        assert preview["id"].startswith("EXP-")
        # 修复验证: experimental_plan 和 results 结构完整性
        assert preview["experimental_plan"] == [
            {"group": "A", "condition": "100C", "expected": "high"}
        ]
        assert preview["results"] == {"qualitative": "", "key_data": [], "figures": []}


class TestExtractThreadDialogue:
    def test_filters_system_and_tool_messages(self) -> None:
        loop = make_agent_loop()
        loop.history = [
            {"role": "system", "content": "[全局上下文]\n实验库共3条"},
            {"role": "user", "content": "记个实验"},
            {"role": "assistant", "content": "好的，请说", "tool_calls": [
                {"id": "x", "type": "function",
                 "function": {"name": "ask_user", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "x",
             "content": '{"status": "asked"}'},
            {"role": "assistant", "content": "请补充材料信息"},
            {"role": "user", "content": "用了P25"},
        ]
        dialogue = _extract_thread_dialogue(loop)
        assert "全局上下文" not in dialogue
        assert "asked" not in dialogue
        assert "用户: 记个实验" in dialogue
        assert "助手: 请补充材料信息" in dialogue
        assert "用户: 用了P25" in dialogue
        # 含 tool_calls 的 assistant 消息应被过滤
        assert "好的，请说" not in dialogue


class TestToolLogSummary:
    def test_load_reference(self) -> None:
        result = {"loaded": {"EXP-001": {"id": "EXP-001"}, "EXP-002": {"error": "x"}}}
        kw = _tool_log_summary("load_reference", {"refs": ["EXP-001", "EXP-002"]}, result)
        assert kw["refs"] == ["EXP-001", "EXP-002"]
        assert kw["loaded_count"] == 1

    def test_search_experiments(self) -> None:
        result = {"candidates": [{"id": "EXP-001"}, {"id": "EXP-002"}]}
        kw = _tool_log_summary("search_experiments", {"query": "ZnO"}, result)
        assert kw["query"] == "ZnO"
        assert kw["hits"] == 2

    def test_update_schema(self) -> None:
        kw = _tool_log_summary("update_schema", {"fields": {"title": "X", "purpose": "Y"}}, {})
        assert set(kw["fields"]) == {"title", "purpose"}

    def test_tool_error(self) -> None:
        kw = _tool_log_summary("load_reference", {"refs": ["X"]}, {"error": "not_found", "message": "missing"})
        assert "error" in kw


# ============================================================
# Task 2.4: AgentLoop 单元测试
# ============================================================


class TestAgentLoopCoreFields:
    def test_photocatalysis_core(self) -> None:
        loop = make_agent_loop()
        loop._schema_context = deepcopy(DEFAULT_CONTEXT)
        loop.experiment_type = "photocatalysis"
        # 空 Schema
        assert not loop._core_fields_filled()
        # 填充核心字段
        loop._schema_context["purpose"] = "测试"
        loop._schema_context["materials"] = [{"name": "TiO2"}]
        loop._schema_context["process_parameters"] = [{"parameter": "光强"}]
        loop._schema_context["results"] = {"qualitative": "ok"}
        assert loop._core_fields_filled()

    def test_hydrothermal_core(self) -> None:
        loop = make_agent_loop()
        loop._schema_context = deepcopy(DEFAULT_CONTEXT)
        loop.experiment_type = "hydrothermal"
        assert not loop._core_fields_filled()
        loop._schema_context["purpose"] = "合成"
        loop._schema_context["materials"] = [{"name": "Zn(NO3)2"}]
        loop._schema_context["sop"] = ["步骤"]
        loop._schema_context["process_parameters"] = [{"parameter": "温度"}]
        loop._schema_context["results"] = {"qualitative": "成功"}
        assert loop._core_fields_filled()

    def test_other_default_core(self) -> None:
        loop = make_agent_loop()
        loop._schema_context = deepcopy(DEFAULT_CONTEXT)
        loop.experiment_type = "other"
        assert not loop._core_fields_filled()
        loop._schema_context["purpose"] = "目的"
        loop._schema_context["materials"] = [{"name": "X"}]
        loop._schema_context["sop"] = ["S"]
        loop._schema_context["results"] = {"qualitative": "R"}
        assert loop._core_fields_filled()


class TestAgentLoopBuildNotes:
    def test_complete_context(self) -> None:
        loop = make_agent_loop()
        loop._schema_context = deepcopy(DEFAULT_CONTEXT)
        loop._schema_context["title"] = "测试实验"
        loop._schema_context["date"] = "2026-01-01"
        loop._schema_context["experimenter"] = "张三"
        loop._schema_context["purpose"] = "验证假设"
        loop._schema_context["materials"] = [{"name": "TiO2", "purity": "99%"}]
        loop._schema_context["experimental_plan"] = [
            {"group": "A", "condition": "100C", "expected": "高产"}
        ]
        loop._schema_context["sop"] = ["步骤1"]
        loop._schema_context["tags"] = ["photocatalysis"]
        loop._schema_context["status"] = "done"
        loop._schema_context["conclusion"] = "成功"

        notes = loop._build_notes_from_context()
        assert "测试实验" in notes
        assert "2026-01-01" in notes
        assert "张三" in notes
        assert "TiO2" in notes
        assert "步骤1" in notes
        assert "成功" in notes
        # 新增渲染字段验证
        assert "实验方案" in notes
        assert "组'A': 100C, 预期高产" in notes
        assert "标签: photocatalysis" in notes
        assert "状态: 已完成" in notes

    def test_empty_context(self) -> None:
        loop = make_agent_loop()
        loop._schema_context = None
        notes = loop._build_notes_from_context()
        assert "无实验描述" in notes

    def test_partial_context(self) -> None:
        loop = make_agent_loop()
        loop._schema_context = {"title": "仅标题"}
        notes = loop._build_notes_from_context()
        assert "仅标题" in notes


class TestAgentLoopSchemaStatus:
    def test_filled_and_missing(self) -> None:
        loop = make_agent_loop()
        loop._schema_context = deepcopy(DEFAULT_CONTEXT)
        loop._schema_context["title"] = "T"
        loop._schema_context["purpose"] = "P"

        status = loop._build_schema_status()
        assert "Schema状态" in status
        assert "已填" in status
        assert "缺失" in status
        assert "标题" in status

    def test_high_fill_prompt(self) -> None:
        loop = make_agent_loop()
        loop._schema_context = deepcopy(DEFAULT_CONTEXT)
        # Fill 12+ fields to pass 70%
        for key in ["title", "date", "experimenter", "status", "purpose",
                     "materials", "sop", "process_parameters", "observations",
                     "results", "conclusion", "next_steps"]:
            if isinstance(DEFAULT_CONTEXT.get(key), list):
                loop._schema_context[key] = ["x"]
            elif isinstance(DEFAULT_CONTEXT.get(key), dict):
                loop._schema_context[key] = {"x": "y"}
            else:
                loop._schema_context[key] = "x"

        status = loop._build_schema_status()
        assert "可考虑结束收集" in status


class TestAgentLoopThreadStatus:
    def test_free_mode(self) -> None:
        loop = make_agent_loop()
        status = loop._build_thread_status()
        assert "自由模式" in status

    def test_record_mode(self) -> None:
        loop = make_agent_loop()
        loop.thread.id = "THR-001"
        loop.thread.type = "record"
        status = loop._build_thread_status()
        assert "record 线程进行中" in status

    def test_analyze_mode(self) -> None:
        loop = make_agent_loop()
        loop.thread.id = "THR-001"
        loop.thread.type = "analyze"
        status = loop._build_thread_status()
        assert "analyze 线程进行中" in status

    def test_exp_editor_child(self) -> None:
        loop = make_agent_loop()
        loop.child.agent_role = "exp_editor"
        status = loop._build_thread_status()
        assert "修改已完成的实验" in status

    def test_analysis_reviewer_child(self) -> None:
        loop = make_agent_loop()
        loop.child.agent_role = "analysis_reviewer"
        status = loop._build_thread_status()
        assert "审阅/修改" in status


class TestAgentLoopMode:
    def test_no_thread_is_general(self) -> None:
        loop = make_agent_loop()
        assert loop.mode == "general"

    def test_record_thread(self) -> None:
        loop = make_agent_loop()
        loop.thread.id = "THR-001"
        loop.thread.type = "record"
        assert loop.mode == "record"

    def test_analyze_thread(self) -> None:
        loop = make_agent_loop()
        loop.thread.id = "THR-001"
        loop.thread.type = "analyze"
        assert loop.mode == "analyze"


class TestAgentLoopActiveTools:
    def test_general_mode_tools(self) -> None:
        loop = make_agent_loop()
        tools = loop._get_active_tools()
        names = {t["function"]["name"] for t in tools}
        assert "load_reference" in names
        assert "search_experiments" in names
        assert "start_record_thread" in names
        assert "start_analyze_thread" in names
        assert "modify_experiment" in names
        assert "update_schema" not in names  # record only
        assert "generate_analysis" not in names  # analyze only

    def test_record_mode_tools(self) -> None:
        loop = make_agent_loop()
        loop.thread.id = "THR-001"
        loop.thread.type = "record"
        tools = loop._get_active_tools()
        names = {t["function"]["name"] for t in tools}
        assert "update_schema" in names
        assert "ask_user" in names
        assert "generate_record" in names
        assert "start_analyze_thread" not in names  # analyze only

    def test_exp_editor_child_tools(self) -> None:
        loop = make_agent_loop()
        loop.child.agent_role = "exp_editor"
        tools = loop._get_active_tools()
        names = {t["function"]["name"] for t in tools}
        assert "modify_experiment" in names
        assert "end_thread" in names
        assert "update_schema" not in names
        assert "generate_record" not in names
        assert "start_record_thread" not in names

    def test_analysis_reviewer_child_tools(self) -> None:
        loop = make_agent_loop()
        loop.child.agent_role = "analysis_reviewer"
        tools = loop._get_active_tools()
        names = {t["function"]["name"] for t in tools}
        assert "modify_analysis" in names
        assert "generate_analysis" not in names  # not for reviewers


class TestAgentLoopL0Stale:
    def test_no_timestamp_is_stale(self) -> None:
        loop = make_agent_loop()
        loop._l0_generated_at = None
        assert loop._l0_stale()

    def test_old_timestamp_is_stale(self) -> None:
        from datetime import datetime, timedelta
        loop = make_agent_loop()
        loop._l0_generated_at = datetime.now() - timedelta(hours=2)
        assert loop._l0_stale()

    def test_recent_timestamp_is_fresh(self) -> None:
        from datetime import datetime
        loop = make_agent_loop()
        loop._l0_generated_at = datetime.now()
        assert not loop._l0_stale()


class TestAgentLoopThreadGuidance:
    def test_record_guidance(self) -> None:
        loop = make_agent_loop()
        g = loop._build_thread_guidance("record")
        assert "记录一条新实验" in g["content"]
        assert "generate_record" in g["content"]

    def test_analyze_guidance(self) -> None:
        loop = make_agent_loop()
        g = loop._build_thread_guidance("analyze")
        assert "跨实验分析" in g["content"]
        assert "generate_analysis" in g["content"]


class TestAgentLoopStateRoundTrip:
    def test_empty_round_trip(self) -> None:
        loop = make_agent_loop()
        loop2 = AgentLoop.from_dict(
            MockLLMClient(), loop.store,
            loop.state_to_dict(),
        )
        assert loop2._schema_context is None
        assert loop2.references == []
        assert loop2.experiment_type == "other"
        assert loop2.turn_count == 0

    def test_with_context_round_trip(self) -> None:
        loop = make_agent_loop()
        loop._schema_context = deepcopy(DEFAULT_CONTEXT)
        loop._schema_context["title"] = "测试"
        loop.references = ["EXP-001"]
        loop.experiment_type = "photocatalysis"
        loop.turn_count = 3
        loop.thread.id = "THR-001"
        loop.thread.type = "record"
        loop.child.is_child = True
        loop.child.exp_id = "EXP-001"
        loop.child.agent_role = "exp_editor"

        data = loop.state_to_dict()
        loop2 = AgentLoop.from_dict(
            MockLLMClient(), loop.store,
            data,
        )
        assert loop2._schema_context is not None
        assert loop2._schema_context["title"] == "测试"
        assert loop2.references == ["EXP-001"]
        assert loop2.experiment_type == "photocatalysis"
        assert loop2.turn_count == 3
        assert loop2.thread.id == "THR-001"
        assert loop2.thread.type == "record"
        assert loop2.child.is_child is True
        assert loop2.child.exp_id == "EXP-001"
        assert loop2.child.agent_role == "exp_editor"


# ============================================================
# Task 2.3 (continued): AgentLoop.run() 集成测试
# ============================================================


class TestAgentLoopRun:
    def test_text_reply(self) -> None:
        """LLM 返回纯文本 → reply 类型。"""
        llm = MockLLMClient()
        llm.set_responses(make_text_response("你好，有什么可以帮你的？"))
        loop = make_agent_loop(llm=llm)

        result = loop.run("你好")
        assert result["type"] == "reply"
        assert "你好" in result["message"]

    def test_tool_loop_then_reply(self) -> None:
        """LLM 先调工具，再返回文本。"""
        llm = MockLLMClient()
        llm.set_responses(
            make_tool_response("search_experiments", {"query": "ZnO"}),
            make_text_response("找到2个ZnO相关实验。"),
        )
        loop = make_agent_loop(llm=llm)

        result = loop.run("搜索ZnO实验")
        assert result["type"] == "reply"

    def test_llm_error_graceful(self) -> None:
        """Phase 4: LLM 异常被捕获，返回友好错误消息。"""
        class FailingLLM(MockLLMClient):
            def chat(self, **kwargs: Any) -> Any:
                raise ConnectionError("模拟网络故障")

        llm = FailingLLM()
        loop = make_agent_loop(llm=llm)

        result = loop.run("你好")
        assert result["type"] == "reply"
        assert "AI 服务" in result["message"] or "暂时不可用" in result["message"]
        # history 应只保留 user 消息 + 系统错误标记
        user_msgs = [m for m in loop.history if m.get("role") == "user"]
        assert len(user_msgs) == 1
        assert user_msgs[0]["content"] == "你好"
        # turn_count 应被重置为实际 user 消息数
        assert loop.turn_count == 1

    def test_consecutive_tool_errors_graceful(self) -> None:
        """连续 3 次工具返回顶层 error → 停止循环并返回错误。"""
        llm = MockLLMClient()
        # generate_record 在非 record 模式下返回顶层 error
        llm.set_responses(
            make_tool_response("generate_record", {}),
            make_tool_response("generate_record", {}),
            make_tool_response("generate_record", {}),
        )
        loop = make_agent_loop(llm=llm)
        # 确保不在 record 模式——generate_record 返回 error
        loop._schema_context = None

        result = loop.run("生成记录")
        assert result["type"] == "reply"
        assert "技术问题" in result["message"] or "换个方式" in result["message"]
