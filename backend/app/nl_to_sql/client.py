"""Abstracted LLM client.

The rest of the app depends only on the `LLMClient` interface, so the model
provider can be swapped (OpenAI today; an Indian-compliant / on-prem
OpenAI-compatible endpoint later) without touching application logic.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod

from app.config import get_settings


class LLMClient(ABC):
    @abstractmethod
    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        """Return the model's response parsed as a JSON object."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return an embedding vector per input text. Optional for subclasses that
        don't support embeddings (raises by default)."""
        raise NotImplementedError


class OpenAIClient(LLMClient):
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.openai_model
        self.embedding_model = settings.openai_embedding_model
        self._api_key = api_key or settings.openai_api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self._api_key)
        return self._client

    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        client = self._get_client()
        resp = client.chat.completions.create(
            model=self.model,
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = resp.choices[0].message.content or "{}"
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {
                "action": "out_of_scope",
                "message": "I couldn't produce a valid query for that. Please rephrase.",
            }

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._get_client()
        # Batch to stay well within request limits.
        out: list[list[float]] = []
        for i in range(0, len(texts), 256):
            batch = texts[i : i + 256]
            resp = client.embeddings.create(model=self.embedding_model, input=batch)
            out.extend([d.embedding for d in resp.data])
        return out


_client: LLMClient | None = None
_explorer_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = OpenAIClient()
    return _client


def get_explorer_llm() -> LLMClient:
    """A (possibly stronger) model used for Explorer idea generation."""
    global _explorer_client
    if _explorer_client is None:
        settings = get_settings()
        model = settings.openai_explorer_model.strip() or settings.openai_model
        _explorer_client = OpenAIClient(model=model)
    return _explorer_client


def set_llm_client(client: LLMClient) -> None:
    """Used by tests to inject a fake client."""
    global _client
    _client = client
