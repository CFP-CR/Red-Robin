"""redrobin/application/redactor.py — Pattern-based redaction of secrets.

Closes Phase 2 gap #19: Layer B sent operator commands to Anthropic with
secrets (passwords, NTLM hashes, emails, JWTs, AWS keys) in clear text.

The Redactor is applied to the operator's command at the very last step
of Layer B's user-message build, so the LLM never sees raw secrets on
the wire. The verdict flow is unchanged — only the wire payload to
Anthropic is sanitized.

Idempotency is a hard contract: re-running ``redact()`` on already-
redacted text must produce the same output. This guards against double-
redaction artifacts if the pipeline ever runs the redactor twice (e.g.,
during a future replay path).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class _Rule:
    pattern: re.Pattern[str]
    replacement: str


_RULES: tuple[_Rule, ...] = (
    # `/password:VALUE`, `/pwd:VALUE`, `/pass=VALUE` — flag-style (mimikatz,
    # Rubeus, etc.). Preserves the flag name so the LLM can still reason about
    # the command shape.
    _Rule(
        pattern=re.compile(r"(?i)(/(?:password|pwd|pass))\s*[:=]\s*\S+"),
        replacement=r"\1:[REDACTED:PASSWORD]",
    ),
    # `password:VALUE`, `pwd=VALUE`, `pass: VALUE` — key-value style. The flag
    # rule above runs first so `/password:X` is already gone by the time this
    # matches a bare `password:X`.
    _Rule(
        pattern=re.compile(r"(?i)\b(password|pwd|pass)\s*[:=]\s*\S+"),
        replacement=r"\1:[REDACTED:PASSWORD]",
    ),
    # NTLM hash pair (LM:NT, each 32 hex chars).
    _Rule(
        pattern=re.compile(r"\b[a-fA-F0-9]{32}:[a-fA-F0-9]{32}\b"),
        replacement="[REDACTED:NTLM]",
    ),
    # RFC-5322-lite email.
    _Rule(
        pattern=re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"),
        replacement="[REDACTED:EMAIL]",
    ),
    # JWT (three base64url segments separated by dots; header starts `eyJ`).
    _Rule(
        pattern=re.compile(r"\b(eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})\b"),
        replacement="[REDACTED:JWT]",
    ),
    # AWS access key ID.
    _Rule(
        pattern=re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
        replacement="[REDACTED:AWS_KEY]",
    ),
)


class Redactor:
    """Apply pattern rules to a text and return the redacted output.

    Rules are applied in order; the first matching rule wins per span. The
    placeholder tokens (``[REDACTED:*]``) are themselves non-matching for
    every rule above, which gives the idempotency guarantee.
    """

    def redact(self, text: str) -> str:
        result = text
        for rule in _RULES:
            result = rule.pattern.sub(rule.replacement, result)
        return result
