"""Parse the agent's final message into a Recommendation.

Mirrors enrichment.parser.parse_deltas, but NEVER raises: a silent or malformed
cycle must not crash the daemon, so it returns None (the assistant simply does
not publish -> signal-not-noise). Enforces the thesis: a recommendation missing
the WHAT (``suggested_action``) or the WHY (``rationale``) is dropped.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from redrobin.domain.assistant import Recommendation
from redrobin.domain.events import Severity

logger = logging.getLogger(__name__)

_VALID_SEVERITIES = {s.value for s in Severity}


def _strip_fences(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def parse_recommendation(raw: str, *, correlation_id: str | None) -> Recommendation | None:
    text = _strip_fences(raw)
    if not text:
        return None
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("assistant: unparseable recommendation JSON — staying silent")
        return None
    if not isinstance(data, dict):
        return None
    if data.get("speak") is False:
        return None  # the model explicitly chose to stay silent
    action = str(data.get("suggested_action") or "").strip()
    rationale = str(data.get("rationale") or "").strip()
    if not action or not rationale:
        return None  # WHAT+WHY are mandatory; without them it is outsourcing, not advice
    risk_raw = str(data.get("opsec_risk", "INFO")).strip().upper()
    risk = Severity(risk_raw) if risk_raw in _VALID_SEVERITIES else Severity.INFO
    confidence = str(data.get("confidence") or "").strip()
    _refs = data.get("references")
    refs_raw = _refs if isinstance(_refs, list) else []
    references = [str(r) for r in refs_raw if isinstance(r, (str, int, float))]
    return Recommendation(
        suggested_action=action,
        rationale=rationale,
        opsec_risk=risk,
        confidence=confidence,
        references=references,
        correlation_id=correlation_id,
    )


__all__ = ["parse_recommendation"]
