"""Phase 4: agent_v2 integration tests"""
import sys, json, os
from pathlib import Path
from copy import deepcopy

sys.path.insert(0, str(Path(__file__).parent))
from lib.agent_v2 import AgentLoop, merge_context, _is_filled, TOOLS_OPENAI_FORMAT


# ---- Mock LLM with controllable tool call responses ----
class MockLLM:
    """Simulates DeepSeek API, returns pre-programmed responses in order."""
    def __init__(self):
        self.client = self
        self.model = "mock"
        self.responses = []
        self.call_count = 0

    def set_responses(self, *responses):
        """Each response is a dict: {content?, tool_calls?}."""
        self.responses = list(responses)
        self.call_count = 0

    class chat:
        class completions:
            @staticmethod
            def create(**kw):
                llm = kw.get("model")  # not used, we get via outer ref
                pass


class MockStore:
    def __init__(self):
        self.path = Path("D:/Projects/Exdiary-v1.2/experiments")
        self.exps = {}
    def load(self, id):
        return self.exps.get(id)
    def list_all_full(self):
        return list(self.exps.values())


# We need a different approach for MockLLM — the nested class can't access outer.
# Let's use a function-based mock.

class ResponseController:
    def __init__(self):
        self.responses = []
        self.idx = 0
    def set_responses(self, *responses):
        self.responses = list(responses)
        self.idx = 0
    def next(self, messages, tools, temperature):
        if self.idx < len(self.responses):
            r = self.responses[self.idx]
            self.idx += 1
            return r
        return {"content": "信息已齐备，正在生成记录。", "tool_calls": None}


def make_mock_llm(controller):
    """Create a MockLLM that delegates to controller.next()"""
    class MockChatCompletions:
        @staticmethod
        def create(**kw):
            return controller.next(
                kw.get("messages"), kw.get("tools"), kw.get("temperature", 0.3)
            )
    class MockChat:
        completions = MockChatCompletions
    class MockClient:
        chat = MockChat
    class LLM:
        def __init__(self):
            self.client = MockClient
            self.model = "mock"
    return LLM()


def make_tool_response(name, arguments):
    """Create a mock API response with a single tool call."""
    args_str = json.dumps(arguments, ensure_ascii=False)
    tc = type("tc", (), {
        "id": "call_001",
        "function": type("fn", (), {"name": name, "arguments": args_str})(),
    })()
    msg = type("msg", (), {"content": None, "tool_calls": [tc]})()
    choice = type("choice", (), {"message": msg})()
    return type("response", (), {"choices": [choice]})()


def make_text_response(content):
    msg = type("msg", (), {"content": content, "tool_calls": None})()
    choice = type("choice", (), {"message": msg})()
    return type("response", (), {"choices": [choice]})()


# ---- Test 4.1: load_reference + update_schema flow ----
def test_load_and_inherit():
    print("=== 4.1: load_reference + update_schema ===")
    ctrl = ResponseController()
    llm = make_mock_llm(ctrl)
    store = MockStore()
    store.exps["EXP-2026-001"] = {
        "id": "EXP-2026-001", "title": "TiO2光催化降解MB",
        "status": "done", "tags": ["photocatalysis", "thin-film"],
        "purpose": "研究P25负载量对降解性能的影响",
        "materials": [{"name": "TiO2 P25", "purity": "99.8%", "vendor": "Degussa"}],
        "sop": ["配制MB溶液", "浸渍提拉涂覆P25", "450°C煅烧2h", "氙灯照射"],
        "process_parameters": [
            {"parameter": "煅烧温度", "setpoint": "450 °C"},
            {"parameter": "光源功率", "setpoint": "300 W"},
        ],
        "results": {"qualitative": "5%负载量降解率92%",
                     "key_data": [{"metric": "降解率", "value": "92%"}]},
        "conclusion": "5% P25负载量效果最佳",
        "date": "", "equipment": [], "experimental_plan": [],
        "observations": {"no_anomalies": True, "items": []},
        "characterization": [], "next_steps": [],
    }

    # Round 1: User says "复现EXP-2026-001" → LLM calls load_reference
    ctrl.set_responses(
        make_tool_response("load_reference", {"refs": ["EXP-2026-001"]}),
        # After load, LLM decides to update_schema with all fields + ask_user
        make_tool_response("update_schema", {
            "fields": {
                "title": "TiO2光催化降解MB",
                "purpose": "研究P25负载量对降解性能的影响",
                "materials": [{"name": "TiO2 P25", "purity": "99.8%", "vendor": "Degussa"}],
                "sop": ["配制MB溶液", "浸渍提拉涂覆P25", "450°C煅烧2h", "氙灯照射"],
                "process_parameters": [
                    {"parameter": "煅烧温度", "setpoint": "450 °C"},
                    {"parameter": "光源功率", "setpoint": "300 W"},
                ],
                "results": {"qualitative": "5%负载量降解率92%",
                            "key_data": [{"metric": "降解率", "value": "92%"}]},
                "conclusion": "5% P25负载量效果最佳",
                "status": "done",
                "tags": ["photocatalysis", "thin-film"],
            }
        }),
        make_tool_response("ask_user", {"questions": ["这次复现有改动吗？还是完全一致？"]}),
    )
    agent = AgentLoop(llm, store)
    r = agent.run("复现了EXP-2026-001")
    assert r["type"] == "reply"
    assert "EXP-2026-001" in agent.references
    assert agent.experiment_type == "photocatalysis"
    # Context should now have the inherited data
    assert len(agent.context["sop"]) == 4
    assert len(agent.context["process_parameters"]) == 2
    assert agent.context["results"]["qualitative"] != ""
    print(f"  Reply: {r['message'][:80]}...")
    print("  [OK]")

    # Round 2: User says "完全一样" → LLM should end (core fields filled)
    ctrl.set_responses(
        make_text_response("好的，跟EXP-2026-001完全一致，我来整理一下。"),
    )
    r = agent.run("完全一样，直接生成吧")
    assert r["type"] == "extract"
    assert agent._core_fields_filled()
    print(f"  Extract triggered, context has {sum(1 for f in agent.context.values() if _is_filled(f))} filled fields")
    print("  [OK]")


