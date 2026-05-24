"""Regression test for the Layer B SYSTEM_PROMPT tuning (#129).

The existing tests in test_layer_b_engine.py mock the LLM and assert on
Verdict parsing + KB call shape — they cannot catch SYSTEM_PROMPT
regressions because the prompt content never reaches the (mocked) LLM.

These tests pin the load-bearing structural phrases of the tuned prompt
introduced by Plan E (docs/superpowers/plans/2026-05-24-layer-b-prompt-
tuning.md). If a future change reverts or weakens the tuning, these
assertions fail — surfacing the regression in CI rather than on a live
beacon smoke.

NOT a behavioral test. The LLM's actual disposition under this prompt
is validated by the operator smoke in
adapters/cobaltstrike/tests/test_cna_smoke.md (T3-S1..S5).

Backlog ref: #129 — Layer B is over-cautious WARN for benign recon.
"""

from __future__ import annotations

from redrobin.application.layer_b_engine import SYSTEM_PROMPT


def test_prompt_states_default_pass_directive():
    """The tuning hinges on a default-to-PASS directive. If it's gone,
    the LLM regresses to the pre-#129 over-cautious WARN behavior."""
    assert "Default to PASS" in SYSTEM_PROMPT, (
        "missing default-to-PASS directive — #129 tuning reverted"
    )
    assert "materially dangerous" in SYSTEM_PROMPT, (
        "missing 'materially dangerous' qualifier — without it the "
        "default-to-PASS rule is unbounded"
    )


def test_prompt_distinguishes_shell_vehicle_from_payload():
    """The 'shell vehicle is not a per-command signal' section is the
    direct fix for the T14 smoke observation. It must be present and
    must name the cmd.exe-spawned-by-beacon anti-pattern explicitly."""
    assert "shell vehicle" in SYSTEM_PROMPT.lower(), (
        "missing 'shell vehicle' framing — #129's core distinction lost"
    )
    assert "cmd.exe" in SYSTEM_PROMPT, (
        "missing explicit cmd.exe reference — the LLM needs the "
        "anti-pattern named verbatim to stop WARN-ing on the vehicle"
    )
    assert "Do NOT WARN" in SYSTEM_PROMPT or "do not WARN" in SYSTEM_PROMPT.lower(), (
        "missing the explicit 'do not WARN on the vehicle alone' "
        "directive — the LLM has too much room to keep WARN-ing"
    )


def test_prompt_uses_three_category_taxonomy():
    """The taxonomy (STANDARD_RECON / RISKY_PAYLOAD /
    UNAMBIGUOUSLY_DANGEROUS) gives the LLM concrete category labels.
    Each must be present so the LLM has anchor points."""
    assert "STANDARD_RECON" in SYSTEM_PROMPT
    assert "RISKY_PAYLOAD" in SYSTEM_PROMPT
    assert "UNAMBIGUOUSLY_DANGEROUS" in SYSTEM_PROMPT


def test_prompt_names_specific_recon_commands():
    """An explicit allow-list of recon commands is the strongest signal
    we can give the LLM. Each name is an anchor point that maps directly
    to a smoke case (T3-S1..S5) or to common operator workflows."""
    recon_commands = (
        "ipconfig",
        "tasklist",
        "whoami",
        "hostname",
        "systeminfo",
        "net view",
        "route print",
        "arp -a",
        "netstat",
        "nslookup",
        "tracert",
        "klist",
    )
    for cmd in recon_commands:
        assert cmd in SYSTEM_PROMPT, (
            f"recon command {cmd!r} missing from STANDARD_RECON allow-list"
        )


def test_prompt_blocks_edr_tamper():
    """The UNAMBIGUOUSLY_DANGEROUS category must explicitly name the
    EDR-tamper / anti-forensics patterns added in audit fix M2. These
    are well-known T1562.001 / T1553.006 patterns that should never
    fall through to PASS."""
    edr_tamper_phrases = (
        "Add-MpPreference -ExclusionPath",
        "Stop-Service WinDefend",
        "bcdedit /set {default} testsigning on",
        "fsutil usn deletejournal",
    )
    for phrase in edr_tamper_phrases:
        assert phrase in SYSTEM_PROMPT, (
            f"EDR-tamper / anti-forensics phrase {phrase!r} missing from "
            f"UNAMBIGUOUSLY_DANGEROUS — Layer B would not BLOCK this"
        )


