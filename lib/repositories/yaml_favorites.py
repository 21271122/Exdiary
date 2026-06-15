import yaml
import re
from pathlib import Path
from datetime import datetime
from lib.repositories.base import (
    AbstractExperimentRepository, AbstractAnalysisRepository,
    AbstractThreadRepository, AbstractFavoritesRepository,
    AbstractUpdateLogRepository,
)




class YamlFavoritesRepository(AbstractFavoritesRepository):
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
