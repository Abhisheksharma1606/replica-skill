"""Shared plumbing for the Layer 3 specialists.

Each specialist is one focused Claude call that returns a validated Pydantic
object (structured outputs), so the orchestrator never parses free text.
Server-side refusal fallback is on: if a request is declined by a safety
classifier the API retries it on a fallback model inside the same call.
"""

from __future__ import annotations

import logging
from typing import Any, Type, TypeVar

import anthropic
from pydantic import BaseModel

from agency.config import Settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class SpecialistError(RuntimeError):
    pass


def cached_system(text: str) -> list[dict[str, Any]]:
    # Static prompts first, marked cacheable: repeated calls reuse the prefix.
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


class Specialist:
    name = "specialist"
    system_prompt = ""
    effort: str | None = None  # None -> settings.specialist_effort

    def __init__(self, llm: anthropic.Anthropic, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings

    def structured(self, user_content: str, schema: Type[T], *, effort: str | None = None,
                   max_tokens: int = 16000) -> T:
        try:
            resp = self.llm.beta.messages.parse(
                model=self.settings.model,
                max_tokens=max_tokens,
                system=cached_system(self.system_prompt),
                messages=[{"role": "user", "content": user_content}],
                output_format=schema,
                output_config={"effort": effort or self.effort or self.settings.specialist_effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.RateLimitError as exc:
            raise SpecialistError(f"{self.name}: rate limited, try again shortly") from exc
        except anthropic.APIStatusError as exc:
            raise SpecialistError(f"{self.name}: API error {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise SpecialistError(f"{self.name}: could not reach the API") from exc

        if resp.stop_reason == "refusal":
            raise SpecialistError(f"{self.name}: request was declined")
        if resp.stop_reason == "max_tokens":
            raise SpecialistError(f"{self.name}: output was cut off")
        parsed = getattr(resp, "parsed_output", None)
        if parsed is None:
            raise SpecialistError(f"{self.name}: no structured output returned")
        return parsed
