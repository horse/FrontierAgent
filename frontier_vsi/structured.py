from __future__ import annotations

import json
import re

from pydantic import BaseModel

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def parse_structured_output[T: BaseModel](text: str, model_type: type[T]) -> T:
    stripped = text.strip()
    match = _FENCE.fullmatch(stripped)
    if match:
        stripped = match.group(1).strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        raise ValueError("structured agent output must be one JSON object")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON structured output: {exc}") from exc
    return model_type.model_validate(payload)
