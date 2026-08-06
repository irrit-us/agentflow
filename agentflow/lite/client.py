from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from agentflow.lite.types import ChatResult, Message, ToolCall, Usage

_ERROR_BODY_LIMIT = 500


class LLMError(Exception):
    """Error raised for failed chat completion requests."""

    def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
        self.status_code = status_code
        summary = (body or "")[:_ERROR_BODY_LIMIT]
        if status_code is not None:
            message = f"{message} (status={status_code})"
        if summary:
            message = f"{message}: {summary}"
        super().__init__(message)
        self.body = summary


class LiteLLMClient:
    """Minimal synchronous OpenAI-compatible chat completions client."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        api_key_env: str | None = None,
        default_headers: dict[str, str] | None = None,
        timeout: float = 120,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        if api_key is None and api_key_env is not None:
            api_key = os.environ.get(api_key_env)
        self.api_key = api_key
        self.default_headers = dict(default_headers or {})
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = httpx.Client(transport=transport, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LiteLLMClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.default_headers}
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        return headers

    def chat(
        self,
        messages: list[Message],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        **extra: Any,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_openai() for m in messages],
        }
        if tools:
            payload["tools"] = tools
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format
        payload.update(extra)

        data = self._post_with_retries(f"{self.base_url}/chat/completions", payload)
        return self._parse_response(data)

    def _post_with_retries(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        last_error: LLMError | None = None
        for attempt in range(max(self.max_retries, 0) + 1):
            try:
                response = self._client.post(url, json=payload, headers=self._headers())
            except httpx.HTTPError as exc:
                last_error = LLMError(f"HTTP request failed: {exc}")
                if attempt < self.max_retries:
                    time.sleep(min(2.0**attempt, 30.0))
                    continue
                raise last_error from exc
            if response.status_code == 429 or response.status_code >= 500:
                last_error = LLMError("LLM request failed", response.status_code, response.text)
                if attempt < self.max_retries:
                    time.sleep(self._retry_delay(response, attempt))
                    continue
                raise last_error
            if response.status_code >= 400:
                raise LLMError("LLM request failed", response.status_code, response.text)
            try:
                return response.json()
            except json.JSONDecodeError as exc:
                raise LLMError("Invalid JSON in LLM response", response.status_code, response.text) from exc
        assert last_error is not None  # pragma: no cover
        raise last_error  # pragma: no cover

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
        return min(2.0**attempt, 30.0)

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> ChatResult:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("LLM response contains no choices", None, json.dumps(data))
        choice = choices[0]
        raw_message = choice.get("message") or {}
        tool_calls = [
            ToolCall(
                id=call.get("id", ""),
                name=(call.get("function") or {}).get("name", ""),
                arguments=_parse_arguments((call.get("function") or {}).get("arguments")),
            )
            for call in raw_message.get("tool_calls") or []
        ]
        raw_usage = data.get("usage") or {}
        return ChatResult(
            message=Message(
                role="assistant",
                content=raw_message.get("content"),
                tool_calls=tool_calls or None,
            ),
            usage=Usage(
                prompt_tokens=raw_usage.get("prompt_tokens", 0),
                completion_tokens=raw_usage.get("completion_tokens", 0),
                total_tokens=raw_usage.get("total_tokens", 0),
            ),
            finish_reason=choice.get("finish_reason"),
            model=data.get("model"),
        )


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
