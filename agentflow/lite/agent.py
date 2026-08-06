from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from agentflow.lite.client import LiteLLMClient
from agentflow.lite.router import ModelRouter
from agentflow.lite.tools import Tool, ToolRegistry
from agentflow.lite.types import ChatResult, Message, Usage


class BudgetExceededError(Exception):
    """Raised when the accumulated token usage exceeds the configured budget."""

    def __init__(self, usage: Usage):
        self.usage = usage
        super().__init__(f"token budget exceeded: {usage.total_tokens} tokens used")


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    messages: list[Message]
    usage: Usage
    iterations: int
    finish_reason: str | None = None


class LiteAgent:
    """Minimal tool-calling agent loop over a direct LLM client or router."""

    def __init__(
        self,
        client: LiteLLMClient | None = None,
        *,
        router: ModelRouter | None = None,
        role: str | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
        tools: list[Tool] | ToolRegistry | None = None,
        max_iterations: int = 8,
        max_total_tokens: int | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        if (client is None) == (router is None):
            raise ValueError("pass exactly one of `client` or `router`")
        if router is not None and role is None:
            raise ValueError("`role` is required when using a router")
        if client is not None and model is None:
            raise ValueError("`model` is required when using a client")
        self.client = client
        self.router = router
        self.role = role
        self.model = model
        self.system_prompt = system_prompt
        if tools is None:
            self.registry = ToolRegistry()
        elif isinstance(tools, ToolRegistry):
            self.registry = tools
        else:
            self.registry = ToolRegistry(tools)
        self.max_iterations = max_iterations
        self.max_total_tokens = max_total_tokens
        self.temperature = temperature
        self.max_tokens = max_tokens

    def _chat(self, messages: list[Message], **extra: Any) -> ChatResult:
        kwargs: dict[str, Any] = dict(extra)
        tools = self.registry.to_openai_tools()
        if tools:
            kwargs["tools"] = tools
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if self.router is not None:
            return self.router.chat(self.role or "", messages, **kwargs)
        assert self.client is not None and self.model is not None
        return self.client.chat(messages, model=self.model, **kwargs)

    def _run(
        self,
        user_input: str,
        history: list[Message] | None,
        extra: dict[str, Any] | None = None,
    ) -> AgentResult:
        messages: list[Message] = []
        if self.system_prompt:
            messages.append(Message(role="system", content=self.system_prompt))
        messages.extend(history or [])
        messages.append(Message(role="user", content=user_input))

        usage = Usage()
        last_result: ChatResult | None = None
        iterations = 0
        for _ in range(self.max_iterations):
            iterations += 1
            result = self._chat(messages, **(extra or {}))
            usage = usage + result.usage
            if self.max_total_tokens is not None and usage.total_tokens > self.max_total_tokens:
                raise BudgetExceededError(usage)
            last_result = result
            messages.append(result.message)
            if result.message.tool_calls:
                for call in result.message.tool_calls:
                    messages.append(
                        Message(role="tool", tool_call_id=call.id, content=self.registry.dispatch(call))
                    )
                continue
            return AgentResult(
                text=result.message.content or "",
                messages=messages,
                usage=usage,
                iterations=iterations,
                finish_reason=result.finish_reason,
            )
        text = ""
        if last_result is not None and last_result.message.content:
            text = last_result.message.content
        return AgentResult(
            text=text,
            messages=messages,
            usage=usage,
            iterations=iterations,
            finish_reason="max_iterations",
        )

    def run(self, user_input: str, history: list[Message] | None = None) -> AgentResult:
        return self._run(user_input, history, None)

    def run_structured(self, user_input: str, schema: type[BaseModel]) -> BaseModel:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
        }
        result = self._run(user_input, None, {"response_format": response_format})
        return schema.model_validate_json(result.text)
