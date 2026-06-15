import yaml
import re
from pathlib import Path
from datetime import datetime
from lib.repositories.base import (
    AbstractExperimentRepository, AbstractAnalysisRepository,
    AbstractThreadRepository, AbstractFavoritesRepository,
    AbstractUpdateLogRepository,
)




class YamlExperimentRepository(AbstractExperimentRepository):
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
