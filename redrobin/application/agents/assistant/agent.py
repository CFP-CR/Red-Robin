"""One agent cycle: assemble context -> run the Tool Runner loop -> parse.

The FROZEN system prompt (persona + the 5-part output contract) is sent as the
cached 1h prefix by the AgentLLM; the VOLATILE push-context (L2 working-memory
snapshot + L5 summary + the trigger command) goes in the user message, AFTER the
cached prefix (spec §6.B caching refinement 1: never co-locate the mutating
snapshot with the cached prefix). PUSH = small resident working-set; PULL =
the kb_retrieve tool. This is Anthropic's endorsed hybrid strategy (spec §6.B).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from redrobin.application.agents.assistant.llm import AgentLLM
from redrobin.application.agents.assistant.parser import (
    ParsedAnswer,
    parse_answer,
    parse_interpretation,
    parse_recommendation,
)
from redrobin.domain.assistant import AgentTool, Interpretation, Recommendation

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are Red Robin, an operation-aware AI copilot behind a red-team operator's C2. "
    "You are ADVISORY ONLY: you propose the next step, you never execute it and never "
    "dispatch to the beacon. You are SELECTIVE — speak only when you have something material "
    "and grounded; otherwise stay silent.\n\n"
    "BE TERSE AND SCANNABLE — the operator is mid-operation, reading this on a live console. "
    "No preamble, no restating the working memory back at them; every field is tight and earns "
    "its space. Prefer a few sharp words over a paragraph.\n\n"
    "You are given the operator's current engagement state (working memory) and the story so "
    "far. Use the kb_retrieve tool to ground tradecraft claims in the knowledge base; reason "
    "over what the tools and the state return, never from memory, and never invent a fact.\n\n"
    "Return ONLY a JSON object: your reply MUST begin with { and end with } — no preamble, "
    "no trailing commentary, no code fence. To stay silent, return "
    '{"speak": false}. To recommend, return these four fields (ALL required):\n'
    '{"suggested_action": "the concrete next step — the WHAT (one imperative line)",\n'
    ' "rationale": "the tradecraft WHY, in 1-2 tight sentences (without this it is '
    'outsourcing, not advising — but do not restate the state back)",\n'
    ' "opsec_risk": "one of INFO|WARN|DANGER — the detection risk, grounded in '
    'defenses_observed, with a short reason",\n'
    ' "confidence": "one line: how sure you are AND what you are explicitly NOT sure about"}'
)


def build_system_prompt() -> str:
    """The frozen persona + output contract. Static, so it caches as the Tool
    Runner's 1h prefix."""
    return _SYSTEM_PROMPT


# Default resident-set cap (spec §6.B refinement 2). A backwards-compat fallback
# for direct callers/tests; production threads ``settings.assistant_wm_max_items``.
_DEFAULT_WM_MAX_ITEMS = 20


def build_push_context(
    *,
    wm_snapshot: dict[str, Any],
    l5_summary: str,
    trigger_command: str,
    chat_history: str = "",
    wm_max_items: int = _DEFAULT_WM_MAX_ITEMS,
    proactive: bool = False,
) -> str:
    """Assemble the small, token-bounded PUSH working-set: L2 (current working
    memory) + L5 (story so far) + the chat thread + the trigger framing. Reactive
    framing names the command just run; proactive framing (``proactive=True``) asks
    for a watch-your-back scan of the accumulated state (no command was run). The L2
    snapshot is capped to the most-recent ``wm_max_items`` per category
    (context-rot guard, spec §6.B refinement 2). Non-mutating."""
    capped = cap_wm_snapshot(wm_snapshot, wm_max_items)
    wm_json = json.dumps(capped, ensure_ascii=False, sort_keys=True)
    lines = [
        "# Engagement working memory (L2)",
        wm_json,
        "",
        "# Story so far (L5)",
        l5_summary or "(no summary yet)",
        "",
        "# Conversation so far",
        chat_history or "(no conversation yet)",
        "",
    ]
    if proactive:
        lines += [
            "# Proactive check — no command was just run",
            "Scan the accumulated engagement state for a WATCH-YOUR-BACK concern the "
            "per-command view may have missed: an unaddressed OPSEC risk, a stalled "
            "objective, or an untaken opportunity.",
            "",
            "If there is a material, grounded one, retrieve any tradecraft you need and "
            'return the 5-part JSON. If not, return {"speak": false}.',
        ]
    else:
        lines += [
            "# What just happened",
            f"The operator ran: {trigger_command or '(unknown command)'}",
            "",
            "Given this, is there a material, grounded next-step recommendation? If yes, "
            'retrieve any tradecraft you need and return the 5-part JSON. If not, return {"speak": false}.',
        ]
    return "\n".join(lines)


