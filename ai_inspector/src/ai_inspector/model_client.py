"""Explicitly configured Meta Model API endpoint using the Chat Completions tool protocol."""

import json
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import APIError


class FunctionCall(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    arguments: Annotated[str, Field(max_length=8192)]


class ToolCall(BaseModel):
    id: Annotated[str, Field(min_length=1, max_length=200)]
    type: Literal["function"]
    function: FunctionCall


class AssistantMessage(BaseModel):
    model_config = ConfigDict(strict=True)
    role: Literal["assistant"]
    content: Annotated[str, Field(max_length=16000)] | None = None
    tool_calls: Annotated[list[ToolCall], Field(max_length=1)] | None = None


def validated_message(value: dict) -> dict:
    try:
        message = AssistantMessage.model_validate(value)
        if not message.tool_calls and not (message.content or "").strip():
            raise ValueError
        result = message.model_dump(exclude_none=True)
        if not message.tool_calls:
            result.pop("tool_calls", None)
        return result
    except (ValueError, TypeError, ValidationError):
        raise APIError(502, "invalid_model_output", "The model returned an invalid assistant message") from None


class MetaModelClient:
    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self.transport = transport

    @property
    def configured(self):
        return bool(self.settings.meta_base_url and self.settings.meta_model and self.settings.meta_api_key)

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        if not self.configured:
            raise APIError(503, "model_not_configured", "Configure the Meta Model API endpoint, model and API key on the server", retryable=True)
        body = {"model": self.settings.meta_model, "messages": messages, "stream": False}
        if tools:
            body.update(tools=tools, tool_choice="auto", parallel_tool_calls=False)
        try:
            with httpx.Client(timeout=self.settings.meta_timeout_s, follow_redirects=False, transport=self.transport) as client:
                with client.stream("POST", self.settings.meta_base_url.rstrip("/") + "/chat/completions",
                                   headers={"Authorization": "Bearer " + self.settings.meta_api_key}, json=body) as response:
                    if response.status_code != 200:
                        retryable = response.status_code in (408, 429) or response.status_code >= 500
                        raise APIError(503 if retryable else 502, "model_unavailable",
                                       "The configured model request was unsuccessful", retryable=retryable)
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > 1024 * 1024:
                            raise APIError(502, "invalid_model_output", "The model response exceeded the size limit")
            result = json.loads(content)
            if not isinstance(result, dict) or not isinstance(result.get("choices"), list):
                raise ValueError
            choice, = result["choices"]
            if not isinstance(choice, dict) or choice.get("finish_reason") not in ("stop", "tool_calls"):
                raise ValueError
            return validated_message(choice["message"])
        except httpx.HTTPError:
            # Upstream errors may contain credentials, URLs or request text. Never persist them.
            raise APIError(503, "model_unavailable", "The model request timed out or could not connect", retryable=True) from None
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise APIError(502, "invalid_model_output", "The model returned an invalid completion") from None
