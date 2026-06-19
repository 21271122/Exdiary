"""跨实验分析服务。吸收 lib/analyzer.py 的 analyze_experiments()。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from lib.core.prompts import ANALYSIS_SYSTEM_PROMPT


class AnalysisService:
    def __init__(self, exp_repo: Any, analysis_repo: Any, analyze_llm: Any):
        self.exp_repo = exp_repo
        self.analysis_repo = analysis_repo
        self.analyze_llm = analyze_llm

    def run_analysis(self, query: str, refs: list[str]) -> dict[str, Any]:
        """执行分析 → 写 AnalysisStore → 更新实验关联 → 返回报告。"""
        summary = self.exp_repo.summarize_all(exp_ids=refs)
        analysis = self._analyze_experiments(summary, query)
        anal_id = self.analysis_repo.save({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "question": query,
            "selected_ids": refs,
            "analysis": analysis,
        })
        for exp_id in refs:
            exp = self.exp_repo.load(exp_id)
            if exp:
                analyzed = exp.get("analyzed_in", [])
                if anal_id not in analyzed:
                    analyzed.append(anal_id)
                    exp["analyzed_in"] = analyzed
                    self.exp_repo.save(exp)
        title = query[:40]
        for line in analysis.split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                title = line[:60]
                break
        return {"anal_id": anal_id, "title": title, "refs": refs, "analysis": analysis}

    def _analyze_experiments(self, summary_text: str, question: str) -> str:
        user_prompt = f"""EXPERIMENT RECORDS:
{summary_text}

RESEARCHER'S QUESTION: {question}

Please analyze the above experiments. Focus on the researcher's stated
question. Use only the analysis dimensions that are relevant. Omit
sections that don't apply.

Structure your response in exactly three sections as specified in the
system prompt: 事实呈现, 发现提示, 值得思考的问题."""
        return str(self.analyze_llm.analyze(ANALYSIS_SYSTEM_PROMPT, user_prompt))