def state_signature(wm_snapshot: dict[str, Any]) -> str:
    """A cheap, stable signature of engagement state for the trigger's novelty
    check — changes iff the working memory changed."""
    return json.dumps(wm_snapshot, ensure_ascii=False, sort_keys=True)


# The list-valued fact types the enricher extracts — exactly the
# ``WorkingMemoryDeltas.to_payload()`` keys.
_DELTA_LIST_KEYS = ("hosts", "credentials", "findings", "tools_used", "defenses_observed")


def cap_wm_snapshot(snapshot: dict[str, Any], max_items: int) -> dict[str, Any]:
    """Cap the resident working-memory set to the most-recent ``max_items`` per
    category (spec §6.B refinement 2 — context-rot guard).

    For each of the enricher's five list-valued fact categories, keep only the
    LAST ``max_items`` items — the tail, since the enricher appends newest-last —
    so a long engagement's WM lists don't grow unbounded and get re-sent every
    cycle (recall degrades as resident tokens grow). Non-mutating: returns a NEW
    dict, and a NEW list for any category it caps; the input ``snapshot`` (the
    assistant's cached L2) is never altered — mirrors
    :func:`apply_deltas_to_snapshot`. Scalar fields and other keys pass through.
    """
    capped = dict(snapshot)
    for key in _DELTA_LIST_KEYS:
        value = capped.get(key)
        if isinstance(value, list) and len(value) > max_items:
            capped[key] = value[-max_items:]
    return capped


def apply_deltas_to_snapshot(
    snapshot: dict[str, Any], deltas_payload: dict[str, Any]
) -> dict[str, Any]:
    """Forward-merge the just-published deltas onto the cached L2 snapshot.

    Production event order is ``working_memory.deltas`` -> ``enrichment.completed``
    -> ``working_memory_snapshot``. The assistant reacts on ``enrichment.completed`` —
    one step BEFORE the authoritative snapshot for that command lands — so on its own
    it would reason over a one-step-stale snapshot missing THIS command's facts.
    Extending the snapshot's list fields with the deltas the assistant already
    received closes that gap. Shallow list-extend only: the authoritative
    ``working_memory_snapshot`` supersedes this within a beat, so we deliberately do
    NOT replicate the reducer's dedup/merge. Returns a NEW dict (and NEW lists) — the
    input ``snapshot`` is never mutated.
    """
    merged = dict(snapshot)
    for key in _DELTA_LIST_KEYS:
        delta_items = deltas_payload.get(key)
        if not delta_items:
            continue
        existing = merged.get(key)
        base = list(existing) if isinstance(existing, list) else []
        merged[key] = base + list(delta_items)
    return merged


