"""线程持久化存储。拆分自原 YamlThreadRepository：5 子类 + 1 Facade。"""

from __future__ import annotations

import re
import yaml
from pathlib import Path
from datetime import datetime
from typing import Any
from lib.repositories.base import (
    AbstractExperimentRepository,
    AbstractThreadRepository,
    AbstractUpdateLogRepository,
)


# ============================================================
# 子类 1: ThreadCrud — 线程文件 CRUD
# ============================================================


class ThreadCrud:
    """线程文件的 CRUD 操作。路径必须指向 _threads/ 目录。"""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def _thread_path(self, thread_id: str) -> Path:
        return self.path / f"{thread_id}.yaml"

    def next_id(self) -> str:
        year = datetime.now().strftime("%Y")
        pattern = re.compile(rf"^THR-{year}-(\d{{3}})\.yaml$")
        max_n = 0
        for f in self.path.glob("THR-*.yaml"):
            m = pattern.match(f.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
        return f"THR-{year}-{max_n + 1:03d}"

    def create(
        self,
        thread_type: str,
        messages: list[dict[str, Any]],
        index_mgr: "ThreadIndexManager",
    ) -> dict[str, Any]:
        """创建新线程文件 + 更新索引。需要 index_mgr 协同。"""
        tid = self.next_id()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        thread: dict[str, Any] = {
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
        index_mgr._append_thread_entry(tid, thread_type, "active", "", "", now[:10], now[:10])
        return thread

    def save(self, thread_data: dict[str, Any]) -> None:
        tid = thread_data.get("id")
        if not tid:
            raise ValueError("thread_data must have 'id'")
        fp = self._thread_path(tid)
        with open(fp, "w", encoding="utf-8") as f:
            yaml.dump(thread_data, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, indent=2)

    def load(self, thread_id: str) -> dict[str, Any] | None:
        fp = self._thread_path(thread_id)
        if not fp.exists():
            return None
        with open(fp, "r", encoding="utf-8") as f:
            return dict(yaml.safe_load(f))

    def list_recent(self, n: int = 5) -> list[dict[str, Any]]:
        """读取 index.yaml 的 threads 列表（轻量，不读完整文件）。"""
        # 需要 index 缓存——在 Facade 层传入
        raise NotImplementedError("list_recent requires index access; use Facade")


# ============================================================
# 子类 2: ThreadIndexManager — 索引 + 反向映射
# ============================================================


class ThreadIndexManager:
    """管理 index.yaml 的读写与缓存。路径必须指向 _threads/ 目录。"""

    def __init__(self, path: str) -> None:
        self._path = Path(path) / "index.yaml"
        self._cache: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._cache is not None:
            return self._cache
        if self._path.exists():
            with open(self._path, "r", encoding="utf-8") as f:
                self._cache = yaml.safe_load(f) or {}
        else:
            self._cache = {}
        self._cache.setdefault("active_thread", None)
        self._cache.setdefault("threads", [])
        self._cache.setdefault("exp_to_thread", {})
        self._cache.setdefault("anal_to_thread", {})
        self._cache.setdefault("user_profile", {
            "experimenter_counts": {},
            "default_experimenter": "",
            "tag_counts": {},
            "frequent_tags": [],
            "last_updated": "",
        })
        return self._cache

    def _save(self) -> None:
        if self._cache is None:
            return
        with open(self._path, "w", encoding="utf-8") as f:
            yaml.dump(self._cache, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, indent=2)

    def get_index(self) -> dict[str, Any]:
        return dict(self._load())

    def update_index(self, thread_data: dict[str, Any]) -> None:
        idx = self._load()
        tid = thread_data["id"]
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
        if thread_data.get("exp_generated") and thread_data["exp_generated"] not in idx["exp_to_thread"]:
            idx["exp_to_thread"][thread_data["exp_generated"]] = tid
        if thread_data.get("anal_generated") and thread_data["anal_generated"] not in idx["anal_to_thread"]:
            idx["anal_to_thread"][thread_data["anal_generated"]] = tid
        self._save()

    def _append_thread_entry(
        self, tid: str, ttype: str, status: str,
        title: str, summary: str, created: str, updated: str,
    ) -> None:
        idx = self._load()
        idx["threads"].insert(0, {
            "id": tid, "type": ttype, "status": status,
            "title": title, "summary": summary,
            "created": created, "updated": updated,
        })
        self._save()

    def list_recent(self, n: int = 5) -> list[dict[str, Any]]:
        idx = self._load()
        result: list[dict[str, Any]] = list(idx.get("threads", [])[:n])
        return result

    def get_active_id(self) -> str | None:
        idx = self._load()
        return idx.get("active_thread")

    def set_active_id(self, thread_id: str | None) -> None:
        idx = self._load()
        idx["active_thread"] = thread_id
        self._save()

    def get_user_profile(self) -> dict[str, Any]:
        idx = self._load()
        return dict(idx.get("user_profile", {}))

    def get_raw_cache(self) -> dict[str, Any]:
        """供 UserProfileStore 修改后回写。"""
        return self._load()


# ============================================================
# 子类 3: ThreadStateStore — 活跃线程 + Agent 运行时状态
# ============================================================


class ThreadStateStore:
    """管理活跃线程标记 + _current_state.yaml + *_child_state.yaml。"""

    def __init__(self, path: str) -> None:
        self._dir = Path(path)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _current_state_path(self) -> Path:
        return self._dir / "_current_state.yaml"

    def _child_state_path(self, thread_id: str) -> Path:
        return self._dir / f"{thread_id}_child_state.yaml"

    def get_active_thread(self, thread_crud: ThreadCrud, index_mgr: ThreadIndexManager) -> dict[str, Any] | None:
        active_id = index_mgr.get_active_id()
        if not active_id:
            return None
        return thread_crud.load(active_id)

    def set_active_thread(
        self, thread_id: str | None,
        thread_crud: ThreadCrud, index_mgr: ThreadIndexManager,
    ) -> None:
        prev = index_mgr.get_active_id()
        if thread_id is None:
            if prev:
                thread = thread_crud.load(prev)
                if thread:
                    thread["status"] = "done"
                    thread_crud.save(thread)
            index_mgr.set_active_id(None)
        else:
            if prev and prev != thread_id:
                thread = thread_crud.load(prev)
                if thread:
                    thread["status"] = "done"
                    thread_crud.save(thread)
            index_mgr.set_active_id(thread_id)

    def save_current_state(self, agent_state: dict[str, Any]) -> None:
        with open(self._current_state_path(), "w", encoding="utf-8") as f:
            yaml.dump(agent_state, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, indent=2)

    def load_current_state(self) -> dict[str, Any] | None:
        fp = self._current_state_path()
        if not fp.exists():
            return None
        with open(fp, "r", encoding="utf-8") as f:
            return dict(yaml.safe_load(f))

    def save_child_state(self, thread_id: str, agent_state: dict[str, Any]) -> None:
        with open(self._child_state_path(thread_id), "w", encoding="utf-8") as f:
            yaml.dump(agent_state, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, indent=2)

    def load_child_state(self, thread_id: str) -> dict[str, Any] | None:
        fp = self._child_state_path(thread_id)
        if not fp.exists():
            return None
        with open(fp, "r", encoding="utf-8") as f:
            return dict(yaml.safe_load(f))

    def delete_child_state(self, thread_id: str) -> None:
        fp = self._child_state_path(thread_id)
        if fp.exists():
            fp.unlink()


# ============================================================
# 子类 4: GlobalContextStore — L0 摘要 + 压缩历史
# ============================================================


class GlobalContextStore:
    """L0 全局摘要 + _global_context.yaml 压缩历史。"""

    def __init__(self, path: str) -> None:
        self._dir = Path(path)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._l0_generated_at: datetime | None = None

    def _path(self) -> Path:
        return self._dir / "_global_context.yaml"

    def build_global_summary(
        self,
        exp_repo: AbstractExperimentRepository | None,
        update_log_repo: AbstractUpdateLogRepository | None,
        recent_threads: list[dict[str, Any]],
        user_profile: dict[str, Any],
    ) -> str:
        """Python 确定性生成 L0 摘要。依赖由 Facade 注入。"""
        lines: list[str] = []

        total = exp_repo.count() if exp_repo else 0
        if total > 0:
            statuses = {"done": 0, "running": 0, "failed": 0, "planned": 0, "repeated": 0}
            all_exps = exp_repo.list_all_full() if exp_repo else []
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

        done_threads = [t for t in recent_threads if t.get("status") == "done"]
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

        freq_tags = user_profile.get("frequent_tags", [])
        if freq_tags:
            tag_display = ", ".join(
                f"{t}({user_profile.get('tag_counts', {}).get(t, '?')})"
                for t in freq_tags[:6]
            )
            lines.append(f"你的常用标签: {tag_display}。")

        if update_log_repo:
            try:
                modified = []
                for t in done_threads[:5]:
                    exp_id = t.get("exp_generated", "")
                    if exp_id and exp_id.startswith("EXP-"):
                        logs = update_log_repo.list_recent(exp_id, limit=1)
                        if logs and logs[0].get("source") != "system":
                            changed_fields = [c.get("field", "") for c in logs[0].get("changes", [])]
                            if changed_fields:
                                modified.append(f"{exp_id}（{', '.join(changed_fields[:3])}）")
                if modified:
                    lines.append(f"近期被修改的实验: {', '.join(modified[:3])}。")
            except Exception:
                pass

        self._l0_generated_at = datetime.now()
        result: str = "\n".join(lines) if lines else "暂无实验记录。"
        return result

    def get_l0_generated_at(self) -> datetime | None:
        return self._l0_generated_at

    def get_global_context(self) -> str:
        fp = self._path()
        if fp.exists():
            with open(fp, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return str(data.get("compressed", ""))
        return ""

    def update_global_context(
        self,
        compressed_text: str,
        uncompressed_thread_ids: list[str] | None = None,
        recently_modified_exps: list[str] | None = None,
    ) -> None:
        data = {
            "compressed": compressed_text,
            "uncompressed_thread_ids": uncompressed_thread_ids or [],
            "recently_modified_exps": recently_modified_exps or [],
            "last_compressed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(self._path(), "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, indent=2)


# ============================================================
# 子类 5: UserProfileStore — 用户画像 + 标签统计
# ============================================================


class UserProfileStore:
    """用户画像与标签频率统计。通过 ThreadIndexManager 读写 index.yaml。"""

    def get_user_profile(self, index_mgr: ThreadIndexManager) -> dict[str, Any]:
        return index_mgr.get_user_profile()

    def update_user_profile(self, exp_data: dict[str, Any], index_mgr: ThreadIndexManager) -> None:
        idx = index_mgr.get_raw_cache()
        profile = idx.setdefault("user_profile", {})

        experimenter = (exp_data.get("experimenter") or "").strip()
        if experimenter:
            counts = profile.setdefault("experimenter_counts", {})
            counts[experimenter] = counts.get(experimenter, 0) + 1
            if counts.get(experimenter, 0) >= counts.get(profile.get("default_experimenter", ""), 0):
                profile["default_experimenter"] = experimenter

        profile["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        # 写回: 直接调用 index _save（通过 _load/_save 模式不好，这里用 set_raw + 显式保存）
        # 简化: 修改 idx 后由 Facade 调用 index_mgr._save()

    def recalc_tag_counts(
        self, exp_repo: AbstractExperimentRepository | None, index_mgr: ThreadIndexManager,
    ) -> None:
        idx = index_mgr.get_raw_cache()
        profile = idx.setdefault("user_profile", {})
        tag_counts: dict[str, int] = {}
        if exp_repo:
            for exp in exp_repo.list_all_full():
                for tag in exp.get("tags", []):
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        profile["tag_counts"] = tag_counts
        profile["frequent_tags"] = [t for t, _ in sorted_tags[:10]]
        profile["last_updated"] = datetime.now().strftime("%Y-%m-%d")


# ============================================================
# Facade: ThreadRepository — 保持对外接口完全不变
# ============================================================


class ThreadRepository(AbstractThreadRepository):
    """Facade 委托给 5 个子类。对外接口与旧 YamlThreadRepository 完全一致。"""

    def __init__(self, path: str) -> None:
        self.crud = ThreadCrud(path)
        self.index = ThreadIndexManager(path)
        self.state = ThreadStateStore(path)
        self.context = GlobalContextStore(path)
        self.profile = UserProfileStore()

    # -- 线程 CRUD --

    def next_id(self) -> str:
        return self.crud.next_id()

    def create(self, thread_type: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return self.crud.create(thread_type, messages, self.index)

    def save(self, thread_data: dict[str, Any]) -> None:
        self.crud.save(thread_data)

    def load(self, thread_id: str) -> dict[str, Any] | None:
        return self.crud.load(thread_id)

    # -- 索引 --

    def get_index(self) -> dict[str, Any]:
        return self.index.get_index()

    def update_index(self, thread_data: dict[str, Any]) -> None:
        self.index.update_index(thread_data)

    # -- 活跃线程 --

    def get_active_thread(self) -> dict[str, Any] | None:
        return self.state.get_active_thread(self.crud, self.index)

    def set_active_thread(self, thread_id: str | None) -> None:
        self.state.set_active_thread(thread_id, self.crud, self.index)

    def list_recent(self, n: int = 5) -> list[dict[str, Any]]:
        return self.index.list_recent(n)

    # -- L0 摘要 --

    def build_global_summary(
        self,
        exp_repo: AbstractExperimentRepository,
        update_log_repo: AbstractUpdateLogRepository,
    ) -> str:
        recent = self.index.list_recent(5)
        profile = self.index.get_user_profile()
        return self.context.build_global_summary(exp_repo, update_log_repo, recent, profile)

    def get_l0_generated_at(self) -> datetime | None:
        return self.context.get_l0_generated_at()

    # -- 压缩历史 --

    def get_global_context(self) -> str:
        return self.context.get_global_context()

    def update_global_context(
        self,
        compressed_text: str,
        uncompressed_thread_ids: list[str] | None = None,
        recently_modified_exps: list[str] | None = None,
    ) -> None:
        self.context.update_global_context(compressed_text, uncompressed_thread_ids, recently_modified_exps)

    # -- Agent 运行时状态 --

    def save_current_state(self, agent_state: dict[str, Any]) -> None:
        self.state.save_current_state(agent_state)

    def load_current_state(self) -> dict[str, Any] | None:
        return self.state.load_current_state()

    # -- 子 Agent 状态 --

    def save_child_state(self, thread_id: str, agent_state: dict[str, Any]) -> None:
        self.state.save_child_state(thread_id, agent_state)

    def load_child_state(self, thread_id: str) -> dict[str, Any] | None:
        return self.state.load_child_state(thread_id)

    def delete_child_state(self, thread_id: str) -> None:
        self.state.delete_child_state(thread_id)

    # -- 用户画像 --

    def get_user_profile(self) -> dict[str, Any]:
        return self.profile.get_user_profile(self.index)

    def update_user_profile(self, exp_data: dict[str, Any]) -> None:
        self.profile.update_user_profile(exp_data, self.index)
        self.index._save()

    def recalc_tag_counts(self, exp_repo: AbstractExperimentRepository) -> None:
        self.profile.recalc_tag_counts(exp_repo, self.index)
        self.index._save()