def test_prompt_does_not_pass_wmic():
    """`wmic.exe` is deprecated by Microsoft (Win11 22H2+) and is a
    well-known LOLBIN (Sigma `proc_creation_win_wmic_*` family, MITRE
    T1047). The audit fix M1 removed `wmic os get` /
    `wmic computersystem get` from STANDARD_RECON; this test ensures
    `wmic` does not appear in the allow-list anchor area. (It may still
    appear elsewhere in the prompt as a RISKY_PAYLOAD reference if a
    future tuning adds it explicitly.)"""
    # Find the STANDARD_RECON anchor and the block of text that follows it
    # up to the next category (RISKY_PAYLOAD). That block is the allow-list.
    standard_recon_idx = SYSTEM_PROMPT.find("STANDARD_RECON")
    risky_payload_idx = SYSTEM_PROMPT.find("RISKY_PAYLOAD")
    assert standard_recon_idx != -1, "STANDARD_RECON anchor not found"
    assert risky_payload_idx != -1, "RISKY_PAYLOAD anchor not found"
    assert risky_payload_idx > standard_recon_idx, (
        "STANDARD_RECON must appear before RISKY_PAYLOAD in the prompt"
    )
    allow_list_block = SYSTEM_PROMPT[standard_recon_idx:risky_payload_idx]
    assert "wmic" not in allow_list_block.lower(), (
        "`wmic` must NOT appear in the STANDARD_RECON allow-list — "
        "it is a deprecated LOLBIN (T1047, Sigma proc_creation_win_wmic_*)"
    )


def test_prompt_response_format_is_unchanged():
    """The JSON response shape (PASS | WARN | BLOCK + reason +
    suggestions) is the contract between the prompt and _parse_verdict.
    Plan E does NOT change the response shape; this test pins that fact
    so a future prompt tuning that accidentally adds ESCALATE as an
    allowed action surfaces as a test failure (ESCALATE in the prompt
    but not in _parse_verdict's mapping would silently downgrade to
    PASS — confusing reasoning trail)."""
    assert '"PASS" | "WARN" | "BLOCK"' in SYSTEM_PROMPT, (
        "JSON action format must remain PASS | WARN | BLOCK exactly — "
        "_parse_verdict at layer_b_engine.py:212-216 maps only these"
    )
    assert "ESCALATE" not in SYSTEM_PROMPT, (
        "ESCALATE is a Layer-A-only verdict; the LLM must never be "
        "asked to produce it because _parse_verdict has no mapping "
        "and would silently fall through to PASS"
    )


def test_prompt_includes_when_uncertain_prefer_pass():
    """The closing directive 'when uncertain → prefer PASS' is the
    deliberate bias correction that makes Layer B the calm second
    opinion rather than a second noisy gate."""
    # Match the section header phrasing OR an equivalent directive.
    lower = SYSTEM_PROMPT.lower()
    assert "when uncertain" in lower or "if uncertain" in lower, (
        "missing 'when uncertain' guidance — Layer B needs an explicit "
        "tiebreaker toward PASS for novel commands"
    )
    # The tiebreaker direction must be PASS, not WARN.
    assert "prefer PASS" in SYSTEM_PROMPT, (
        "'when uncertain' branch must direct toward PASS, not WARN"
    )


def test_prompt_acknowledges_engagement_context_override():
    """The engagement summary may legitimately elevate (e.g.,
    'CrowdStrike confirmed') or lower (e.g., 'lab environment') a
    command's default disposition. The prompt must name this so the
    LLM reads the engagement context first."""
    assert "engagement" in SYSTEM_PROMPT.lower(), (
        "engagement context awareness missing — Layer B should read "
        "the cacheable_engagement_summary block before applying defaults"
    )
