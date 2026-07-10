"""
Thin wrapper around the Anthropic Messages API shared by every agent.

Each agent (AMS Orchestrator, Incident Router, Job Remediation) is backed
by an Anthropic LLM call through this client, optionally each with its
own model configured in config.AnthropicSettings.
"""
import json
import logging
import re
from typing import Optional

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from config import AnthropicSettings

logger = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class AnthropicAgentClient:
    """
    Wraps anthropic.Anthropic so agents can request either free-text or
    strict-JSON completions without repeating boilerplate.
    """

    def __init__(self, settings: AnthropicSettings):
        if not settings.api_key:
            logger.warning(
                "ANTHROPIC_API_KEY is not set. Set it in your environment or .env file before running."
            )
        self.client = anthropic.Anthropic(api_key=settings.api_key)
        self.max_tokens = settings.max_tokens

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def complete(
        self, system_prompt: str, user_prompt: str, model: str,
        max_tokens: Optional[int] = None, temperature: Optional[float] = None,
    ) -> str:
        kwargs = {
            "model": model,
            "max_tokens": max_tokens or self.max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        response = self.client.messages.create(**kwargs)
        return "".join(block.text for block in response.content if block.type == "text")

    def complete_json(
        self, system_prompt: str, user_prompt: str, model: str,
        max_tokens: Optional[int] = None, temperature: Optional[float] = None,
    ) -> dict:
        """
        Calls the model with an instruction to return ONLY JSON, then
        robustly parses the result (stripping markdown code fences if the
        model adds them anyway).
        """
        strict_system = (
            f"{system_prompt}\n\n"
            "IMPORTANT: Respond with ONLY a single valid JSON object. "
            "Do not include any preamble, explanation, or markdown code fences."
        )
        raw = self.complete(strict_system, user_prompt, model=model, max_tokens=max_tokens, temperature=temperature)
        cleaned = _JSON_FENCE_RE.sub("", raw.strip()).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # last-resort: try to locate the outermost {...} block
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            logger.error("Failed to parse JSON from LLM response: %s", raw)
            raise
