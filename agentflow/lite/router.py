from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict, Field

from agentflow.lite.client import LiteLLMClient, LLMError
from agentflow.lite.types import ChatResult, Message


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    model: str
    base_url: str
    api_key_env: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class ModelRouter:
    """Routes chat calls to role-based fallback chains of model profiles."""

    def __init__(self, roles: dict[str, list[ModelProfile]], transport: httpx.BaseTransport | None = None):
        self.roles = roles
        self._transport = transport
        self._clients: dict[str, LiteLLMClient] = {}

    def profile(self, role: str) -> ModelProfile:
        chain = self.roles.get(role)
        if not chain:
            available = ", ".join(sorted(self.roles)) or "(none)"
            raise KeyError(f"unknown role '{role}'; available roles: {available}")
        return chain[0]

    def _client_for(self, profile: ModelProfile) -> LiteLLMClient:
        client = self._clients.get(profile.name)
        if client is None:
            client = LiteLLMClient(
                base_url=profile.base_url,
                api_key_env=profile.api_key_env,
                default_headers=profile.headers,
                transport=self._transport,
            )
            self._clients[profile.name] = client
        return client

    def chat(self, role: str, messages: list[Message], **kwargs: object) -> ChatResult:
        chain = self.roles.get(role)
        if not chain:
            available = ", ".join(sorted(self.roles)) or "(none)"
            raise KeyError(f"unknown role '{role}'; available roles: {available}")
        last_error: LLMError | None = None
        for profile in chain:
            merged = dict(kwargs)
            if profile.temperature is not None:
                merged.setdefault("temperature", profile.temperature)
            if profile.max_tokens is not None:
                merged.setdefault("max_tokens", profile.max_tokens)
            try:
                return self._client_for(profile).chat(messages, model=profile.model, **merged)  # type: ignore[arg-type]
            except LLMError as exc:
                last_error = exc
        assert last_error is not None  # pragma: no cover
        raise last_error

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()
