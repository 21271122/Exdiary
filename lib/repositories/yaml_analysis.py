import yaml
import re
from pathlib import Path
from datetime import datetime
from lib.repositories.base import (
    AbstractExperimentRepository, AbstractAnalysisRepository,
    AbstractThreadRepository, AbstractFavoritesRepository,
    AbstractUpdateLogRepository,
)




class YamlAnalysisRepository(AbstractAnalysisRepository):
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
