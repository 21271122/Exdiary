"""共享 pytest fixtures 与测试辅助函数。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from copy import deepcopy
from typing import Any

import pytest

from lib.llm import AbstractLLMClient, LLMResponse
from lib.core.schema import DEFAULT_CONTEXT
from lib.repositories.yaml_experiment import YamlExperimentRepository
from lib.repositories.yaml_analysis import YamlAnalysisRepository
from lib.repositories.yaml_thread import ThreadRepository
from lib.repositories.yaml_favorites import YamlFavoritesRepository
from lib.repositories.yaml_update_log import YamlUpdateLogRepository


# ============================================================
# Mock LLM Client
# ============================================================


class MockLLMClient(AbstractLLMClient):
    """返回预设 LLMResponse 序列的 Mock 客户端。"""

    def __init__(self) -> None:
        self.responses: list[LLMResponse] = []
        self.call_count: int = 0

    def set_responses(self, *responses: LLMResponse) -> None:
        self.responses = list(responses)
        self.call_count = 0

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        if self.call_count >= len(self.responses):
            raise RuntimeError(
                f"MockLLM: 预设响应已耗尽 (call #{self.call_count}, "
                f"只有 {len(self.responses)} 个预设)"
            )
        resp = self.responses[self.call_count]
        self.call_count += 1
        return resp


# ============================================================
# LLMResponse 工厂函数
# ============================================================


def make_text_response(content: str) -> LLMResponse:
    """纯文本响应（无 tool_calls）。"""
    return LLMResponse(content=content)


def make_tool_response(tool_name: str, arguments: dict[str, Any]) -> LLMResponse:
    """只包含 tool_calls、无纯文本的响应。"""
    return LLMResponse(
        content="",
        tool_calls=[{
            "id": f"call_mock_{tool_name}",
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": __import__("json").dumps(arguments, ensure_ascii=False),
            },
        }],
    )


def make_tool_with_text(text: str, tool_name: str, arguments: dict[str, Any]) -> LLMResponse:
    """同时包含文本和 tool_calls 的响应。"""
    return LLMResponse(
        content=text,
        tool_calls=[{
            "id": f"call_mock_{tool_name}",
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": __import__("json").dumps(arguments, ensure_ascii=False),
            },
        }],
    )


# ============================================================
# 临时 Repository fixtures
# ============================================================


@pytest.fixture
def tmp_exp_repo() -> YamlExperimentRepository:
    """基于临时目录的实验 Repository，预置种子数据。"""
    with tempfile.TemporaryDirectory() as td:
        repo = YamlExperimentRepository(td)
        yield repo


@pytest.fixture
def tmp_analysis_repo() -> YamlAnalysisRepository:
    """基于临时目录的分析 Repository。"""
    with tempfile.TemporaryDirectory() as td:
        repo = YamlAnalysisRepository(td)
        yield repo


@pytest.fixture
def tmp_thread_repo() -> ThreadRepository:
    """基于临时目录的线程 Repository。"""
    with tempfile.TemporaryDirectory() as td:
        repo = ThreadRepository(td)
        yield repo


@pytest.fixture
def tmp_favorites_repo() -> YamlFavoritesRepository:
    """基于临时文件的收藏 Repository。"""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "_favorites.yaml"
        repo = YamlFavoritesRepository(str(path))
        yield repo


@pytest.fixture
def tmp_update_log_repo() -> YamlUpdateLogRepository:
    """基于临时目录的更新日志 Repository。"""
    with tempfile.TemporaryDirectory() as td:
        repo = YamlUpdateLogRepository(td)
        yield repo


# ============================================================
# 样本数据 fixtures
# ============================================================


@pytest.fixture
def sample_exp_data() -> dict[str, Any]:
    """标准测试实验 dict。"""
    return {
        "id": "EXP-2026-001",
        "title": "TiO2 光催化测试",
        "date": "2026-01-15",
        "experimenter": "测试员",
        "status": "done",
        "tags": ["photocatalysis", "thin-film"],
        "purpose": "测试 P25 光催化降解 MB 性能",
        "materials": [
            {"name": "P25 TiO2", "purity": "99.8%", "vendor": "Degussa", "amount": "1g", "notes": ""},
            {"name": "亚甲基蓝", "purity": "AR", "vendor": "Sigma", "amount": "10mg/L", "notes": ""},
        ],
        "equipment": [
            {"device": "紫外-可见分光光度计", "model": "", "location": ""},
        ],
        "experimental_plan": [],
        "sop": ["配制 MB 溶液", "加入催化剂", "光照反应", "取样测吸光度"],
        "process_parameters": [
            {"step": "光催化", "parameter": "光源功率", "setpoint": "300W", "actual": "", "deviation": ""},
        ],
        "observations": {"no_anomalies": False, "items": ["溶液颜色变浅"]},
        "characterization": [
            {"method": "UV-Vis", "sample_id": "", "preparation": "", "submission_date": "", "data_path": ""},
        ],
        "results": {
            "qualitative": "降解效果显著",
            "key_data": [{"metric": "降解率", "value": "92%", "comparison": "", "change": ""}],
            "figures": [],
        },
        "conclusion": "5% P25 负载量效果最佳",
        "next_steps": ["重复实验", "SEM 表征"],
        "original_notes": "",
        "references": [],
    }


@pytest.fixture
def sample_exp_data_list() -> list[dict[str, Any]]:
    """多条实验数据，用于搜索/过滤测试。"""
    return [
        {
            "id": "EXP-2026-001", "title": "TiO2 光催化降解 MB",
            "date": "2026-01-10", "experimenter": "张三", "status": "done",
            "tags": ["photocatalysis", "thin-film"],
            "purpose": "测试 P25 降解性能",
            "materials": [{"name": "P25 TiO2"}, {"name": "亚甲基蓝"}],
            "sop": ["步骤1", "步骤2"],
            "process_parameters": [{"parameter": "光源", "setpoint": "300W"}],
            "observations": {"no_anomalies": True, "items": []},
            "characterization": [],
            "results": {"qualitative": "好", "key_data": [{"metric": "降解率", "value": "92%"}]},
            "conclusion": "效果良好",
            "next_steps": [],
        },
        {
            "id": "EXP-2026-002", "title": "ZnO 水热合成纳米棒",
            "date": "2026-01-20", "experimenter": "李四", "status": "done",
            "tags": ["hydrothermal", "nano"],
            "purpose": "合成 ZnO 纳米棒",
            "materials": [{"name": "Zn(NO3)2"}, {"name": "NaOH"}],
            "sop": ["配制前驱体", "水热反应"],
            "process_parameters": [{"parameter": "温度", "setpoint": "180°C"}],
            "observations": {"no_anomalies": True, "items": []},
            "characterization": [],
            "results": {"qualitative": "成功合成", "key_data": []},
            "conclusion": "水热法有效",
            "next_steps": [],
        },
        {
            "id": "EXP-2026-003", "title": "钙钛矿电池制备",
            "date": "2026-02-01", "experimenter": "张三", "status": "running",
            "tags": ["perovskite-solar", "spin-coating"],
            "purpose": "制备高效率钙钛矿电池",
            "materials": [{"name": "PbI2"}, {"name": "MAI"}],
            "sop": ["清洗基底", "旋涂", "退火"],
            "process_parameters": [{"parameter": "转速", "setpoint": "4000rpm"}],
            "observations": {"no_anomalies": True, "items": []},
            "characterization": [],
            "results": {"qualitative": "", "key_data": []},
            "conclusion": "",
            "next_steps": ["蒸镀电极"],
        },
    ]


# ============================================================
# AgentLoop 构造辅助函数（注意：不是 pytest fixture）
# ============================================================


def make_agent_loop(
    llm: AbstractLLMClient | None = None,
    tool_executor: Any = None,
    exp_repo: Any = None,
    thread_repo: Any = None,
    update_log_repo: Any = None,
    favorites_repo: Any = None,
    analysis_repo: Any = None,
) -> Any:
    """构造 AgentLoop 实例用于测试。
    所有 repo 参数默认 None 时自动使用临时版本。"""
    from lib.agent_v2 import AgentLoop

    if llm is None:
        llm = MockLLMClient()

    if exp_repo is None:
        exp_repo = YamlExperimentRepository(tempfile.mkdtemp())

    return AgentLoop(
        llm,
        exp_repo,
        tool_executor=tool_executor,
        thread_store=thread_repo,
        update_log_store=update_log_repo,
        favorites_store=favorites_repo,
        analysis_store=analysis_repo,
    )
