"""
Exdiary Agent 调试追踪器 — 记录所有 LLM 调用、prompt、响应和解析结果。

用法:
    tracer = create_debug_tracer("experiments")
    tracer.log_llm_call(...)
    tracer.log_parse_error(...)
    tracer.log_context(...)

所有日志保存到 experiments/_debug/<时间戳>/ 目录下。
"""

import json
from pathlib import Path
from datetime import datetime


class DebugTracer:
    """按对话 session 组织 LLM 调用日志"""

    def __init__(self, session_dir: Path):
        self.dir = Path(session_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index = 0
        self.session_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _write(self, filename: str, content: str) -> Path:
        filepath = self.dir / filename
        filepath.write_text(content, encoding="utf-8")
        self.index += 1
        return filepath

    def log_conversation_start(self, user_message: str) -> None:
        """记录对话开始"""
        self._write(f"{self.index:03d}_conversation_start.md",
            f"# 对话开始\n\n**时间**: {self.session_start}\n\n"
            f"**用户首条消息**:\n\n```\n{user_message[:2000]}\n```\n")

    def log_llm_call(self, stage: str, system_prompt: str,
                     user_prompt: str, temperature: float,
                     raw_response: str) -> Path:
        """记录一次完整的 LLM 调用"""
        # 截断过长的 prompt
        sp = system_prompt
        if len(sp) > 8000:
            sp = sp[:4000] + "\n\n... (截断) ...\n\n" + sp[-4000:]

        up = user_prompt
        if len(up) > 4000:
            up = up[:4000] + "\n\n... (截断) ..."

        rr = raw_response
        if len(rr) > 6000:
            rr = rr[:6000] + "\n\n... (截断) ..."

        content = (
            f"# LLM Call #{self.index:03d} — Stage: {stage}\n\n"
            f"**时间**: {datetime.now().strftime('%H:%M:%S')}\n"
            f"**Temperature**: {temperature}\n\n"
            f"## System Prompt ({len(system_prompt)} chars)\n\n```\n{sp}\n```\n\n"
            f"## User Prompt ({len(user_prompt)} chars)\n\n```\n{up}\n```\n\n"
            f"## Raw Response ({len(raw_response)} chars)\n\n```\n{rr}\n```\n"
        )
        return self._write(f"{self.index:03d}_{stage}_call.md", content)

    def log_parse_error(self, stage: str, raw_response: str, error: str) -> Path:
        """记录 JSON 解析失败"""
        rr = raw_response[:6000] if len(raw_response) > 6000 else raw_response
        content = (
            f"# Parse Error #{self.index:03d} — Stage: {stage}\n\n"
            f"**时间**: {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"## 错误信息\n\n```\n{error}\n```\n\n"
            f"## LLM 返回的原始文本 ({len(raw_response)} chars)\n\n```\n{rr}\n```\n"
        )
        return self._write(f"{self.index:03d}_{stage}_parse_error.md", content)

    def log_context(self, label: str, content) -> Path:
        """记录中间上下文数据（摘要、引用详情等）"""
        if isinstance(content, str):
            text = content
        else:
            text = json.dumps(content, ensure_ascii=False, indent=2)
        if len(text) > 10000:
            text = text[:10000] + "\n\n... (截断) ..."
        return self._write(f"{self.index:03d}_context_{label}.md",
            f"# {label}\n\n```\n{text}\n```\n")

    def log_state(self, state_dict: dict) -> Path:
        """记录当前 AgentState"""
        text = json.dumps(state_dict, ensure_ascii=False, indent=2)
        if len(text) > 8000:
            text = text[:8000] + "\n\n... (截断) ..."
        return self._write(f"{self.index:03d}_state.md",
            f"# AgentState\n\n```json\n{text}\n```\n")


def create_debug_tracer(base_dir: str) -> DebugTracer:
    """在 base_dir/_debug/<时间戳>/ 下创建新的调试追踪器"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = Path(base_dir) / "_debug" / ts
    return DebugTracer(session_dir)