async def run_cycle(
    *,
    llm: AgentLLM,
    tools: list[AgentTool],
    wm_snapshot: dict[str, Any],
    l5_summary: str,
    trigger_command: str,
    correlation_id: str | None,
    chat_history: str = "",
    max_tokens: int = 2048,
    wm_max_items: int = _DEFAULT_WM_MAX_ITEMS,
    proactive: bool = False,
) -> Recommendation | None:
    """Run ONE agent cycle: assemble -> Tool Runner loop (via AgentLLM) -> parse
    into a Recommendation, or None to stay silent. ``proactive=True`` swaps the
    reactive command framing for the watch-your-back scan (spec §6.A)."""
    raw = await llm.run(
        system=build_system_prompt(),
        user_message=build_push_context(
            wm_snapshot=wm_snapshot,
            l5_summary=l5_summary,
            trigger_command=trigger_command,
            chat_history=chat_history,
            wm_max_items=wm_max_items,
            proactive=proactive,
        ),
        tools=tools,
        max_tokens=max_tokens,
    )
    # Intentionally NO retry on unparseable output (unlike the enricher's parse-retry):
    # this is advisory, so silence is the safe default, and the Tool Runner already ran
    # a multi-turn loop — re-asking adds latency, not safety. parse_recommendation
    # returns None and the cycle stays silent. Documented decision (no retry machinery).
    return parse_recommendation(raw, correlation_id=correlation_id)


_ANSWER_SYSTEM_PROMPT = (
    "You are Red Robin, an operation-aware AI copilot behind a red-team operator's C2. "
    "The operator is TALKING TO YOU. Answer their question grounded in the engagement state "
    "and the retrieval tools (kb_retrieve, search_journal, and ALWAYS get_lessons — honour what "
    "this operator already taught you). Reason over what the tools and state return, never from "
    "memory, never invent a fact. You are ADVISORY ONLY.\n\n"
    "If the operator's message is a CORRECTION of your advice (e.g. 'that's too loud', "
    "'don't do X here'), you must ALSO capture a context-scoped lesson so you never repeat it: "
    "scope 'operator' if it is a general preference about how THIS operator works (persists across "
    "engagements), or 'engagement' if it is specific to THIS operation's situation (dies with the op). "
    "NEVER a blanket rule — always tie it to the context and the why.\n\n"
    "Return ONLY a JSON object: your reply MUST begin with { and end with } — no preamble, "
    "no trailing commentary, no code fence:\n"
    '{"answer": "your grounded answer (or an acknowledgement of the correction)",\n'
    ' "lesson": null OR {"scope": "operator|engagement", "context": "when it applies", '
    '"lesson": "what to do differently", "why": "the reason"}}'
)


def build_answer_context(
    *,
    wm_snapshot: dict[str, Any],
    l5_summary: str,
    question: str,
    chat_history: str = "",
    wm_max_items: int = _DEFAULT_WM_MAX_ITEMS,
) -> str:
    """Assemble the conversational PUSH context: L2 (current working memory) + L5
    (story so far) + the chat thread + the operator's question. The L2 snapshot is
    capped to the most-recent ``wm_max_items`` per category (context-rot guard).
    Non-mutating."""
    capped = cap_wm_snapshot(wm_snapshot, wm_max_items)
    return "\n".join(
        [
            "# Engagement working memory (L2)",
            json.dumps(capped, ensure_ascii=False, sort_keys=True),
            "",
            "# Story so far (L5)",
            l5_summary or "(no summary yet)",
            "",
            "# Conversation so far",
            chat_history or "(no conversation yet)",
            "",
            "# The operator says",
            question or "(empty)",
            "",
            "Answer them. Call get_lessons first. If this is a correction, capture the lesson.",
        ]
    )


async def run_answer_cycle(
    *,
    llm: AgentLLM,
    tools: list[AgentTool],
    wm_snapshot: dict[str, Any],
    l5_summary: str,
    question: str,
    chat_history: str = "",
    max_tokens: int = 2048,
    wm_max_items: int = _DEFAULT_WM_MAX_ITEMS,
) -> ParsedAnswer | None:
    """Run ONE conversational cycle: Q&A prompt -> Tool Runner loop -> ParsedAnswer
    (answer + citations + optional captured lesson), or None to stay silent."""
    raw = await llm.run(
        system=_ANSWER_SYSTEM_PROMPT,
        user_message=build_answer_context(
            wm_snapshot=wm_snapshot,
            l5_summary=l5_summary,
            question=question,
            chat_history=chat_history,
            wm_max_items=wm_max_items,
        ),
        tools=tools,
        max_tokens=max_tokens,
    )
    return parse_answer(raw)


