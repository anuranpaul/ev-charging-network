"""
app/core/llm_provider.py — ChargeWise India Geo Service

LLM provider abstraction for AI-powered features (Enhancements 2 & 4).

Supports three backends:
  - mock     — returns a template-formatted string (no network call)
  - openai   — wraps the OpenAI Chat Completions API
  - bedrock  — wraps AWS Bedrock InvokeModel

Configuration via environment variables:
  LLM_PROVIDER         = mock | openai | bedrock (default: mock)
  LLM_API_KEY          = sk-...  (required for openai)
  LLM_MODEL            = gpt-4o-mini (default for openai)
  LLM_TIMEOUT_SECONDS  = 10

Design reference: design.md §AI Enhancement 2: LLM Provider Abstraction
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol — the interface all providers implement
# ---------------------------------------------------------------------------

class LLMProvider(Protocol):
    """Protocol for LLM text generation backends."""

    async def generate(
        self, system: str, user: str, max_tokens: int = 300
    ) -> str:
        """Generate a completion given system and user prompts."""
        ...


# ---------------------------------------------------------------------------
# MockProvider — deterministic, no external dependency
# ---------------------------------------------------------------------------

class MockProvider:
    """
    Returns a template-formatted explanation without any network call.

    Useful for local development, testing, and environments without LLM
    credentials. The output is deterministic and instant.
    """

    async def generate(
        self, system: str, user: str, max_tokens: int = 300
    ) -> str:
        """Parse factor scores from the user prompt and build a summary."""
        # Extract key values from the structured user prompt
        lines = user.strip().split("\n")
        factors: list[str] = []
        score_line = ""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("Overall score:"):
                score_line = stripped
            elif stripped.startswith("- ") and "/100" in stripped:
                # e.g. "- Population density (1 km): 68/100 (34,210 residents)"
                factors.append(stripped.lstrip("- "))

        # Build a structured summary from the factor lines
        strongest = ""
        weakest = ""
        max_score = -1
        min_score = 101
        for f in factors:
            # Parse "Name: XX/100 (...)"
            try:
                name_part = f.split(":")[0].strip()
                score_part = f.split(":")[1].strip().split("/")[0]
                val = int(score_part)
                if val > max_score:
                    max_score = val
                    strongest = name_part
                if val < min_score:
                    min_score = val
                    weakest = name_part
            except (IndexError, ValueError):
                continue

        factor_summary = "; ".join(factors) if factors else "factors not parsed"

        return (
            f"{score_line}. "
            f"Strongest factor: {strongest} ({max_score}/100). "
            f"Weakest factor: {weakest} ({min_score}/100). "
            f"Factor breakdown: {factor_summary}."
        )


# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------

class OpenAIProvider:
    """Wraps the OpenAI Chat Completions API (gpt-4o-mini default)."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = int(os.getenv("LLM_TIMEOUT_SECONDS", "10"))

    async def generate(
        self, system: str, user: str, max_tokens: int = 300
    ) -> str:
        """Call the OpenAI Chat Completions API."""
        try:
            import httpx
        except ImportError:
            raise RuntimeError("httpx is required for OpenAIProvider")

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.3,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()


# ---------------------------------------------------------------------------
# BedrockProvider
# ---------------------------------------------------------------------------

class BedrockProvider:
    """Wraps AWS Bedrock InvokeModel (Claude Haiku default)."""

    def __init__(
        self,
        model_id: str = "anthropic.claude-3-haiku-20240307-v1:0",
    ) -> None:
        self._model_id = model_id
        self._timeout = int(os.getenv("LLM_TIMEOUT_SECONDS", "10"))

    async def generate(
        self, system: str, user: str, max_tokens: int = 300
    ) -> str:
        """Call AWS Bedrock InvokeModel."""
        import json

        try:
            import boto3
        except ImportError:
            raise RuntimeError("boto3 is required for BedrockProvider")

        client = boto3.client("bedrock-runtime")

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "temperature": 0.3,
        })

        response = client.invoke_model(
            modelId=self._model_id,
            contentType="application/json",
            accept="application/json",
            body=body,
        )

        result = json.loads(response["body"].read())
        return result["content"][0]["text"].strip()


# ---------------------------------------------------------------------------
# Factory — instantiate the configured provider
# ---------------------------------------------------------------------------

def create_llm_provider() -> LLMProvider:
    """
    Instantiate the LLM provider based on LLM_PROVIDER env var.

    Returns a MockProvider when unconfigured or set to "mock".
    """
    provider_name = os.getenv("LLM_PROVIDER", "mock").lower()

    if provider_name == "openai":
        api_key = os.getenv("LLM_API_KEY", "")
        if not api_key:
            logger.warning(
                "LLM_PROVIDER=openai but LLM_API_KEY is empty — falling back to mock"
            )
            return MockProvider()
        model = os.getenv("LLM_MODEL", "gpt-4o-mini")
        logger.info("LLM provider initialised", extra={"provider": "openai", "model": model})
        return OpenAIProvider(api_key=api_key, model=model)

    if provider_name == "bedrock":
        model_id = os.getenv("LLM_MODEL", "anthropic.claude-3-haiku-20240307-v1:0")
        logger.info("LLM provider initialised", extra={"provider": "bedrock", "model": model_id})
        return BedrockProvider(model_id=model_id)

    # Default: mock
    logger.info("LLM provider initialised", extra={"provider": "mock"})
    return MockProvider()


# ---------------------------------------------------------------------------
# Module-level singleton (import from routers)
# ---------------------------------------------------------------------------

#: Application-wide LLM provider instance.
llm: LLMProvider = create_llm_provider()
