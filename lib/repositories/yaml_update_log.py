import yaml
import re
from pathlib import Path
from datetime import datetime
from lib.repositories.base import (
    AbstractExperimentRepository, AbstractAnalysisRepository,
    AbstractThreadRepository, AbstractFavoritesRepository,
    AbstractUpdateLogRepository,
)




class YamlUpdateLogRepository(AbstractUpdateLogRepository):
    """实验更新日志持久化。每次实验字段修改时记录 old→new diff。"""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def _filepath(self, exp_id: str) -> Path:
        return self.path / f"{exp_id}.yaml"

    def _load(self, exp_id: str) -> dict:
        fp = self._filepath(exp_id)
        if fp.exists():
            with open(fp, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def _save(self, exp_id: str, data: dict) -> None:
        fp = self._filepath(exp_id)
        with open(fp, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, indent=2)

    def _next_entry_id(self, exp_id: str) -> str:
        """生成 UPD-NNN-XXX 格式的条目 ID。NNN 来自实验编号，XXX 递增。"""
        m = re.match(r"EXP-\d{4}-(\d{3})", exp_id)
        exp_num = m.group(1) if m else "000"
        data = self._load(exp_id)
        entries = data.get("entries", [])
        max_n = 0
        prefix = f"UPD-{exp_num}-"
        for entry in entries:
            eid = entry.get("id", "")
            if eid.startswith(prefix):
                try:
                    max_n = max(max_n, int(eid.split("-")[-1]))
                except ValueError:
                    pass
        return f"{prefix}{max_n + 1:03d}"

    def append(self, exp_id: str, source: str, changes: list[dict],
               context: dict | None = None, thread_id: str | None = None) -> str:
        """追加一条更新日志。changes 中每项含 {path, field, old, new}。
        old 值由调用方在读盘后传入（磁盘是 truth）。返回 entry_id。"""
        data = self._load(exp_id)
        data["experiment_id"] = exp_id
        if "entries" not in data:
            data["entries"] = []

        entry = {
            "id": self._next_entry_id(exp_id),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": source,
            "thread_id": thread_id,
            "context": context or {},
            "changes": changes,
        }
        data["entries"].insert(0, entry)
        self._save(exp_id, data)
        return entry["id"]

    def list_recent(self, exp_id: str, limit: int = 5) -> list[dict]:
        """返回最近 N 条更新条目（按时间倒序）。"""
        data = self._load(exp_id)
        return data.get("entries", [])[:limit]

    def list_all(self, exp_id: str) -> list[dict]:
        """返回全部更新条目（按时间倒序）。"""
        data = self._load(exp_id)
        return data.get("entries", [])

    def get_entry(self, exp_id: str, entry_id: str) -> dict | None:
        """获取单条更新条目。"""
        for entry in self.list_all(exp_id):
            if entry.get("id") == entry_id:
                return entry
        return None
