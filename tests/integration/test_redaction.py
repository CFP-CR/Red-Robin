"""Slice 12 integration: redactor scrubs secrets before LLM call.

Closes Phase 2 gap #19: Layer B sent operator commands to Anthropic with
secrets (passwords, NTLM hashes, emails, JWTs, AWS keys) in clear text.
The redactor sanitizes the operator command *before* it is embedded in
the user prompt — Anthropic never sees raw secrets on the wire.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from redrobin.application.kb_service import KBService
from redrobin.application.layer_b_engine import LayerBEngine
from redrobin.application.redactor import Redactor
from redrobin.domain.llm import LLMResponse


def test_redactor_masks_password_kv():
    r = Redactor()
    out = r.redact("logonpasswords /password:Hunter2 /domain:CORP")
    assert "Hunter2" not in out
    assert "[REDACTED:PASSWORD]" in out


def test_redactor_masks_email():
    r = Redactor()
    out = r.redact("send results to alice@bigcorp.com please")
    assert "alice@bigcorp.com" not in out
    assert "[REDACTED:EMAIL]" in out


def test_redactor_masks_ntlm_hash():
    r = Redactor()
    out = r.redact("hash: aad3b435b51404eeaad3b435b51404ee:31d6cfe0d16ae931b73c59d7e0c089c0")
    assert "31d6cfe0d16ae931b73c59d7e0c089c0" not in out
    assert "[REDACTED:NTLM]" in out


def test_redactor_idempotent():
    r = Redactor()
    out1 = r.redact("password:Hunter2")
    out2 = r.redact(out1)
    assert out1 == out2


def _mock_llm_capturing(captured: dict) -> AsyncMock:
    """LLM mock that records the kwargs of complete_with_cache."""
    llm = AsyncMock()

    async def _capture(**kwargs):
        captured.update(kwargs)
        return LLMResponse(
            text='```json\n{"action":"PASS","reason":"ok"}\n```',
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            output_tokens=10,
        )

    llm.complete_with_cache = _capture
    return llm


def _mock_kb() -> AsyncMock:
    kb = AsyncMock(spec=KBService)
    kb.retrieve = AsyncMock(return_value=[])
    return kb


@pytest.mark.asyncio
async def test_layer_b_redacts_user_prompt():
    """Layer B's actual LLM-bound user_message must NOT contain the raw secret."""
    captured: dict = {}
    llm = _mock_llm_capturing(captured)
    kb = _mock_kb()
    engine = LayerBEngine(llm=llm, kb=kb)

    secret_cmd = "logonpasswords /password:Hunter2"
    await engine.evaluate(command=secret_cmd, raw=secret_cmd)

    user_msg = captured.get("user_message", "")
    assert "Hunter2" not in user_msg
    assert "[REDACTED:PASSWORD]" in user_msg
