"""A scripted stand-in for anthropic.Anthropic so the whole pipeline runs offline."""

from __future__ import annotations

import itertools
from types import SimpleNamespace as NS
from typing import Any, Callable

_ids = itertools.count(1)


def text(t: str) -> NS:
    return NS(type="text", text=t)


def tool(name: str, input: Any) -> NS:
    return NS(type="tool_use", id=f"toolu_{next(_ids)}", name=name, input=input)


class FakeLLM:
    """
    brain_script: list of turns. Each turn is a list of blocks (text/tool) or a
    callable(kwargs) -> list of blocks, evaluated when that request is made.
    parse_handlers: {SchemaClass: callable(kwargs) -> instance}
    """

    def __init__(self) -> None:
        self.brain_script: list[Any] = []
        self.parse_handlers: dict[type, Callable[[dict], Any]] = {}
        self.research_text = "Competitor A leads with price. Source: https://example.com/a"
        self.brain_requests: list[dict] = []
        self.parse_requests: list[dict] = []
        self.beta = NS(messages=NS(create=self._create, parse=self._parse))

    def _create(self, **kw):
        tools = kw.get("tools") or []
        if any(t.get("type", "").startswith("web_search") for t in tools):
            return NS(stop_reason="end_turn", content=[
                text(self.research_text),
                NS(type="web_search_tool_result", content=[NS(url="https://example.com/a")]),
            ])
        self.brain_requests.append(kw)
        if not self.brain_script:
            return NS(stop_reason="end_turn", content=[text("(no script)")])
        step = self.brain_script.pop(0)
        blocks = step(kw) if callable(step) else step
        stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
        return NS(stop_reason=stop, content=blocks)

    def _parse(self, **kw):
        self.parse_requests.append(kw)
        schema = kw["output_format"]
        handler = self.parse_handlers.get(schema)
        if handler is None:
            raise AssertionError(f"no fake handler for {schema.__name__}")
        return NS(stop_reason="end_turn", parsed_output=handler(kw), content=[])


def last_tool_results(kw: dict) -> list[dict]:
    msgs = kw["messages"]
    for m in reversed(msgs):
        if m["role"] == "user" and isinstance(m["content"], list):
            return m["content"]
    return []
