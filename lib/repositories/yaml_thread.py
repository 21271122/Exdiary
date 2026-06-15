import yaml
import re
from pathlib import Path
from datetime import datetime
from lib.repositories.base import (
    AbstractExperimentRepository, AbstractAnalysisRepository,
    AbstractThreadRepository, AbstractFavoritesRepository,
    AbstractUpdateLogRepository,
)




class YamlThreadRepository(AbstractThreadRepository):
    """线程持久化存储 + L0 摘要 + 用户画像 + 子 Agent 状态 + 待合并队列。"""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self._index_cache = None
        self._l0_generated_at = None

    # -- helpers --

    def _index_path(self) -> Path:
        return self.path / "index.yaml"

    def _thread_path(self, thread_id: str) -> Path:
        return self.path / f"{thread_id}.yaml"

    def _global_context_path(self) -> Path:
        return self.path / "_global_context.yaml"

    def _current_state_path(self) -> Path:
        return self.path / "_current_state.yaml"

    def _child_state_path(self, thread_id: str) -> Path:
        return self.path / f"{thread_id}_child_state.yaml"

    def next_id(self) -> str:
        year = datetime.now().strftime("%Y")
        pattern = re.compile(rf"^THR-{year}-(\d{{3}})\.yaml$")
        max_n = 0
        for f in self.path.glob("THR-*.yaml"):
            m = pattern.match(f.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
        return f"THR-{year}-{max_n + 1:03d}"

    # -- index management --

    def _load_index(self) -> dict:
        if self._index_cache is not None:
            return self._index_cache
        fp = self._index_path()
        if fp.exists():
            with open(fp, "r", encoding="utf-8") as f:
                self._index_cache = yaml.safe_load(f) or {}
        else:
            self._index_cache = {}
        self._index_cache.setdefault("active_thread", None)
        self._index_cache.setdefault("threads", [])
        self._index_cache.setdefault("exp_to_thread", {})
        self._index_cache.setdefault("anal_to_thread", {})
        self._index_cache.setdefault("user_profile", {
            "experimenter_counts": {},
            "default_experimenter": "",
            "tag_counts": {},
            "frequent_tags": [],
            "last_updated": "",
        })
        return self._index_cache

    def _save_index(self) -> None:
        if self._index_cache is None:
            return
        with open(self._index_path(), "w", encoding="utf-8") as f:
            yaml.dump(self._index_cache, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, indent=2)

    def get_index(self) -> dict:
        return dict(self._load_index())

    def update_index(self, thread_data: dict) -> None:
        """线程 done 时更新索引列表 + 反向映射。"""
        idx = self._load_index()
        tid = thread_data["id"]
        # Update or append thread summary in list
        existing = False
        for i, t in enumerate(idx["threads"]):
            if t.get("id") == tid:
                idx["threads"][i] = {
                    "id": tid,
                    "type": thread_data.get("type", "record"),
                    "status": thread_data.get("status", "done"),
                    "title": thread_data.get("title", ""),
                    "summary": thread_data.get("summary", ""),
                    "exp_generated": thread_data.get("exp_generated", ""),
                    "created": thread_data.get("created", ""),
                    "updated": thread_data.get("updated", ""),
                }
                existing = True
                break
        if not existing:
            idx["threads"].insert(0, {
                "id": tid,
                "type": thread_data.get("type", "record"),
                "status": thread_data.get("status", "done"),
                "title": thread_data.get("title", ""),
                "summary": thread_data.get("summary", ""),
                "exp_generated": thread_data.get("exp_generated", ""),
                "created": thread_data.get("created", ""),
                "updated": thread_data.get("updated", ""),
            })
        # Update reverse mapping（不覆盖已有映射——一个线程只对应一个产出物）
        if thread_data.get("exp_generated") and thread_data["exp_generated"] not in idx["exp_to_thread"]:
            idx["exp_to_thread"][thread_data["exp_generated"]] = tid
        if thread_data.get("anal_generated") and thread_data["anal_generated"] not in idx["anal_to_thread"]:
            idx["anal_to_thread"][thread_data["anal_generated"]] = tid
        self._save_index()

    def get_active_thread(self) -> dict | None:
        idx = self._load_index()
        active_id = idx.get("active_thread")
        if not active_id:
            return None
        return self.load(active_id)

    def set_active_thread(self, thread_id: str | None) -> None:
        idx = self._load_index()
        if thread_id is None:
            # Clear active: mark previous active as done
            prev = idx.get("active_thread")
            if prev:
                thread = self.load(prev)
                if thread:
                    thread["status"] = "done"
                    self.save(thread)
            idx["active_thread"] = None
        else:
            # New active: auto-close previous
            prev = idx.get("active_thread")
            if prev and prev != thread_id:
                thread = self.load(prev)
                if thread:
                    thread["status"] = "done"
                    self.save(thread)
            idx["active_thread"] = thread_id
        self._save_index()

    def list_recent(self, n: int = 5) -> list[dict]:
        idx = self._load_index()
        return idx.get("threads", [])[:n]

    # -- thread CRUD --

    def create(self, thread_type: str, messages: list[dict]) -> dict:
        """创建新线程文件。messages 应包含边界标记消息。"""
        tid = self.next_id()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        thread = {
            "id": tid,
            "type": thread_type,
            "status": "active",
            "created": now,
            "updated": now,
            "title": "",
            "summary": "",
            "messages": messages,
            "branches": [],
        }
        if thread_type == "record":
            thread["experiment_type"] = "other"
            thread["exp_generated"] = ""
        elif thread_type == "analyze":
            thread["anal_generated"] = ""
            thread["selected_exps"] = []
        self.save(thread)
        # Update index
        idx = self._load_index()
        idx["threads"].insert(0, {
            "id": tid,
            "type": thread_type,
            "status": "active",
            "title": "",
            "summary": "",
            "created": now[:10],
            "updated": now[:10],
        })
        self._save_index()
        return thread

    def save(self, thread_data: dict) -> None:
        tid = thread_data.get("id")
        if not tid:
            raise ValueError("thread_data must have 'id'")
        fp = self._thread_path(tid)
        with open(fp, "w", encoding="utf-8") as f:
            yaml.dump(thread_data, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, indent=2)

    def load(self, thread_id: str) -> dict | None:
        fp = self._thread_path(thread_id)
        if not fp.exists():
            return None
        with open(fp, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    # -- global summary (L0) --

    def build_global_summary(self, experiment_store, update_log_store) -> str:
        """Python 确定性生成 L0 摘要。每项为空时省略该行。"""
        lines = []

        # Experiment library overview
        total = experiment_store.count() if experiment_store else 0
        if total > 0:
            statuses = {"done": 0, "running": 0, "failed": 0, "planned": 0, "repeated": 0}
            all_exps = experiment_store.list_all_full() if experiment_store else []
            for e in all_exps:
                s = e.get("status", "planned")
                if s in statuses:
                    statuses[s] += 1
            parts = [f"当前实验库共 {total} 条实验"]
            detail = []
            for st, label in [("done", "已完成"), ("running", "进行中"),
                              ("failed", "失败"), ("planned", "计划中")]:
                if statuses.get(st, 0) > 0:
                    detail.append(f"{label}: {statuses[st]}")
            if detail:
                parts.append("（" + ", ".join(detail) + "）")
            lines.append("".join(parts) + "。")

        # Recent threads
        recent = self.list_recent(5)
        done_threads = [t for t in recent if t.get("status") == "done"]
        if done_threads:
            display = []
            for t in done_threads[:3]:
                exp = t.get("exp_generated", "")
                title = t.get("title", "")[:20]
                if exp:
                    display.append(f"{t['id']}→{exp} {title}".strip())
                else:
                    display.append(f"{t['id']} {title}".strip())
            lines.append(f"最近完成: {', '.join(display)}。")

        # User profile
        profile = self.get_user_profile()
        freq_tags = profile.get("frequent_tags", [])
        if freq_tags:
            tag_display = ", ".join(f"{t}({profile.get('tag_counts', {}).get(t, '?')})"
                                   for t in freq_tags[:6])
            lines.append(f"你的常用标签: {tag_display}。")

        # Recently modified experiments
        if update_log_store:
            try:
                modified = []
                for t in done_threads[:5]:
                    exp_id = t.get("exp_generated", "")
                    if exp_id and exp_id.startswith("EXP-"):
                        logs = update_log_store.list_recent(exp_id, limit=1)
                        if logs and logs[0].get("source") != "system":
                            changed_fields = [c.get("field", "") for c in logs[0].get("changes", [])]
                            if changed_fields:
                                modified.append(f"{exp_id}（{', '.join(changed_fields[:3])}）")
                if modified:
                    lines.append(f"近期被修改的实验: {', '.join(modified[:3])}。")
            except Exception:
                pass

        self._l0_generated_at = datetime.now()
        return "\n".join(lines) if lines else "暂无实验记录。"

    def get_l0_generated_at(self):
        return self._l0_generated_at

    # -- global context (compressed history) --

    def get_global_context(self) -> str:
        fp = self._global_context_path()
        if fp.exists():
            with open(fp, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data.get("compressed", "")
        return ""

    def update_global_context(self, compressed_text: str,
                              uncompressed_thread_ids: list[str] | None = None,
                              recently_modified_exps: list[str] | None = None) -> None:
        data = {
            "compressed": compressed_text,
            "uncompressed_thread_ids": uncompressed_thread_ids or [],
            "recently_modified_exps": recently_modified_exps or [],
            "last_compressed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(self._global_context_path(), "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, indent=2)

    # -- runtime state --

    def save_current_state(self, agent_state: dict) -> None:
        with open(self._current_state_path(), "w", encoding="utf-8") as f:
            yaml.dump(agent_state, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, indent=2)

    def load_current_state(self) -> dict | None:
        fp = self._current_state_path()
        if not fp.exists():
            return None
        with open(fp, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    # -- child agent state --

    def save_child_state(self, thread_id: str, agent_state: dict) -> None:
        with open(self._child_state_path(thread_id), "w", encoding="utf-8") as f:
            yaml.dump(agent_state, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, indent=2)

    def load_child_state(self, thread_id: str) -> dict | None:
        fp = self._child_state_path(thread_id)
        if not fp.exists():
            return None
        with open(fp, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def delete_child_state(self, thread_id: str) -> None:
        fp = self._child_state_path(thread_id)
        if fp.exists():
            fp.unlink()

    # -- user profile --

    def get_user_profile(self) -> dict:
        idx = self._load_index()
        return dict(idx.get("user_profile", {}))

    def update_user_profile(self, exp_data: dict) -> None:
        """record 线程 done 时更新画像。exp_data 为产出的 EXP 完整 dict。"""
        idx = self._load_index()
        profile = idx.setdefault("user_profile", {})

        # Experimenter count
        experimenter = (exp_data.get("experimenter") or "").strip()
        if experimenter:
            counts = profile.setdefault("experimenter_counts", {})
            counts[experimenter] = counts.get(experimenter, 0) + 1
            # Update default
            if counts.get(experimenter, 0) >= counts.get(profile.get("default_experimenter", ""), 0):
                profile["default_experimenter"] = experimenter

        # Tag counts (full recalculation from experiment store)
        # This is done externally (experiment_store passed by caller) -
        # here we only mark profile as stale. The actual recalc is lightweight
        # and can be done by the caller passing all experiments.
        profile["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        self._index_cache = idx
        self._save_index()

    def recalc_tag_counts(self, experiment_store) -> None:
        """全量重算标签计数（从所有实验 YAML 中统计）。在 record 线程 done 时调用。"""
        idx = self._load_index()
        profile = idx.setdefault("user_profile", {})
        tag_counts = {}
        if experiment_store:
            for exp in experiment_store.list_all_full():
                for tag in exp.get("tags", []):
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        profile["tag_counts"] = tag_counts
        profile["frequent_tags"] = [t for t, _ in sorted_tags[:10]]
        profile["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        self._index_cache = idx
        self._save_index()