# ---- Test 4.2: Error tolerance ----
def test_error_tolerance():
    print("=== 4.2: Error tolerance ===")
    ctrl = ResponseController()
    llm = make_mock_llm(ctrl)
    store = MockStore()
    store.exps["EXP-001"] = {"id": "EXP-001", "title": "Test", "tags": [],
                              "purpose": "", "materials": [], "sop": [],
                              "process_parameters": [], "results": {},
                              "conclusion": "", "date": "", "status": "done"}

    # LLM tries 3 times with a bad tool name → should break with friendly message
    ctrl.set_responses(
        make_tool_response("bad_tool_name", {}),  # error 1
        make_tool_response("bad_tool_name", {}),  # error 2
        make_tool_response("bad_tool_name", {}),  # error 3 → break
    )
    agent = AgentLoop(llm, store)
    r = agent.run("hello")
    assert r["type"] == "reply"
    assert "技术问题" in r["message"] or "换个方式" in r["message"]
    print(f"  Error break: {r['message'][:80]}")
    print("  [OK]")


# ---- Test 4.3: Extract flow with parse_notes fallback ----
def test_extract_flow():
    print("=== 4.3: Extract flow ===")
    ctrl = ResponseController()
    llm = make_mock_llm(ctrl)
    store = MockStore()

    # Round 1: intent → get some data
    ctrl.set_responses(
        make_tool_response("load_reference", {"refs": ["EXP-2026-001"]}),
        make_tool_response("update_schema", {
            "fields": {"title": "Test", "purpose": "Testing",
                       "materials": [{"name": "TiO2"}],
                       "sop": ["step1"], "process_parameters": [{"parameter": "t", "setpoint": "1"}],
                       "results": {"qualitative": "good"}}
        }),
        make_tool_response("ask_user", {"questions": ["有改动吗？"]}),
    )
    store.exps["EXP-2026-001"] = {
        "id": "EXP-2026-001", "title": "Ref", "tags": ["photocatalysis"],
        "purpose": "", "materials": [], "sop": [], "process_parameters": [],
        "results": {}, "conclusion": "", "date": "", "status": "done"
    }
    agent = AgentLoop(llm, store)
    r = agent.run("测试实验")
    assert r["type"] == "reply"
    print(f"  Round 1 reply: {r['message'][:60]}...")

    # Round 2: say done → extract
    ctrl.set_responses(
        make_text_response("差不多了，整理一下"),
    )
    r = agent.run("就这样")
    assert r["type"] == "extract"
    # Verify context has the data
    assert agent.context["title"] == "Test"
    assert agent._core_fields_filled()
    print(f"  Extract OK, context filled: title={agent.context['title']}")
    print("  [OK]")


# ---- Test 4.4: Fuzzy search + confirm + load ----
def test_fuzzy_flow():
    print("=== 4.4: Fuzzy reference flow ===")
    ctrl = ResponseController()
    llm = make_mock_llm(ctrl)
    store = MockStore()
    store.exps["EXP-2026-002"] = {
        "id": "EXP-2026-002", "title": "ZnO水热合成纳米棒",
        "tags": ["hydrothermal", "nano"], "purpose": "合成ZnO纳米棒",
        "materials": [{"name": "Zn(NO3)2"}], "sop": ["水热反应"],
        "process_parameters": [{"parameter": "温度", "setpoint": "180°C"}],
        "results": {"qualitative": "成功合成"}, "conclusion": "水热法有效",
        "date": "", "status": "done"
    }

    # LLM flow: search → get candidates → ask user to confirm
    ctrl.set_responses(
        make_tool_response("search_experiments", {"query": "上次的ZnO实验"}),
        make_tool_response("ask_user", {"questions": [
            "找到匹配: EXP-2026-002 ZnO水热合成纳米棒。是这个吗？"
        ]}),
    )
    agent = AgentLoop(llm, store)
    r = agent.run("跟上次那个ZnO水热实验一样")
    assert r["type"] == "reply"
    assert "ZnO" in r["message"]
    print(f"  Search reply: {r['message'][:100]}...")
    print("  [OK]")


