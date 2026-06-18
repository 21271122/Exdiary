"""LLM 客户端 — OpenAI SDK 封装，含 ABC 抽象接口 + 指数退避重试。"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI, APIError, APITimeoutError, RateLimitError, APIConnectionError
from openai.types.chat import ChatCompletionChunk


@dataclass
class LLMResponse:
    content: str
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    usage: dict[str, int] | None = None


@dataclass
class StreamEvent:
    """流式输出中的单个事件。"""
    type: str  # "text" | "tool_call" | "done"
    content: str = ""           # text 事件时的增量文本
    tool_name: str = ""         # tool_call 事件时的工具名
    tool_args: str = ""         # tool_call 事件时的参数 JSON（增量累积）
    finished: LLMResponse | None = None  # done 事件时的完整响应


class AbstractLLMClient(ABC):
    """LLM 客户端抽象接口。只有 chat() 需要子类实现。"""

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
        reasoning_effort: str | None = None,
    ) -> LLMResponse: ...

    def structured_extract(
        self,
        prompt: str,
        system_prompt: str,
        output_schema: dict[str, Any],
    ) -> dict[str, Any]:
        resp = self.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            tools=[{
                "type": "function",
                "function": {
                    "name": "save_experiment",
                    "description": "Save parsed experiment record",
                    "parameters": output_schema,
                },
            }],
        )
        if not resp.tool_calls:
            raise RuntimeError(f"Model did not call the function. Response: {resp.content[:200]}")
        return dict(json.loads(resp.tool_calls[0]["function"]["arguments"]))

    def analyze(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
    ) -> str:
        resp = self.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
        )
        return resp.content


class LLMClient(AbstractLLMClient):
    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-v4-pro",
        base_url: str = "https://api.deepseek.com",
    ):
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,   # 禁用 SDK 内置重试，完全由自定义逻辑接管
            timeout=30.0,
        )
        self.model = model

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort

        last_exception: Exception | None = None
        for attempt in range(3):
            try:
                resp = self.client.chat.completions.create(**kwargs)
                msg = resp.choices[0].message
                return LLMResponse(
                    content=msg.content or "",
                    reasoning=getattr(msg, "reasoning_content", "") or "",
                    tool_calls=[
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in (msg.tool_calls or [])
                    ],
                    usage={
                        "prompt_tokens": resp.usage.prompt_tokens,
                        "completion_tokens": resp.usage.completion_tokens,
                    }
                    if resp.usage
                    else None,
                )
            except RateLimitError as e:
                last_exception = e
                if attempt < 2:
                    retry_after = _parse_retry_after(e)
                    wait = max(retry_after, 5.0) if retry_after > 0 else _backoff(attempt)
                    time.sleep(wait)
            except (APITimeoutError, APIConnectionError) as e:
                last_exception = e
                if attempt < 2:
                    time.sleep(_backoff(attempt))
            except APIError as e:
                last_exception = e
                if attempt < 2:
                    time.sleep(_backoff(attempt))

        raise last_exception  # type: ignore[misc]

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
        reasoning_effort: str | None = None,
    ) -> Generator[StreamEvent, None, LLMResponse]:
        """流式 chat：逐 token 产出 text 事件，工具调用时产 tool_call 事件，
        流结束后产 done 事件，yield 结束返回完整 LLMResponse。"""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort

        last_exception: Exception | None = None
        for attempt in range(3):
            try:
                stream = self.client.chat.completions.create(**kwargs)
                content_chunks: list[str] = []
                reasoning_chunks: list[str] = []
                tool_calls_acc: dict[int, dict[str, Any]] = {}
                final_usage: dict[str, int] | None = None

                for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta is None:
                        continue

                    # 文本增量
                    if delta.content:
                        content_chunks.append(delta.content)
                        yield StreamEvent(type="text", content=delta.content)

                    # 推理增量
                    if getattr(delta, "reasoning_content", None):
                        reasoning_chunks.append(delta.reasoning_content)

                    # 工具调用增量
                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {
                                    "id": tc_delta.id or "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    tool_calls_acc[idx]["function"]["name"] += tc_delta.function.name
                                if tc_delta.function.arguments:
                                    tool_calls_acc[idx]["function"]["arguments"] += tc_delta.function.arguments
                            # 推送工具名
                            if tc_delta.function and tc_delta.function.name:
                                yield StreamEvent(
                                    type="tool_call",
                                    tool_name=tc_delta.function.name,
                                    tool_args=tool_calls_acc[idx]["function"]["arguments"],
                                )

                    # usage
                    if chunk.usage:
                        final_usage = {
                            "prompt_tokens": chunk.usage.prompt_tokens,
                            "completion_tokens": chunk.usage.completion_tokens,
                        }

                # 构建完整响应
                raw_tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc.keys())]
                resp = LLMResponse(
                    content="".join(content_chunks),
                    reasoning="".join(reasoning_chunks),
                    tool_calls=raw_tool_calls if raw_tool_calls else None,
                    usage=final_usage,
                )
                yield StreamEvent(type="done", finished=resp)
                return resp

            except RateLimitError as e:
                last_exception = e
                if attempt < 2:
                    retry_after = _parse_retry_after(e)
                    wait = max(retry_after, 5.0) if retry_after > 0 else _backoff(attempt)
                    time.sleep(wait)
            except (APITimeoutError, APIConnectionError) as e:
                last_exception = e
                if attempt < 2:
                    time.sleep(_backoff(attempt))
            except APIError as e:
                last_exception = e
                if attempt < 2:
                    time.sleep(_backoff(attempt))

        raise last_exception  # type: ignore[misc]


def _backoff(attempt: int) -> float:
    """指数退避: 2s, 4s, 8s."""
    return 2.0 ** (attempt + 1)


def _parse_retry_after(exc: RateLimitError) -> float:
    """尝试从响应头读取 Retry-After。"""
    try:
        headers = getattr(exc, "response", None)
        if headers is not None:
            val = headers.headers.get("Retry-After")
            if val is not None:
                return float(val)
    except Exception:
        pass
    return 0.0
