"""OpenAI-compatible /v1/chat/completions provider — the workhorse.

Covers local Qwen (Docker Model Runner), OpenAI, vLLM, LM Studio, OpenRouter, etc.
`post(url, json, headers, timeout) -> (status, data)` is injectable for tests.
"""
from __future__ import annotations

import httpx

from .base import LlmHttpError, LlmTimeout


def _default_post(url, json, headers, timeout):
    try:
        r = httpx.post(url, json=json, headers=headers, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise LlmTimeout(f"llm timeout: {exc}") from exc
    except httpx.HTTPError as exc:
        raise LlmHttpError(0, str(exc)) from exc
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, model: str, api_key: str | None = None, *, post=_default_post) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._key = api_key
        self._post = post

    def chat(self, system, user, *, json_mode, temperature, max_tokens, timeout_s) -> str:
        body = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        status, data = self._post(self._url, body, headers, timeout_s)
        # Some OpenAI-compatible servers (e.g. LM Studio) reject response_format=json_object —
        # they accept only 'json_schema' or 'text'. Retry once without it: the prompt already
        # asks for JSON and the caller extracts the JSON span from the reply.
        if status == 400 and "response_format" in body:
            body.pop("response_format")
            status, data = self._post(self._url, body, headers, timeout_s)
        if status != 200 or not data:
            raise LlmHttpError(status, str(data)[:200] if data else "")
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmHttpError(status, f"unexpected response shape: {exc}") from exc
