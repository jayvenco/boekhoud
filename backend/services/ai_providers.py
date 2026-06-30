from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Tuple
import httpx


@dataclass
class AIModel:
    id: str
    label: str


class AIProvider(ABC):
    """Provider-onafhankelijke interface. Nieuwe providers (Gemini, Grok, lokale
    LLM's, ...) worden toegevoegd door deze klasse te implementeren en te
    registreren in PROVIDERS — geen wijzigingen in de UI of routes nodig."""

    id: str
    name: str
    models: List[AIModel]

    @abstractmethod
    async def test_connection(self, api_key: str) -> Tuple[str, str]:
        """Returns (status, message). status is een van:
        'verbonden', 'ongeldig', 'onbereikbaar', 'niet_geconfigureerd'."""

    @abstractmethod
    async def complete(self, api_key: str, model: str, prompt: str) -> str:
        """Stuurt prompt naar de provider en geeft de ruwe tekstrespons terug."""

    def default_model(self) -> str:
        return self.models[0].id


class OpenAIProvider(AIProvider):
    id = "openai"
    name = "OpenAI (ChatGPT)"
    models = [
        AIModel("gpt-5", "GPT-5"),
        AIModel("gpt-5-mini", "GPT-5 Mini"),
        AIModel("gpt-4.1", "GPT-4.1"),
        AIModel("gpt-4.1-mini", "GPT-4.1 Mini"),
    ]

    async def test_connection(self, api_key: str) -> Tuple[str, str]:
        if not api_key:
            return "niet_geconfigureerd", "Geen API-sleutel ingesteld."
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            if resp.status_code == 200:
                return "verbonden", "Verbonden"
            if resp.status_code in (401, 403):
                return "ongeldig", "Ongeldig API-token"
            return "onbereikbaar", f"Provider niet bereikbaar (HTTP {resp.status_code})"
        except httpx.RequestError:
            return "onbereikbaar", "Provider niet bereikbaar"

    async def complete(self, api_key: str, model: str, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 500,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]


class AnthropicProvider(AIProvider):
    id = "anthropic"
    name = "Anthropic (Claude)"
    models = [
        AIModel("claude-opus-4-8", "Claude Opus"),
        AIModel("claude-sonnet-4-6", "Claude Sonnet"),
        AIModel("claude-haiku-4-5-20251001", "Claude Haiku"),
    ]

    async def test_connection(self, api_key: str) -> Tuple[str, str]:
        if not api_key:
            return "niet_geconfigureerd", "Geen API-sleutel ingesteld."
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self.models[-1].id,
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                )
            if resp.status_code == 200:
                return "verbonden", "Verbonden"
            if resp.status_code in (401, 403):
                return "ongeldig", "Ongeldig API-token"
            return "onbereikbaar", f"Provider niet bereikbaar (HTTP {resp.status_code})"
        except httpx.RequestError:
            return "onbereikbaar", "Provider niet bereikbaar"

    async def complete(self, api_key: str, model: str, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 500,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["content"][0]["text"]


PROVIDERS = {p.id: p for p in [OpenAIProvider(), AnthropicProvider()]}


def get_provider(provider_id: str):
    return PROVIDERS.get(provider_id)
