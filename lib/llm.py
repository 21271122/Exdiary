import json
from dataclasses import dataclass
from openai import OpenAI


@dataclass
class LLMResponse:
    content: str
    reasoning: str = ""
    tool_calls: list[dict] | None = None
    usage: dict | None = None


class LLMClient:
    def __init__(self, api_key: str, model: str = "deepseek-v4-pro",
                 base_url: str = "https://api.deepseek.com"):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def chat(self, messages, tools=None, temperature=0.3,
             reasoning_effort=None) -> LLMResponse:
        kwargs = {"model": self.model, "messages": messages, "temperature": temperature}
        if tools:
            kwargs["tools"] = tools
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort

        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        return LLMResponse(
            content=msg.content or "",
            reasoning=getattr(msg, "reasoning_content", "") or "",
            tool_calls=[{
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments}
            } for tc in (msg.tool_calls or [])],
            usage={"prompt_tokens": resp.usage.prompt_tokens,
                   "completion_tokens": resp.usage.completion_tokens}
            if resp.usage else None
        )

    def structured_extract(self, prompt: str, system_prompt: str,
                           output_schema: dict) -> dict:
        resp = self.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            tools=[{"type": "function", "function": {
                "name": "save_experiment",
                "description": "Save parsed experiment record",
                "parameters": output_schema
            }}]
        )
        if not resp.tool_calls:
            raise RuntimeError(f"Model did not call the function. Response: {resp.content[:200]}")
        return json.loads(resp.tool_calls[0]["function"]["arguments"])

    def analyze(self, system_prompt: str, user_prompt: str,
                temperature: float = 0.3) -> str:
        resp = self.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=temperature
        )
        return resp.content