_INTERPRET_SYSTEM_PROMPT = (
    "You are Red Robin, an operation-aware AI copilot behind a red-team operator's C2. "
    "Read the engagement state and decide whether the SHARED PICTURE should be updated: the "
    "current_phase, an objective's status/progress_pct, or a kill-chain milestone.\n\n"
    "current_phase should track the FURTHEST activity the findings evidence, along this ladder: "
    "not_started -> recon -> enumeration -> foothold -> privilege_escalation -> "
    "lateral_movement -> objective. Nudge it forward as a SOFT, REVERSIBLE read whenever the "
    "working memory plainly shows that activity (e.g. a host enumerated with findings + defenses "
    "gathered means recon/enumeration is underway) — leaving current_phase stale after that is a "
    "miss. This nudge is low-stakes and reversible, so lean toward reflecting reality. "
    "'Never invent progress' still binds the OBJECTIVE status/progress_pct — do NOT claim a "
    "percentage or a completion the findings do not evidence.\n\n"
    "Return ONLY a JSON object: your reply MUST begin with { and end with } — no preamble, "
    "no trailing commentary, no code fence. If nothing materially changed, return "
    '{"milestone_id": "", "phase": ""}. Otherwise:\n'
    '{"phase": "the current phase per the ladder above, or \\"\\" to leave it",\n'
    ' "milestone_id": "the objective/kill-chain id this is about, or \\"\\"",\n'
    ' "status": "the new status (e.g. in_progress, COMPLETE)",\n'
    ' "progress_pct": 0-100,\n'
    ' "needs_confirm": true ONLY for a change that CLOSES or COMMITS (an objective -> COMPLETE, a '
    "kill-chain goal reached); false for a soft, reversible read (a phase nudge, a progress bump),\n"
    ' "prompt": "if needs_confirm, a one-line yes/no question for the operator; else \\"\\""}'
)


def build_interpret_context(
    *, wm_snapshot: dict[str, Any], l5_summary: str, wm_max_items: int = _DEFAULT_WM_MAX_ITEMS
) -> str:
    capped = cap_wm_snapshot(wm_snapshot, wm_max_items)
    return "\n".join(
        [
            "# Engagement working memory (L2)",
            json.dumps(capped, ensure_ascii=False, sort_keys=True),
            "",
            "# Story so far (L5)",
            l5_summary or "(no summary yet)",
            "",
            "Given this, should the shared picture be updated? Return the interpretation JSON or null.",
        ]
    )


async def run_interpretation_cycle(
    *,
    llm: AgentLLM,
    tools: list[AgentTool],
    wm_snapshot: dict[str, Any],
    l5_summary: str,
    max_tokens: int = 1024,
    wm_max_items: int = _DEFAULT_WM_MAX_ITEMS,
) -> Interpretation | None:
    """Run ONE interpretation cycle → Interpretation or None (no material change)."""
    raw = await llm.run(
        system=_INTERPRET_SYSTEM_PROMPT,
        user_message=build_interpret_context(
            wm_snapshot=wm_snapshot,
            l5_summary=l5_summary,
            wm_max_items=wm_max_items,
        ),
        tools=tools,
        max_tokens=max_tokens,
    )
    return parse_interpretation(raw)


__all__ = [
    "apply_deltas_to_snapshot",
    "cap_wm_snapshot",
    "build_system_prompt",
    "build_push_context",
    "build_answer_context",
    "build_interpret_context",
    "state_signature",
    "run_cycle",
    "run_answer_cycle",
    "run_interpretation_cycle",
]