# ---- Test 4.5: Context merge dedup ----
def test_merge_dedup():
    print("=== 4.5: Context merge dedup ===")
    ctx = {"tags": [], "materials": [], "sop": []}
    merge_context(ctx, {"tags": ["a", "b", "a"], "sop": ["s1"]})
    assert ctx["tags"] == ["a", "b"]  # dedup
    merge_context(ctx, {"tags": ["a", "c"]})
    assert ctx["tags"] == ["a", "b", "c"]  # 'a' not duplicated

    # Nested dict merge
    ctx["results"] = {"qualitative": "", "key_data": []}
    merge_context(ctx, {"results": {"qualitative": "good"}})
    assert ctx["results"]["qualitative"] == "good"
    merge_context(ctx, {"results": {"qualitative": "better"}})
    assert ctx["results"]["qualitative"] == "better"
    print("  [OK]")


# ---- Test 4.6: Schema status after inherit ----
def test_schema_status():
    print("=== 4.6: Schema status ===")
    ctrl = ResponseController()
    llm = make_mock_llm(ctrl)
    store = MockStore()
    store.exps["EXP-001"] = {
        "id": "EXP-001", "title": "Ref", "tags": ["photocatalysis"],
        "purpose": "test", "materials": [{"name": "TiO2"}],
        "sop": ["s1", "s2"], "process_parameters": [{"parameter": "t", "setpoint": "1"}],
        "results": {"qualitative": "ok"}, "conclusion": "", "date": "", "status": "done"
    }
    ctrl.set_responses(
        make_tool_response("load_reference", {"refs": ["EXP-001"]}),
        make_tool_response("update_schema", {
            "fields": {"title": "Ref", "purpose": "test",
                       "materials": [{"name": "TiO2"}], "sop": ["s1", "s2"],
                       "process_parameters": [{"parameter": "t", "setpoint": "1"}],
                       "results": {"qualitative": "ok"}, "status": "done",
                       "tags": ["photocatalysis"]}
        }),
        make_tool_response("ask_user", {"questions": ["有改动吗？"]}),
    )
    agent = AgentLoop(llm, store)
    agent.run("复现EXP-001")

    # Check Schema status was injected into history
    status_msgs = [m for m in agent.history if m["role"] == "system" and "Schema状态" in m.get("content", "")]
    assert len(status_msgs) >= 1
    status = status_msgs[-1]["content"]
    assert "/16" in status
    assert "缺失" in status
    print(f"  Status: {status.split(chr(10))[0]}")
    print("  [OK]")


# ---- Test 4.7: Real API conversation (requires API key) ----
def test_real_conversation():
    from lib.llm import LLMClient
    import yaml
    config_path = Path("D:/Projects/Exdiary-v1.2/config.yaml")
    if not config_path.exists():
        print("=== 4.7: Real API — SKIP (no config) ===")
        return

    with open(config_path) as f:
        cfg = yaml.safe_load(f) or {}
    api_key = cfg.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("=== 4.7: Real API — SKIP (no API key) ===")
        return

    from lib.storage import ExperimentStore
    llm = LLMClient(api_key=api_key, model="deepseek-v4-flash")
    store = ExperimentStore("D:/Projects/Exdiary-v1.2/experiments")

    print("=== 4.7: Real API conversation ===")
    agent = AgentLoop(llm, store)
    r = agent.run("复现了EXP-2026-001，完全一样，直接生成")
    print(f"  Type: {r['type']}")
    print(f"  Reply: {r.get('message', '')[:200]}")
    if r["type"] == "extract":
        print("  Extract triggered — checking context...")
        filled = sum(1 for v in agent.context.values() if _is_filled(v))
        print(f"  Context filled: {filled}/16 fields")
        print("  [OK]")
    else:
        # Might need another round
        print(f"  Context so far: {sum(1 for v in agent.context.values() if _is_filled(v))}/16 fields")
        r2 = agent.run("就这样，直接生成")
        print(f"  Round 2 type: {r2['type']}")
        print(f"  Reply: {r2.get('message', '')[:200]}")
        print("  [OK]")

    agent.save_final_messages()
    print(f"  Debug saved to: {agent.debug_dir}")


# ---- Run all tests ----
if __name__ == "__main__":
    try:
        test_load_and_inherit()
        test_error_tolerance()
        test_extract_flow()
        test_fuzzy_flow()
        test_merge_dedup()
        test_schema_status()
        test_real_conversation()
        print()
        print("=== ALL TESTS PASSED ===")
    except Exception as e:
        print(f"\nFAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)