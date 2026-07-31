"""Parse an LLM extraction response (a JSON string) into WorkingMemoryDeltas.

Tolerates ```json code fences (models often wrap JSON). Raises DeltaParseError on
unparseable / non-object input; the enricher does ONE retry, then logs + drops.
Whitelisting + provenance live in WorkingMemoryDeltas.from_payload (domain).
"""

from __future__ import annotations

import json
import re

from redrobin.domain.memory import WorkingMemoryDeltas

# Strip a leading ```json / ``` opener and a trailing ``` closer (+ surrounding
# whitespace); IGNORECASE so ```JSON also matches. Body whitespace is consumed by
# the \s* on each side. Only one fence pair is stripped (models don't nest fences).
_FENCE_OPEN = re.compile(r"^\s*```(?:json)?\s*", re.IGNORECASE)
_FENCE_CLOSE = re.compile(r"\s*```\s*$")


class DeltaParseError(Exception):
    """Raised when an LLM response is not valid JSON object output."""


def parse_deltas(raw: str, *, source_beacon_id: str | None) -> WorkingMemoryDeltas:
    text = _FENCE_CLOSE.sub("", _FENCE_OPEN.sub("", raw.strip()))
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise DeltaParseError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DeltaParseError(f"expected a JSON object, got {type(data).__name__}")
    return WorkingMemoryDeltas.from_payload(data, source_beacon_id=source_beacon_id)
