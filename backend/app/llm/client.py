"""OpenAI 兼容 LLM 客户端。直接走 httpx，不引 SDK。

支持：普通对话、流式对话、JSON 结构化输出。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.errors import LLMError, LLMTimeoutError

logger = logging.getLogger(__name__)


def join_url(base: str, path: str) -> str:
    """按 OpenAI SDK 的约定拼接：base_url 原样使用，只追加路径。

    不自动补 /v1 —— 有的服务挂在 host 根（/chat/completions），
    有的挂在 /v1 下（/v1/chat/completions），只有使用者知道是哪种。
    自动补 /v1 会把前者拼成 404。
    """
    return f"{base.rstrip('/')}{path}"


class _Retryable(Exception):
    pass


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = base_url or settings.llm_base_url
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model
        self.timeout = timeout or settings.llm_timeout_s
        self._client: httpx.AsyncClient | None = None

    def _make_client(self) -> httpx.AsyncClient:
        """每次请求都新建 client。

        不复用连接是刻意的：内网 LiteLLM 反代在同一条 keep-alive 连接上，
        先 GET /models 再 POST /chat/completions 会返回 404（首个请求正常，
        之后全 404），即使显式发 Connection: close 也没用。这种假 404 极易
        被误判成「路由不存在」。LLM 调用本身是秒级，TCP 握手开销可忽略。
        """
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=15.0),
            headers=headers,
            limits=httpx.Limits(max_keepalive_connections=0, max_connections=16),
        )

    async def close(self) -> None:
        pass

    # ---------------- 非流式 ----------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(_Retryable),
        reraise=True,
    )
    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = join_url(self.base_url, "/chat/completions")
        try:
            async with self._make_client() as client:
                resp = await client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"模型响应超时（{self.timeout}s）") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"模型请求失败: {exc}") from exc

        if resp.status_code in (429, 500, 502, 503, 504):
            raise _Retryable(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            detail = resp.text[:300]
            raise LLMError(f"模型接口返回 {resp.status_code}: {detail}")

        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise LLMError("模型返回非 JSON") from exc

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, str] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or settings.llm_max_tokens,
            "stream": False,
        }
        if response_format:
            payload["response_format"] = response_format

        data = await self._post(payload)
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("模型返回空 choices")
        return {
            "content": choices[0].get("message", {}).get("content", ""),
            "finish_reason": choices[0].get("finish_reason"),
            "usage": data.get("usage") or {},
            "model": data.get("model"),
        }

    async def json_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        res = await self.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens or settings.utility_max_tokens,
            response_format={"type": "json_object"},
            model=model or settings.utility_model,
        )
        raw = (res.get("content") or "").strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # 有些模型会包一层 ```json
            start, end = raw.find("{"), raw.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(raw[start : end + 1])
                except json.JSONDecodeError:
                    pass
            logger.warning("json_chat 解析失败: %s", raw[:200])
            return {}

    # ---------------- 流式 ----------------

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """逐块产出 {type: delta|usage|done, ...}。"""
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or settings.llm_max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        url = join_url(self.base_url, "/chat/completions")

        try:
            async with self._make_client() as client:
                async with client.stream("POST", url, json=payload) as resp:
                    if resp.status_code >= 400:
                        body = (await resp.aread()).decode("utf-8", "ignore")[:300]
                        raise LLMError(f"模型接口返回 {resp.status_code}: {body}")

                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if chunk == "[DONE]":
                            break
                        try:
                            data = json.loads(chunk)
                        except json.JSONDecodeError:
                            continue

                        usage = data.get("usage")
                        if usage:
                            yield {"type": "usage", "usage": usage}

                        for choice in data.get("choices") or []:
                            delta = choice.get("delta") or {}
                            text = delta.get("content")
                            if text:
                                yield {"type": "delta", "text": text}
                            if choice.get("finish_reason"):
                                yield {"type": "done", "finish_reason": choice["finish_reason"]}
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"模型流式响应超时（{self.timeout}s）") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"模型流式请求失败: {exc}") from exc


_client: LLMClient | None = None


def get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


async def close_client() -> None:
    global _client
    if _client:
        await _client.close()
        _client = None
