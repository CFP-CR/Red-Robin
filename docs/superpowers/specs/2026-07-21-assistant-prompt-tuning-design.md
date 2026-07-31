# Assistant Prompt Tuning — Design Note (spec-lite)

**Date:** 2026-07-21 · **Branch:** `feat-ai-assistant` · **Scope:** two application-layer prompt-string edits · **Acceptance:** operator re-smoke (live behavior; CI only proves no-breakage)

## Problem (from the live smoke, operator feedback)
1. **Advisory too verbose** — the 5-part card renders as a wall of text; the model writes `rationale`/`confidence` as multi-sentence paragraphs. The STRUCTURE (5 parts) is fine; the LENGTH is not.
2. **`current_phase` never moves off `not_started`** — the interpretation-write cycle is wired + firing (verified), but the model leaves the phase empty: the prompt says "never invent progress" + "if nothing materially changed return empty" and gives NO phase vocabulary, so it plays safe even after recon is plainly done (host + 17 findings + defenses).

## Change (prompt-only; reversible; no parser/domain/renderer/schema change)

### 1. `agent.py:_SYSTEM_PROMPT` (advisory) — terseness
- Add a "BE TERSE AND SCANNABLE" directive (operator is mid-op on a live console; no preamble, no restating the state; few sharp words over a paragraph).
- Tighten field specs: `suggested_action` = one imperative line; `rationale` = 1-2 tight sentences (no restating the state); `confidence` = one line.
- KEEP: the 5 field names, `ADVISORY ONLY`, the `{"speak": false}` sentinel (so `test_agent.py::test_system_prompt_names_the_five_parts_and_the_thesis` stays green).

### 2. `agent.py:_INTERPRET_SYSTEM_PROMPT` (interpretation) — phase nudge
- Add a phase ladder + soft-nudge mandate: `current_phase` tracks the FURTHEST activity the findings evidence (`not_started → recon → enumeration → foothold → privilege_escalation → lateral_movement → objective`); nudge it forward as a SOFT, REVERSIBLE read when the WM plainly shows that activity — leaving it stale after recon is a miss.
- KEEP "never invent progress" but SCOPE it to the OBJECTIVE `status`/`progress_pct` (no fabricated %/completion) — distinct from the low-stakes phase reflection.
- KEEP the JSON output contract (parse_interpretation unchanged).

## Expected result
- Advisory: same 5 parts, ~6-8 lines instead of ~18-20 — reads like a tight briefing.
- Phase: with recon done, the model auto-writes `current_phase = recon/enumeration` (soft, reversible, labeled as its read) → the Working-Memory panel tracks reality; smoke **Checkpoint K** (soft auto-write) starts passing. Objective %/completion still never fabricated.

## Risk
Making the model more willing to write could OVER-nudge the phase — watched in the re-smoke. Both edits are single string constants; `git revert` fully undoes.

## Non-goals
- NOT the "form" redesign (5-part-terse vs lead-sentence+dimmed-footer vs single paragraph) — that touches parser/domain/renderer and grazes the "5 mandatory parts = grounded thesis" decision; a separate spec if the terser 5-part still doesn't feel natural.
- No change to the enricher, the gate, or any non-assistant prompt.

## Acceptance
CI: 5 gates green (the string edits break nothing; the one prompt-content test stays green by construction). LIVE: operator re-smoke — advisory reads tight; `current_phase` advances off `not_started` after recon (Checkpoint K). This is a live-surface change; the real acceptance is the smoke.
