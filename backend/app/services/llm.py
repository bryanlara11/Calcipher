"""Thin async wrapper around Google Gemini (`google-genai`). Entirely optional.

Every caller must tolerate `LLMError` and fall back to deterministic logic.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    name: str

    async def generate_json(self, system: str, prompt: str, schema: type[BaseModel]) -> dict[str, Any]: ...

    async def generate_text(self, system: str, prompt: str) -> str: ...


class GeminiClient:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash", timeout_s: float = 12.0) -> None:
        from google import genai  # deferred: optional dependency at import time

        self._client = genai.Client(api_key=api_key)
        self.model = model
        self.name = model
        self.timeout_s = timeout_s

    async def _generate(self, system: str, prompt: str, **config: Any) -> str:
        from google.genai import types

        try:
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(system_instruction=system, **config),
                ),
                timeout=self.timeout_s,
            )
        except TimeoutError as exc:
            raise LLMError("Gemini request timed out") from exc
        except Exception as exc:  # network, auth, quota, safety block...
            raise LLMError(f"Gemini request failed: {exc.__class__.__name__}") from exc
        text = getattr(response, "text", None)
        if not text:
            raise LLMError("Gemini returned an empty response")
        return text

    async def generate_json(self, system: str, prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
        text = await self._generate(
            system,
            prompt,
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=schema,
        )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError("Gemini returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise LLMError("Gemini JSON was not an object")
        return data

    async def generate_text(self, system: str, prompt: str) -> str:
        return await self._generate(system, prompt, temperature=0.2, max_output_tokens=600)
