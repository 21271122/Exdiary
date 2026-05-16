import yaml
import re
from pathlib import Path
from datetime import datetime


class ExperimentStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def next_id(self) -> str:
        year = datetime.now().strftime("%Y")
        pattern = re.compile(rf"^EXP-{year}-(\d{{3}})\.yaml$")
        max_n = 0
        for f in self.path.iterdir():
            m = pattern.match(f.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
        return f"EXP-{year}-{max_n + 1:03d}"

    def save(self, experiment: dict) -> str:
        exp_id = experiment.get("id") or self.next_id()
        experiment["id"] = exp_id
        filepath = self.path / f"{exp_id}.yaml"
        with open(filepath, "w", encoding="utf-8") as f:
            yaml.dump(experiment, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, indent=2)
        return exp_id

    def load(self, exp_id: str) -> dict | None:
        filepath = self.path / f"{exp_id}.yaml"
        if not filepath.exists():
            return None
        with open(filepath, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def list_all(self) -> list[dict]:
        experiments = []
        for filepath in sorted(self.path.glob("EXP-*.yaml"), reverse=True):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data:
                    experiments.append({
                        "id": data.get("id"),
                        "title": data.get("title", ""),
                        "date": data.get("date", ""),
                        "experimenter": data.get("experimenter", ""),
                        "status": data.get("status", "planned"),
                        "tags": data.get("tags", []),
                    })
            except Exception:
                continue
        return experiments

    def summarize_all(self, exp_ids: list[str] | None = None) -> str:
        if exp_ids:
            ids_set = set(exp_ids)
            experiments = [e for e in self.list_all() if e["id"] in ids_set]
        else:
            experiments = self.list_all()
        parts = []
        for exp in experiments:
            full = self.load(exp["id"])
            if not full:
                continue
            results = full.get("results", {}) or {}
            obs = full.get("observations", {}) or {}
            obs_items = obs.get("items", []) if isinstance(obs, dict) else []
            parts.append(
                f"### {exp['id']}: {exp['title']}\n"
                f"Date: {exp['date']} | Status: {exp['status']} | Tags: {', '.join(exp['tags'])}\n"
                f"Purpose: {str(full.get('purpose', ''))[:300]}\n"
                f"Conclusion: {str(full.get('conclusion', ''))[:300]}\n"
                f"Key Results: {str(results.get('qualitative', ''))[:200]}\n"
                f"Observations: {'; '.join(obs_items)[:200]}\n"
            )
        return "\n---\n".join(parts) if parts else "No experiments found."

    def update(self, exp_id: str, experiment: dict) -> bool:
        filepath = self.path / f"{exp_id}.yaml"
        if not filepath.exists():
            return False
        experiment["id"] = exp_id
        with open(filepath, "w", encoding="utf-8") as f:
            yaml.dump(experiment, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, indent=2)
        return True

    def delete(self, exp_id: str) -> bool:
        filepath = self.path / f"{exp_id}.yaml"
        if filepath.exists():
            filepath.unlink()
            return True
        return False

    def list_all_full(self) -> list[dict]:
        experiments = []
        for filepath in sorted(self.path.glob("EXP-*.yaml"), reverse=True):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data:
                    experiments.append(data)
            except Exception:
                continue
        return experiments

    def count(self) -> int:
        return len(list(self.path.glob("EXP-*.yaml")))


class FavoritesStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = None

    def _load(self) -> dict:
        if self._data is not None:
            return self._data
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = yaml.safe_load(f) or {}
        else:
            self._data = {}
        if "pinned" not in self._data:
            self._data["pinned"] = []
        if "collections" not in self._data:
            self._data["collections"] = {"默认收藏夹": []}
        return self._data

    def _save(self):
        if self._data is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.dump(self._data, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, indent=2)

    def is_pinned(self, exp_id: str) -> bool:
        data = self._load()
        return exp_id in data.get("pinned", [])

    def is_favorited(self, exp_id: str, collection: str = "默认收藏夹") -> bool:
        data = self._load()
        return exp_id in data.get("collections", {}).get(collection, [])

    def toggle_pin(self, exp_id: str) -> dict:
        data = self._load()
        pinned = data.get("pinned", [])
        if exp_id in pinned:
            pinned.remove(exp_id)
            self._save()
            return {"ok": True, "pinned": False}
        if len(pinned) >= 3:
            return {"ok": False, "error": "最多只能置顶 3 个实验"}
        pinned.append(exp_id)
        data["pinned"] = pinned
        self._save()
        return {"ok": True, "pinned": True}

    def toggle_favorite(self, exp_id: str, collection: str = "默认收藏夹") -> dict:
        data = self._load()
        collections = data.get("collections", {})
        if collection not in collections:
            collections[collection] = []
        if exp_id in collections[collection]:
            collections[collection].remove(exp_id)
            self._save()
            return {"ok": True, "favorited": False}
        collections[collection].append(exp_id)
        data["collections"] = collections
        self._save()
        return {"ok": True, "favorited": True}

    def get_pinned(self) -> list[str]:
        data = self._load()
        return list(data.get("pinned", []))

    def get_collections(self) -> dict:
        data = self._load()
        return dict(data.get("collections", {}))

    def create_collection(self, name: str) -> dict:
        data = self._load()
        if name in data.get("collections", {}):
            return {"ok": False, "error": "收藏夹已存在"}
        data["collections"][name] = []
        self._save()
        return {"ok": True}

    def delete_collection(self, name: str) -> dict:
        data = self._load()
        if name not in data.get("collections", {}):
            return {"ok": False, "error": "收藏夹不存在"}
        if name == "默认收藏夹":
            return {"ok": False, "error": "不能删除默认收藏夹"}
        del data["collections"][name]
        self._save()
        return {"ok": True}

    def add_to_collection(self, exp_id: str, collection: str) -> dict:
        data = self._load()
        if collection not in data.get("collections", {}):
            data["collections"][collection] = []
        if exp_id not in data["collections"][collection]:
            data["collections"][collection].append(exp_id)
        self._save()
        return {"ok": True}

    def remove_from_collection(self, exp_id: str, collection: str = "默认收藏夹") -> dict:
        data = self._load()
        if collection in data.get("collections", {}):
            lst = data["collections"][collection]
            if exp_id in lst:
                lst.remove(exp_id)
        self._save()
        return {"ok": True}


class AnalysisStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def next_id(self) -> str:
        year = datetime.now().strftime("%Y")
        pattern = re.compile(rf"^ANAL-{year}-(\d{{3}})\.yaml$")
        max_n = 0
        for f in self.path.iterdir():
            m = pattern.match(f.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
        return f"ANAL-{year}-{max_n + 1:03d}"

    def save(self, analysis: dict) -> str:
        aid = analysis.get("id") or self.next_id()
        analysis["id"] = aid
        filepath = self.path / f"{aid}.yaml"
        with open(filepath, "w", encoding="utf-8") as f:
            yaml.dump(analysis, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, indent=2)
        return aid

    def load(self, aid: str) -> dict | None:
        filepath = self.path / f"{aid}.yaml"
        if not filepath.exists():
            return None
        with open(filepath, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def list_all(self) -> list[dict]:
        results = []
        for fp in sorted(self.path.glob("ANAL-*.yaml"), reverse=True):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data:
                    results.append(data)
            except Exception:
                continue
        return results

    def delete(self, aid: str) -> bool:
        filepath = self.path / f"{aid}.yaml"
        if filepath.exists():
            filepath.unlink()
            return True
        return False
