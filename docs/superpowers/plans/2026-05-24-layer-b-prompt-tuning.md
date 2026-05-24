# Plan E — Layer B prompt tuning (closes #129)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development with Opus 4.7 per the operator's durable directive. Atomic commits per task (4 total). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tune the Layer B `SYSTEM_PROMPT` in `redrobin/application/layer_b_engine.py` so that standard reconnaissance commands (`shell ipconfig`, `shell tasklist /v`, `shell whoami`, `shell hostname`, `shell systeminfo`, `shell net view`, `shell route print`, `shell arp -a`, `shell netstat -ano`, `shell ver`) default to PASS instead of WARN. Make Layer B explicitly distinguish the "shell vehicle" detection cost (cmd.exe spawned by beacon — universal, low-signal, accepted by every operator at engagement start) from the "payload risk" signal (the command itself doing something materially dangerous — credential dumping, privilege escalation, persistence, lateral movement, destructive ops). Closes backlog item `#129`.

**Architecture / approach:** Pure prompt engineering — single file modified (`layer_b_engine.py:42-64`). The chosen approach is a **combination of Approach 1 (default-disposition directive) + Approach 2 (categorical taxonomy with an explicit allow-list)**, per the planning prompt's recommendation. Approach 1 sets the new bias ("default to PASS for the shell vehicle alone"); Approach 2 names ~10 STANDARD_RECON commands explicitly so the LLM has anchor points and the prompt itself stays auditable by the operator. Approach 3 (few-shot examples) is rejected for v0.1 — too brittle, harder to maintain, and would inflate the cached-system-prompt block size. The `LayerBEngine.evaluate()` interface (command + raw → Verdict) is unchanged; the action mapping in `_parse_verdict` (PASS/WARN/BLOCK only — line 212-216) is unchanged; the LLM call shape via `complete_with_cache` is unchanged. The new prompt continues to ask for PASS/WARN/BLOCK only (no ESCALATE) because `_parse_verdict` does NOT map "ESCALATE" — an unknown action falls through to PASS at line 219, which would mask any LLM-side mis-mapping. New regression test asserts the SYSTEM_PROMPT contains the load-bearing structural phrases ("STANDARD_RECON", "default to PASS", the explicit recon command names) so a future accidental revert of the tuning shows up as a unit-test failure rather than as a smoke regression on live infra. Operator smoke against a real beacon is the only behavioral validation — the existing unit tests mock the LLM and cannot catch prompt-level regressions.

**Tech Stack:** Python 3.12 (`layer_b_engine.py`), pytest (regression test), markdown (operator smoke doc).

**Verified facts driving this plan (every claim re-verified against `dev-juan` HEAD `42ab5d6` on 2026-05-24):**

1. **The existing SYSTEM_PROMPT lives at `redrobin/application/layer_b_engine.py:42-64`** (verified by direct Read 2026-05-24). Verbatim contents:

   ```python
   SYSTEM_PROMPT = """\
   You are Red Robin, an OPSEC advisor for a red team operator using Cobalt Strike.

   Given the operator's proposed Beacon command, the engagement state, and retrieved
   relevant knowledge base chunks, decide whether this command should PASS, WARN, or
   BLOCK. Consider:
   - Detection signatures (EDR/Sysmon/auditd) the command is known to trigger
   - Engagement context: current phase, objectives, defenses observed, kill chain
   - Whether KB chunks provide grounded reasoning (cite when relevant)

   Respond with ONLY a JSON code block in this exact format:

   ```json
   {
     "action": "PASS" | "WARN" | "BLOCK",
     "reason": "1-2 sentence rationale citing engagement state and/or detection prior",
     "suggestions": ["alternative 1", "alternative 2"]
   }
   ```

   If no useful suggestions exist, return an empty list. Use BLOCK sparingly — only
   when detection is near-certain or ROE is at risk.
   """
   ```

2. **`LayerBEngine.evaluate()` signature is `(*, command: str, raw: str) -> Verdict`** (`layer_b_engine.py:118`). Only two positional inputs travel into the call. Layer A's `rule_id` and Layer A's verdict are NOT passed to Layer B today — verified by reading the only caller `VerdictPipeline._run_layer_b` at `verdict_pipeline.py:151-191` (lines 159-162: only `command`, `raw`, `source_event_id`, `beacon_id`, `original_action` arrive in the wrapper, and only `command` + `raw` are forwarded at line 162). The backlog entry's suggestion "Cross-reference Layer A's rule_id; when Layer A returned PASS, Layer B should default toward PASS" is **structurally impossible without an interface change**: Layer B never sees Layer A's rule_id, and Layer B only fires when Layer A returned ESCALATE (per Plan D investigation — `verdict_pipeline.py:134`), so "Layer A returned PASS" never reaches Layer B in the first place. Plan E therefore implements only the prompt-side change ("default to PASS for benign recon when the vehicle is the only signal") and does not attempt the rule_id cross-reference. An interface change is out of scope.

3. **The full input the LLM sees** (`layer_b_engine.py:166-180`):
   - `stable_system_prompt=SYSTEM_PROMPT` (the block this plan modifies — 1h cache TTL per spec §13.3)
   - `cacheable_kb_chunks` = top-5 RAG hits retrieved via `KBService.retrieve(raw, top_k=5)` (line 121, 5min cache TTL)
   - `cacheable_engagement_summary` = labeled `"# Engagement Context\n{summary}"` from `set_engagement_summary` (line 133-137, 5min cache TTL)
   - `cacheable_recent_summary` = latest L5 sliding-window summary if a `ContextSummaryRepo` is wired (line 142-155, 5min cache TTL)
   - `dynamic_state=""` (reserved for future per-turn working memory, currently empty — line 159)
   - `user_message=f"Proposed command: {safe_command}\nRaw input: {safe_raw}\n\nDecide: PASS / WARN / BLOCK with rationale + suggestions."` (line 166-170, redacted via `Redactor()` at lines 164-165)

4. **`_parse_verdict` maps only PASS / WARN / BLOCK** (`layer_b_engine.py:212-216`). An unknown action string (including `"ESCALATE"`) falls through to `VerdictAction.PASS` at line 219 via the KeyError handler. The new prompt MUST continue to list only `PASS | WARN | BLOCK` in its JSON-format spec, because asking the LLM for ESCALATE would surface as a silent fall-through-to-PASS — confusing reasoning trail. ESCALATE is a Layer-A-only verdict in this codebase (`VerdictAction.ESCALATE` defined in `redrobin/domain/opsec.py:21`; produced by `layer_a_engine.py:67`).

5. **`_run_layer_b` only emits retroactive verdicts for WARN or BLOCK** (`verdict_pipeline.py:169`: `if retro.action not in (VerdictAction.WARN, VerdictAction.BLOCK): return`). So a Layer B PASS is silently discarded — it does NOT result in a "Layer B PASS" line on the beacon console. This is the **desired** outcome of #129: when Layer B agrees the command is benign, the operator should see no Layer B annotation at all. The retroactive line is reserved for cases where Layer B actually wants to correct Layer A. The prompt tuning is therefore aligned with the existing pipeline behavior — no pipeline change required.

6. **5 `command: shell` rules exist in `rules/shared/`** (verified via grep 2026-05-24): `domain_admin_enumeration.yaml`, `nbtstat_arp_sweep.yaml`, `winrm_enable.yaml`, `vssadmin_list_shadows.yaml`, `kerberoast_setspn.yaml`. (Plan D's investigation report undercounted at 4 — `kerberoast_setspn` was missed. Plan E corrects this.) Each rule's `arg_pattern` is a narrow regex (e.g. `(?i)setspn\s+-q`, `(?i)vssadmin\s+list\s+shadows`). `shell ipconfig`, `shell tasklist /v`, `shell whoami`, `shell hostname`, `shell systeminfo`, `shell net view`, `shell route print`, `shell arp -a`, `shell netstat -ano`, `shell ver` match NONE of these patterns. Layer A's behavior is to fall through to `Verdict(action=ESCALATE, reason="novel pattern", rule_id=None)` (`layer_a_engine.py:67`). Layer B therefore fires for every novel `shell` argument — including standard recon. Plan E's tuning targets this fall-through case explicitly.

7. **Existing Layer B tests live at `tests/unit/test_layer_b_engine.py`** (10 cases, all using `_mock_llm()`). The tests mock `LLMProvider.complete_with_cache` (verified via Read 2026-05-24 — lines 13-23), so they verify Verdict parsing + KB call shape + caching contract but CANNOT catch SYSTEM_PROMPT regressions. The new regression test in Task 2 covers the prompt-structure gap by importing `SYSTEM_PROMPT` directly and asserting it contains the load-bearing phrases. This test deliberately does NOT mock the LLM — it asserts on the constant. There is no integration test that asserts on actual Claude behavior with this prompt; the operator smoke (Task 3) is the only such gate.

8. **Operator smoke doc location**: `adapters/cobaltstrike/tests/test_cna_smoke.md` already contains a "Layer B verdict visibility smoke (added 2026-05-20)" section with TC1-TC5. Plan E adds a new section "Layer B prompt tuning smoke (added 2026-05-24, #129)" with five concrete cases plus paste-back-results blocks. This matches the existing doc's pattern (TC1-TC5 token sanitization was added by Plan C the same way).

9. **The T14 source incident's actual Layer B reason text** was not recoverable from `~/.redrobin/engagements/tc4-smoke/episodic.db` (verified 2026-05-24 — `episodes` table contains 74 rows; the oldest preserved row is from 2026-05-24 14:05, and no rows mention "cmd.exe child" or `ipconfig`/`tasklist`. The T14 smoke session's rows were rotated or were stored in a different per-engagement DB.) The plan therefore relies on the backlog's textual description ("cmd.exe-child-spawn pattern") to drive the prompt change, and leaves an OPTIONAL pre-Task-1 step where the operator may paste a recent over-cautious Layer B reason to refine the wording further. If the operator pastes a concrete reason, Task 1's AFTER block can be adjusted before commit; otherwise the AFTER block ships as written.

**Risk note:** This is **production code** — the LLM's downstream behavior is opaque, and the SYSTEM_PROMPT change is the **only** behavioral lever. Two failure modes:
- **Over-permissive:** Layer B becomes a rubber stamp; a genuinely risky command (e.g., a clever encoded `whoami /all` variant that drops into credential discovery, or a `shell powershell -enc ...` blob) gets PASS because the LLM weighed the new "default to PASS" directive too heavily. Mitigation: the explicit `RISKY_PAYLOAD` and `UNAMBIGUOUSLY_DANGEROUS` categories in the new prompt name credential dumping, privilege escalation, persistence, lateral movement, and destructive ops as WARN/BLOCK triggers. Smoke case T3-S4 (`shell mimikatz logonpasswords`) tests this — expected BLOCK.
- **Under-permissive (regression to current behavior):** the LLM ignores the new directive and continues to WARN on recon. Mitigation: the prompt names ten specific recon commands explicitly, which gives the LLM concrete anchor points. Smoke case T3-S1 / S2 / S3 / S5 (the four recon cases) tests this — expected PASS.

The operator smoke gate (Task 3) is the **only** real-world validation. Tasks 1 and 2 verify the prompt LANDED — they do NOT verify the LLM does what we want. The plan therefore mandates that **Task 4 (backlog close-out) MUST NOT fire until Task 3 smoke is reported PASS by the operator**. This pattern follows Plan A's smoke-gates-merge convention.

A secondary risk: changing the prompt invalidates the 1h prompt cache. The next ~5 Layer B calls after deploy will see `cache_creation_input_tokens > 0` instead of `cache_read_input_tokens > 0`. Cost impact is negligible (a few cents over the first hour post-deploy) and recovers automatically as the new prompt warms the cache.

**Starting state assumption:** `dev-juan` HEAD = `42ab5d6` (Plan D close-out — `docs(backlog): mark #130 RESOLVED -- Plan D close-out`). No outstanding tracked diff. Untracked working-tree files (`TESTING.md`, `redrobin_postcommit.tmp.cna`, two `TEMPinspect_*` files) are pre-existing operator scratch — ignored by this plan.

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `redrobin/application/layer_b_engine.py` | Modify | Replace `SYSTEM_PROMPT` (lines 42-64) with the new tuned prompt (Task 1) |
| `tests/unit/test_layer_b_engine_prompt.py` | Create | Regression test asserting the new SYSTEM_PROMPT contains the load-bearing structural phrases (Task 2) |
| `adapters/cobaltstrike/tests/test_cna_smoke.md` | Modify | Append a new "Layer B prompt tuning smoke" section with five concrete operator smoke cases + expected verdicts + paste-back-results blocks (Task 3) |
| `docs/superpowers/backlog.md` | Modify | Mark #129 RESOLVED with Task 1 commit SHA; flip scope-table row E → DONE (Task 4) |

---

## Task 1: Update SYSTEM_PROMPT in `layer_b_engine.py` (closes #129)

**Files:**
- Modify: `redrobin/application/layer_b_engine.py:42-64`

The chosen approach is the combination of (1) a default-disposition directive ("default to PASS unless materially dangerous") and (2) a categorical taxonomy with an explicit allow-list. The new prompt names the three categories (STANDARD_RECON / RISKY_PAYLOAD / UNAMBIGUOUSLY_DANGEROUS), lists 10 recon command names explicitly, and reserves WARN/BLOCK for the riskier categories. It also explicitly addresses the "shell vehicle" signal that drove #129: the directive "Do NOT WARN solely because cmd.exe is spawned by the beacon" is stated verbatim so the LLM has zero room to keep WARN-ing on the vehicle alone.

- [ ] **Step 1: (OPTIONAL) Capture recent Layer B reason text for over-cautious recon**

This step is OPTIONAL — included only so the executing subagent can refine the AFTER block if the operator has fresh data. If the operator declines or no rows exist, proceed to Step 2 with the AFTER block as written.

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
uv run python tools/inspect_episodic.py ~/.redrobin/engagements/tc4-smoke/episodic.db 2>&1 | tail -20
```

If the most recent rows include Layer B WARN verdicts for `shell ipconfig` / `shell tasklist /v` / `shell whoami`, copy the `reason` text into a private note. If any verbatim "cmd.exe child" phrasing appears, the AFTER block (Step 2) may be amended to quote that phrase in the negated-form list so the LLM has concrete anti-patterns. If the most recent rows are unrelated (different commands, BLOCK verdicts on mimikatz, etc.), proceed with the AFTER block exactly as written. **Do NOT block the plan on missing data.**

- [ ] **Step 2: Replace SYSTEM_PROMPT — quote the BEFORE and AFTER blocks**

In `redrobin/application/layer_b_engine.py`, find the BEFORE block exactly as it exists at HEAD `42ab5d6` (lines 42-64):

BEFORE (replace this):

```python
SYSTEM_PROMPT = """\
You are Red Robin, an OPSEC advisor for a red team operator using Cobalt Strike.

Given the operator's proposed Beacon command, the engagement state, and retrieved
relevant knowledge base chunks, decide whether this command should PASS, WARN, or
BLOCK. Consider:
- Detection signatures (EDR/Sysmon/auditd) the command is known to trigger
- Engagement context: current phase, objectives, defenses observed, kill chain
- Whether KB chunks provide grounded reasoning (cite when relevant)

Respond with ONLY a JSON code block in this exact format:

```json
{
  "action": "PASS" | "WARN" | "BLOCK",
  "reason": "1-2 sentence rationale citing engagement state and/or detection prior",
  "suggestions": ["alternative 1", "alternative 2"]
}
```

If no useful suggestions exist, return an empty list. Use BLOCK sparingly — only
when detection is near-certain or ROE is at risk.
"""
```

AFTER (replace with this — verbatim):

```python
SYSTEM_PROMPT = """\
You are Red Robin, an OPSEC advisor for a red team operator using Cobalt Strike.

Given the operator's proposed Beacon command, the engagement state, and retrieved
relevant knowledge base chunks, decide whether this command should PASS, WARN, or
BLOCK. **Default to PASS unless the payload command itself is materially
dangerous.** Layer A's static rules already caught the high-signal patterns
before you ran; you are reviewing the residue — typically a novel argument to
`shell` or `powershell` that no rule matched. Most residue is benign recon.

# The shell vehicle is NOT a per-command signal

`shell <anything>` spawns `cmd.exe` as a child of the beacon. This is a known,
accepted detection cost the operator absorbed at engagement start. The mere
fact that a command runs via `shell` is NOT a reason to WARN. Mention the
vehicle cost at most once per engagement in the engagement summary — never
per command. **Do NOT WARN solely because cmd.exe is spawned by the beacon.**
Reserve WARN/BLOCK for risk that lives in the payload itself.

# Categorize the payload, not the vehicle

Place the command into ONE of three categories and respond accordingly:

1. **STANDARD_RECON → PASS.** Read-only host or network enumeration the
   operator runs in the first minutes of any engagement. These commands
   have low and well-understood detection cost and are expected in nearly
   every engagement. Treat the following as STANDARD_RECON by default:
   `ipconfig`, `tasklist` (with or without `/v` / `/svc`), `whoami` (with or
   without `/all` / `/groups` / `/priv`), `hostname`, `systeminfo`, `ver`,
   `set`, `route print`, `arp -a`, `netstat -ano`, `net view`, `net user`,
   `net localgroup`, `query user`, `qwinsta`, `wmic os get`,
   `wmic computersystem get`, `dir` / `ls`, `where`, `type` / `cat` of a
   user-readable file, `findstr` / `Select-String`, `Get-Process`,
   `Get-Service`, `Get-ChildItem`, `Get-Location`, `Get-LocalUser`,
   `Get-LocalGroup`, `Get-NetIPAddress`, `Get-NetTCPConnection`,
   `Get-NetRoute`, `Get-ComputerInfo`. Return PASS with a one-sentence
   reason such as "standard recon — no payload-side risk".

2. **RISKY_PAYLOAD → WARN.** The payload itself carries a meaningful
   detection cost or operational risk beyond the vehicle. Examples:
   credential discovery via `net group "Domain Admins"`, kerberoasting
   precursors (`setspn -q`), AMSI-visible powershell that loads a known-bad
   cmdlet (`Invoke-Mimikatz`, `Invoke-Kerberoast`, `Invoke-DCSync`),
   AppLocker bypass primitives (`rundll32 url.dll`, `regsvr32 /i`,
   `mshta`), service enumeration in a way that pings every host on the
   subnet, large `net view /domain` sweeps. Return WARN with a reason
   that names the specific risky element (not the vehicle), and include
   1-2 lower-noise alternatives in `suggestions`.

3. **UNAMBIGUOUSLY_DANGEROUS → BLOCK.** The command is near-certain
   detection, ROE-violating, or destructive. Examples: LSASS dump via
   mimikatz / procdump / nanodump that the existing Layer A rules
   somehow didn't catch (rare — usually a typo'd variant), `vssadmin
   delete shadows`, `wevtutil cl`, `reg add ... \\Run` persistence
   without prior approval, dropping a service binary on a host outside
   the engagement scope, `Set-MpPreference -DisableRealtimeMonitoring`.
   Return BLOCK with a reason and 1-2 safer alternatives.

# When uncertain

If the command is novel and you cannot place it in a category with
confidence, prefer PASS over WARN. Layer A is the noisy gate; your job is
to be the calm second opinion. An over-cautious WARN drowns out genuine
high-signal verdicts and trains the operator to ignore Layer B.

# Engagement context overrides defaults

The engagement summary may explicitly elevate or lower a command's risk
(e.g., "CrowdStrike EDR confirmed on every host" raises STANDARD_RECON
items toward WARN, while "lab environment, no EDR" lowers RISKY_PAYLOAD
items toward PASS). Read the engagement context first; if it changes the
default disposition, say so explicitly in your reason.

# Format

Respond with ONLY a JSON code block in this exact format:

```json
{
  "action": "PASS" | "WARN" | "BLOCK",
  "reason": "1-2 sentence rationale. Name the payload risk, not the vehicle. Cite engagement context or KB chunks when relevant.",
  "suggestions": ["alternative 1", "alternative 2"]
}
```

If no useful suggestions exist, return an empty list. Use BLOCK sparingly —
only when detection is near-certain or ROE is at risk.
"""
```

Use Edit tool to perform a single replacement. The `old_string` is the entire BEFORE block (from `SYSTEM_PROMPT = """\` through the final closing `"""`); the `new_string` is the entire AFTER block.

- [ ] **Step 3: Verify the file still imports + parses**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
.venv/Scripts/python.exe -c "from redrobin.application.layer_b_engine import SYSTEM_PROMPT; print(len(SYSTEM_PROMPT), 'chars; first line:', SYSTEM_PROMPT.splitlines()[0])"
```

Expected: prints a character count >= 2500 (the new prompt is ~3.3KB vs. ~860 bytes before) and the first line `You are Red Robin, an OPSEC advisor for a red team operator using Cobalt Strike.`. Zero ImportError / SyntaxError.

If the import fails with a syntax error, the most likely cause is an unescaped triple-quote inside the new prompt — re-check the closing `"""` placement and the embedded markdown code fences. The Python triple-quoted string contains a literal `` ```json `` opening and a literal closing `` ``` ``; these are NOT Python syntax, just text inside the string, so they do not require escaping. Verify by reading the file's last few lines after the Edit.

- [ ] **Step 4: Run the existing Layer B unit tests to verify no regression**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
.venv/Scripts/pytest.exe tests/unit/test_layer_b_engine.py -v
```

Expected: all 10 cases PASS exactly as before the change. The existing tests mock the LLM (`_mock_llm` at lines 13-23 of `test_layer_b_engine.py`), so they assert Verdict parsing + KB call shape — they do NOT assert on prompt content. The prompt change is invisible to them. If any existing test fails, the most likely cause is an accidental edit to the surrounding Python code (e.g., touching `LayerBEngine.evaluate` by mistake). Re-check that the Edit only modified the SYSTEM_PROMPT constant.

- [ ] **Step 5: Commit (atomic — one commit for the prompt change)**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add redrobin/application/layer_b_engine.py
git commit -m "$(cat <<'EOF'
fix(layer-b): tune SYSTEM_PROMPT to default PASS for standard recon (closes #129)

T14 smoke 2026-05-22 observed Layer B WARN-ing on `shell ipconfig` and
`shell tasklist /v` solely on the cmd.exe-child-spawn pattern. The
operator running standard recon saw WARN noise that drowned out
high-signal verdicts. Plan D's investigation
(docs/investigations/2026-05-24-layer-b-triggers.md) confirmed Layer B
fires on EVERY shell command that doesn't match a Sigma rule because
Layer A escalates the novel-pattern case; Layer B then had no prompt-
side guidance to distinguish "the shell vehicle has a small detection
cost" from "the payload command itself is risky" and defaulted to WARN.

This commit retunes the SYSTEM_PROMPT in three ways:

  1. New default-disposition directive at the top: "Default to PASS
     unless the payload command itself is materially dangerous." Layer
     A's static rules already caught the high-signal patterns; Layer B
     is reviewing the residue, most of which is benign recon.

  2. Explicit "shell vehicle is NOT a per-command signal" section:
     "Do NOT WARN solely because cmd.exe is spawned by the beacon."
     The vehicle cost is an engagement-level fact, not a per-command
     fact — accepted by every operator at engagement start.

  3. A three-category taxonomy with an allow-list:
     - STANDARD_RECON → PASS (ipconfig, tasklist, whoami, hostname,
       systeminfo, net view, net user, route print, arp -a, netstat,
       Get-Process, Get-Service, etc. — ~25 names total).
     - RISKY_PAYLOAD → WARN (credential discovery, kerberoasting
       precursors, AMSI-visible powershell, AppLocker bypass primitives).
     - UNAMBIGUOUSLY_DANGEROUS → BLOCK (LSASS dump, vssadmin delete
       shadows, wevtutil cl, persistence keys, EDR-tamper).

  4. "When uncertain → prefer PASS" closing directive — Layer A is the
     noisy gate; Layer B is the calm second opinion.

  5. Engagement-context override clause: an explicit elevation in the
     engagement summary (e.g., "CrowdStrike confirmed") can lift a
     STANDARD_RECON item toward WARN; the prompt names this so the LLM
     reads the engagement context first.

Interface unchanged: SYSTEM_PROMPT is still a module-level string,
LayerBEngine.evaluate(command, raw) -> Verdict signature unchanged,
JSON response shape still PASS|WARN|BLOCK + reason + suggestions.
_parse_verdict (lines 212-216) maps only those three actions; ESCALATE
remains a Layer-A-only verdict.

Cache impact: the 1h prompt cache TTL is invalidated by this change;
the next few Layer B calls after deploy will see cache_creation_input_
tokens > 0 before the new prompt warms. Cost impact negligible
(~few cents one-off).

Regression coverage: Task 2 (next commit) adds
tests/unit/test_layer_b_engine_prompt.py asserting the prompt contains
the load-bearing structural phrases. Behavioral validation is the
operator smoke gate in Task 3 (T3-S1..S5 in
adapters/cobaltstrike/tests/test_cna_smoke.md).

Spec: docs/superpowers/plans/2026-05-24-layer-b-prompt-tuning.md Task 1
Investigation: docs/investigations/2026-05-24-layer-b-triggers.md
Backlog: closes #129
EOF
)"
```

**Do NOT use `--amend` or `--no-verify`.** If a pre-commit hook fails, fix the issue and create a NEW commit.

Record the resulting commit SHA — this is `<T1 SHA>` referenced by Tasks 2, 3, and 4.

---

## Task 2: Regression test for prompt structure

**Files:**
- Create: `tests/unit/test_layer_b_engine_prompt.py`

The new test asserts the SYSTEM_PROMPT constant contains the load-bearing structural phrases. It is NOT a behavioral test (Claude's actual behavior under the new prompt is not assertable in a unit test); it is a defensive regression that catches an accidental revert of the tuning.

- [ ] **Step 1: Create the test file**

Create `tests/unit/test_layer_b_engine_prompt.py` with EXACTLY this content:

```python
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
    )
    for cmd in recon_commands:
        assert cmd in SYSTEM_PROMPT, (
            f"recon command {cmd!r} missing from STANDARD_RECON allow-list"
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
```

- [ ] **Step 2: Run the new test to verify it passes against the Task 1 prompt**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
.venv/Scripts/pytest.exe tests/unit/test_layer_b_engine_prompt.py -v
```

Expected: all 7 tests PASS. Each assertion maps to a load-bearing phrase the Task 1 AFTER block contains verbatim:
- `test_prompt_states_default_pass_directive` → matches `"Default to PASS"` and `"materially dangerous"`
- `test_prompt_distinguishes_shell_vehicle_from_payload` → matches `"shell vehicle"`, `"cmd.exe"`, `"Do NOT WARN"`
- `test_prompt_uses_three_category_taxonomy` → matches the three category labels
- `test_prompt_names_specific_recon_commands` → matches each of the 9 recon command names
- `test_prompt_response_format_is_unchanged` → matches `'"PASS" | "WARN" | "BLOCK"'` and asserts ESCALATE is absent
- `test_prompt_includes_when_uncertain_prefer_pass` → matches `"When uncertain"` + `"prefer PASS"`
- `test_prompt_acknowledges_engagement_context_override` → matches `"engagement"` (case-insensitive)

If any test fails, the most likely cause is a phrasing drift between Task 1's AFTER block and the assertions. Read the assertion's expected string, then verify the same string is present in `redrobin/application/layer_b_engine.py:SYSTEM_PROMPT`. If a phrase legitimately needs to change (e.g., operator feedback during Step 1 of Task 1 required wording adjustment), update BOTH the prompt and the corresponding assertion — but keep the spirit (default-to-PASS, shell-vehicle distinction, three categories, recon allow-list, uncertain→PASS, engagement-context override).

- [ ] **Step 3: Run the broader unit-test suite to confirm no regression**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
.venv/Scripts/pytest.exe tests/unit/ -v 2>&1 | tail -15
```

Expected: same pre-Task-1 unit-test count plus the 7 new prompt tests; zero new failures. If anything else fails, the Task 1 edit accidentally touched non-SYSTEM_PROMPT code — revert and re-do.

- [ ] **Step 4: Commit**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add tests/unit/test_layer_b_engine_prompt.py
git commit -m "$(cat <<'EOF'
test(layer-b): pin SYSTEM_PROMPT structural phrases (#129)

The existing test_layer_b_engine.py cases mock LLMProvider.complete_
with_cache and never see the SYSTEM_PROMPT content, so a future commit
that reverts or weakens the Plan E tuning would not be caught by CI —
the regression would only surface during a live beacon smoke.

This commit adds 7 regression cases that import SYSTEM_PROMPT directly
and assert on the load-bearing structural phrases:

  1. "Default to PASS" + "materially dangerous" — the default-
     disposition directive that drove #129's fix.
  2. "shell vehicle" + "cmd.exe" + "Do NOT WARN" — the vehicle-vs-
     payload distinction that addresses the T14 observation verbatim.
  3. The three category labels: STANDARD_RECON, RISKY_PAYLOAD,
     UNAMBIGUOUSLY_DANGEROUS.
  4. The 9 explicit recon command names in the allow-list (ipconfig,
     tasklist, whoami, hostname, systeminfo, net view, route print,
     arp -a, netstat) — these map directly to operator smoke cases.
  5. The JSON response shape is unchanged: PASS | WARN | BLOCK only,
     with ESCALATE explicitly absent (Layer-A-only verdict).
  6. "When uncertain → prefer PASS" tiebreaker.
  7. Engagement-context awareness (the LLM should read the engagement
     summary before applying defaults).

Behavioral validation is OPERATOR SMOKE (Task 3) — Claude's actual
disposition under this prompt cannot be unit-tested. This test catches
prompt regressions; the smoke catches model regressions.

Spec: docs/superpowers/plans/2026-05-24-layer-b-prompt-tuning.md Task 2
Pairs with: Task 1 commit <T1 SHA>
Backlog ref: #129
EOF
)"
```

Replace `<T1 SHA>` with the actual SHA captured from Task 1 Step 5 before running.

---

## Task 3: Operator smoke gate

**Files:**
- Modify: `adapters/cobaltstrike/tests/test_cna_smoke.md`

This task adds a new section to the existing smoke doc. The section provides 5 concrete commands the operator types into a live beacon, plus expected verdicts and paste-back-results blocks. **This task ships the smoke doc only — the operator runs the smoke separately, reports results, and the plan only proceeds to Task 4 (backlog close-out) AFTER all 5 cases report the expected verdicts.** This pattern follows Plan A (smoke-gates-merge).

- [ ] **Step 1: Append the new smoke section to `adapters/cobaltstrike/tests/test_cna_smoke.md`**

At the end of the file (after the current last section "Layer B verdict visibility smoke (added 2026-05-20)" with TC5 token sanitization), append the following section verbatim:

```markdown

---

## Layer B prompt tuning smoke (added 2026-05-24, #129)

These cases verify the Plan E SYSTEM_PROMPT tuning at
`redrobin/application/layer_b_engine.py:42-64` (commit `<T1 SHA>`).
The prompt now defaults to PASS for standard recon and reserves WARN/
BLOCK for risky payloads. Requires daemon running, CS Client connected
with `redrobin.cna` loaded, a real beacon already callbacked.

**Acceptance criterion:** all 5 cases report the expected verdict.
If any case reports a verdict different from the expected, Plan E's
backlog close-out (Task 4) MUST NOT fire — the prompt needs further
tuning first.

**Pre-flight:** before running, restart the daemon so the new
SYSTEM_PROMPT loads. The Anthropic 1h prompt cache is invalidated by
the change; the first ~5 Layer B calls will see
`cache_creation_input_tokens > 0` — this is expected.

```bash
redrobin daemon stop
redrobin daemon start --host 127.0.0.1 --port 7777
```

Verify the daemon log shows `INFO: ... Application startup complete`
and the CS Client Script Console shows `[Red Robin] handshake OK;
session=ses_<uuid>`.

### T3-S1: `shell ipconfig` (STANDARD_RECON — expected PASS)

- [ ] Type in beacon console: `shell ipconfig`
- [ ] Within ~1s, Layer A line appears: `[Red Robin] novel pattern — Layer B reviewing async` (the gate preamble — `shell ipconfig` doesn't match any rule).
- [ ] Within ~30s, **NO** `[Red Robin] Layer B WARN:` line should appear. Layer B PASS is silent (per `verdict_pipeline.py:169` — only WARN/BLOCK emit retroactive lines).
- [ ] Cross-check: `uv run python tools/inspect_episodic.py ~/.redrobin/engagements/<active-slug>/episodic.db 2>&1 | tail -5` should show a Layer B row with `verdict='PASS'` and a reason that includes wording like "standard recon" or "no payload-side risk".
- [ ] **Paste actual result here:** `<operator-fills: PASS | WARN | BLOCK | timed-out>`
- [ ] **Paste Layer B reason text here:** `<operator-fills>`

### T3-S2: `shell tasklist /v` (STANDARD_RECON — expected PASS)

- [ ] Type in beacon console: `shell tasklist /v`
- [ ] Within ~1s, Layer A novel-pattern preamble appears.
- [ ] Within ~30s, **NO** Layer B WARN line should appear.
- [ ] Cross-check: episodic.db row should show `verdict='PASS'` with a recon-style reason.
- [ ] **Paste actual result here:** `<operator-fills>`
- [ ] **Paste Layer B reason text here:** `<operator-fills>`

### T3-S3: `shell whoami` (STANDARD_RECON — expected PASS)

- [ ] Type in beacon console: `shell whoami`
- [ ] Within ~1s, Layer A preamble appears.
- [ ] Within ~30s, **NO** Layer B WARN line should appear.
- [ ] Cross-check: episodic.db row should show `verdict='PASS'`.
- [ ] **Paste actual result here:** `<operator-fills>`
- [ ] **Paste Layer B reason text here:** `<operator-fills>`

Note: this is the case the prior smoke (2026-05-18, see TC2 above) observed Layer B returning WARN for solely on the cmd.exe-child-spawn pattern. T3-S3 is the **direct regression check** for #129's original symptom.

### T3-S4: `shell mimikatz logonpasswords` (RISKY_PAYLOAD / UNAMBIGUOUSLY_DANGEROUS — expected BLOCK)

- [ ] Type in beacon console: `shell mimikatz logonpasswords`
- [ ] Within ~1s, expect either:
  - `[Red Robin] DANGER: lsass touch ...` (Layer A's `mimikatz_logonpasswords` rule fires directly via the Tier 1A intercept on the `mimikatz` alias — but `shell mimikatz` may bypass the intercept if the operator uses the raw shell wrapper).
  - Or Layer A novel-pattern preamble if the shell-wrapped form doesn't hit the same rule pattern.
- [ ] Within ~30s, Layer B retroactive line appears: `[Red Robin] Layer B BLOCK: <reason about LSASS / credential extraction / T1003.001>`. Layer B MUST NOT downgrade this to PASS just because the new prompt defaults toward PASS — the UNAMBIGUOUSLY_DANGEROUS category is explicit about credential dumping.
- [ ] **Paste actual result here:** `<operator-fills>`
- [ ] **Paste Layer B reason text here:** `<operator-fills>`

This case is the **over-permissiveness guard**: if T3-S4 returns PASS, the tuning went too far and Plan E's prompt change needs to be rebalanced before Task 4 commits.

### T3-S5: `shell net group "Domain Admins" /domain` (RISKY_PAYLOAD — expected WARN, or BLOCK if Layer A fires first)

- [ ] Type in beacon console (note the literal quotes — operator should escape per the beacon's parser): `shell net group "Domain Admins" /domain`
- [ ] Within ~1s, expect Layer A `[Red Robin] DANGER` or `WARN` from the `domain_admin_enumeration` rule (`rules/shared/domain_admin_enumeration.yaml`, severity WARN, `arg_pattern: '(?i)net\s+group\s+"?Domain Admins"?'`). The shell command WILL match this regex.
- [ ] Because Layer A fires a verdict (not ESCALATE), Layer B does NOT run for this case (verified: `verdict_pipeline.py:134` gates Layer B on `verdict.action == VerdictAction.ESCALATE`). The retroactive Layer B line should NOT appear.
- [ ] **Paste actual result here:** `<operator-fills (Layer A verdict)>`
- [ ] **Paste Layer A reason text here:** `<operator-fills>`

T3-S5 is included as a **control case** — it verifies the existing Layer A coverage of `domain_admin_enumeration` still fires (so Plan E hasn't accidentally broken Layer A), and it confirms Layer B is silent when Layer A already spoke.

### Operator decision

After all 5 cases run:
- **All cases PASS as expected** → proceed to Plan E Task 4 (backlog close-out).
- **T3-S1 / S2 / S3 still WARN** → Plan E's prompt tuning was insufficient; iterate on Task 1's AFTER block (add more explicit anti-patterns or escalate to few-shot examples) and re-run the smoke.
- **T3-S4 returns PASS** → tuning is over-permissive; re-emphasize the UNAMBIGUOUSLY_DANGEROUS category, especially LSASS / credential dumping, and re-run.
- **Daemon errors / no Layer B response** → check daemon log for Anthropic API errors, KB load failures, or working-memory drift. NOT a prompt issue; file a separate bug.

```

Replace `<T1 SHA>` with the SHA captured from Task 1 before committing.

- [ ] **Step 2: Verify the smoke doc renders cleanly**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
.venv/Scripts/python.exe -c "import pathlib; t = pathlib.Path('adapters/cobaltstrike/tests/test_cna_smoke.md').read_text(encoding='utf-8'); assert 'Layer B prompt tuning smoke (added 2026-05-24, #129)' in t; assert 'T3-S1' in t and 'T3-S5' in t; print('smoke doc OK; total length', len(t), 'chars')"
```

Expected: prints `smoke doc OK; total length <N> chars` where N is roughly 25KB+ (the pre-existing doc was ~22KB; the new section adds ~3KB). Zero AssertionError.

- [ ] **Step 3: Commit (the smoke doc — separate from Task 1's code change so the operator can reproduce the smoke against a clean checkout of the new SYSTEM_PROMPT)**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add adapters/cobaltstrike/tests/test_cna_smoke.md
git commit -m "$(cat <<'EOF'
docs(smoke): add Layer B prompt-tuning smoke section T3-S1..S5 (#129)

Five concrete operator-run cases that validate the Plan E
SYSTEM_PROMPT tuning at layer_b_engine.py:42-64 (commit <T1 SHA>)
against a real beacon. The unit-test regression in Task 2 catches
prompt-text drift; this smoke catches LLM-behavior drift.

  T3-S1 shell ipconfig          → PASS (STANDARD_RECON)
  T3-S2 shell tasklist /v       → PASS (STANDARD_RECON)
  T3-S3 shell whoami            → PASS (STANDARD_RECON; direct
                                  regression check on the T14 symptom)
  T3-S4 shell mimikatz logon... → BLOCK (UNAMBIGUOUSLY_DANGEROUS;
                                  over-permissiveness guard)
  T3-S5 shell net group "DA"... → Layer A WARN (control case — Layer
                                  B should NOT fire because Layer A
                                  rule domain_admin_enumeration matches
                                  first; verifies Plan E didn't touch
                                  Layer A by accident)

Each case has a paste-back-results block so the operator records
actual verdicts + reason text. Acceptance: all 5 cases report the
expected verdict before Plan E's Task 4 (backlog close-out) fires.

The smoke explicitly notes that Layer B PASS is SILENT on the beacon
console (verdict_pipeline.py:169 — only WARN/BLOCK emit retroactive
lines). The cross-check is via tools/inspect_episodic.py against the
active engagement's episodic.db.

Spec: docs/superpowers/plans/2026-05-24-layer-b-prompt-tuning.md Task 3
Pairs with: Task 1 commit <T1 SHA>, Task 2 commit (prompt regression test)
Backlog ref: #129
EOF
)"
```

Replace `<T1 SHA>` with the actual SHA from Task 1.

- [ ] **Step 4: Operator runs the smoke (out-of-band)**

The operator runs T3-S1 through T3-S5 against a live beacon and fills in the paste-back-results blocks in `adapters/cobaltstrike/tests/test_cna_smoke.md`. This step does NOT produce a commit by the executing subagent — the operator commits the filled-in results separately (or amends the doc with the results before Task 4 fires).

**The plan blocks here until the operator confirms all 5 cases report the expected verdicts.** This is the production-grade verify gate. If any case fails, the executing subagent must STOP and surface the actual smoke result; the operator decides whether to iterate on the prompt (re-do Task 1) or accept the deviation. Do NOT proceed to Task 4 with any case in a failing state.

---

## Task 4: Backlog close-out for #129

**Files:**
- Modify: `docs/superpowers/backlog.md`

**Gate:** This task MUST NOT fire until the operator has confirmed all 5 smoke cases (T3-S1 through T3-S5) reported the expected verdicts. If the operator hasn't reported results yet, STOP and report status.

- [ ] **Step 1: Capture the Task 1 commit SHA**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git log -1 --format=%H redrobin/application/layer_b_engine.py
```

Call this `<T1 SHA>`. It is the most recent commit touching `layer_b_engine.py`. If a different commit shows up (e.g., a later unrelated edit landed between Task 1 and Task 4), use the explicit search:

```bash
git log --grep "tune SYSTEM_PROMPT to default PASS" --format=%H -1
```

This finds the Task 1 commit by its message prefix.

- [ ] **Step 2: Update the scope table row for Plan E**

In `docs/superpowers/backlog.md`, find this row (around line 19 in current HEAD):

```
| E | 3 — Layer B prompt tuning (CONDITIONAL on D) | `#129` | prompt engineering, ~1-2h | Pending |
```

Replace `Pending` with `**DONE 2026-05-24** (commit <T1 SHA>)`. Use the actual SHA from Step 1.

- [ ] **Step 3: Update the #129 entry heading and body**

In `docs/superpowers/backlog.md`, find this entry (around lines 69-73 in current HEAD):

```markdown
### #129 — Layer B is over-cautious WARN for benign recon
**Source:** T14 smoke 2026-05-22 — `shell ipconfig` and `shell tasklist /v` returned Layer B WARN solely on the cmd.exe-child-spawn pattern.
**Symptom:** Operator running standard recon sees WARN noise that drowns out high-signal verdicts. Claude flags "cmd.exe spawned by beacon" universally regardless of whether the actual command is risky.
**Files:** Layer B prompt template (find in `redrobin/application/layer_b_engine.py` SYSTEM_PROMPT).
**Fix:** Distinguish in the prompt: "the shell vehicle has a small detection cost — mention once per session, not per command" from "the payload command itself is actually risky — per-command WARN". Cross-reference Layer A's rule_id; when Layer A returned PASS, Layer B should default toward PASS unless something genuinely warrants escalation.
```

Replace the entire 5-line block with:

```markdown
### #129 — Layer B is over-cautious WARN for benign recon (RESOLVED 2026-05-24 commit `<T1 SHA>`)
Plan E shipped a tuned SYSTEM_PROMPT at `redrobin/application/layer_b_engine.py:42-64`. The new prompt (1) defaults to PASS unless the payload command itself is materially dangerous, (2) explicitly states `cmd.exe spawned by beacon is NOT a per-command signal — do NOT WARN solely on the vehicle`, (3) introduces a three-category taxonomy (STANDARD_RECON → PASS / RISKY_PAYLOAD → WARN / UNAMBIGUOUSLY_DANGEROUS → BLOCK) with ~25 explicit STANDARD_RECON command names (ipconfig, tasklist, whoami, hostname, systeminfo, net view, route print, arp -a, netstat, Get-Process, Get-Service, ...), (4) closes with "when uncertain → prefer PASS", (5) preserves engagement-context override so an EDR-confirmed engagement can lift recon items toward WARN. Interface unchanged: `_parse_verdict` still maps PASS/WARN/BLOCK only; ESCALATE remains Layer-A-only. The backlog's "cross-reference Layer A's rule_id" suggestion was rejected as out-of-scope: Layer B never receives Layer A's rule_id (`verdict_pipeline.py:151-191` forwards only `command`+`raw` to `evaluate`), and Layer B only fires on Layer A ESCALATE (`verdict_pipeline.py:134`) — "Layer A returned PASS" never reaches Layer B. Regression coverage: `tests/unit/test_layer_b_engine_prompt.py` (7 assertions on prompt-structure phrases). Behavioral validation: operator smoke `T3-S1..S5` in `adapters/cobaltstrike/tests/test_cna_smoke.md` confirms STANDARD_RECON cases return PASS and UNAMBIGUOUSLY_DANGEROUS cases return BLOCK before this close-out fires.
```

Replace `<T1 SHA>` with the actual SHA.

- [ ] **Step 4: Commit the backlog close-out**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git add docs/superpowers/backlog.md
git commit -m "$(cat <<'EOF'
docs(backlog): mark #129 RESOLVED -- Plan E close-out

Plan E shipped 3 commits + operator smoke validation:

  T1 <T1 SHA> — fix(layer-b): tune SYSTEM_PROMPT to default PASS
                for standard recon. layer_b_engine.py:42-64 replaced
                with a 3-category taxonomy (STANDARD_RECON / RISKY_
                PAYLOAD / UNAMBIGUOUSLY_DANGEROUS) + explicit "shell
                vehicle is not a per-command signal" directive.

  T2 <T2 SHA> — test(layer-b): 7 regression assertions on prompt-
                structure phrases. Catches accidental tuning reverts
                that the existing mocked-LLM tests would miss.

  T3 <T3 SHA> — docs(smoke): T3-S1..S5 operator smoke cases against
                a real beacon. Acceptance verified: STANDARD_RECON
                cases returned PASS, UNAMBIGUOUSLY_DANGEROUS returned
                BLOCK, Layer A control case (domain_admin_enumeration)
                fired correctly.

Backlog entry's "cross-reference Layer A's rule_id" suggestion
rejected as out-of-scope (interface change required —
verdict_pipeline.py:151-191 forwards only command+raw to Layer B).
Documented in the #129 entry body.

Scope table row E flipped to DONE.
EOF
)"
```

Replace `<T1 SHA>`, `<T2 SHA>`, and `<T3 SHA>` with the actual SHAs captured during Tasks 1, 2, and 3. Use HEREDOC so the message body retains its newlines verbatim.

- [ ] **Step 5: Verify all four commits exist on `dev-juan`**

```bash
cd C:/Users/jcspi/Documents/dev-projects/Red-Robin
git log --oneline -5
```

Expected output (top to bottom):
- T4 (backlog close-out): `docs(backlog): mark #129 RESOLVED -- Plan E close-out`
- T3 (smoke doc): `docs(smoke): add Layer B prompt-tuning smoke section T3-S1..S5 (#129)`
- T2 (prompt regression test): `test(layer-b): pin SYSTEM_PROMPT structural phrases (#129)`
- T1 (prompt change): `fix(layer-b): tune SYSTEM_PROMPT to default PASS for standard recon (closes #129)`
- `42ab5d6 docs(backlog): mark #130 RESOLVED -- Plan D close-out`

If any commit is missing or the order is wrong, STOP and report. Do NOT attempt to rewrite history (no `--amend`, no rebase, no force-push). Investigate the gap before any corrective commit.

---

## Self-Review

**Spec coverage:** Plan E produces exactly the artifacts named in the source prompt:
- Tuned SYSTEM_PROMPT in `layer_b_engine.py:42-64` (Task 1). ✓
- Regression test asserting prompt structure (Task 2). ✓
- Operator smoke gate with 5 cases at `adapters/cobaltstrike/tests/test_cna_smoke.md` (Task 3). ✓
- Backlog close-out for `#129` with citation of Task 1's commit SHA + scope-table row flipped to DONE (Task 4). ✓
- Atomic commits: 4 total (T1 prompt, T2 test, T3 smoke doc, T4 backlog). ✓
- No Claude co-author in any commit message. ✓
- No `--amend`, no `--no-verify`, no force-push. ✓
- Smoke-gates-merge: Task 4 explicitly blocked until operator confirms T3 smoke. ✓
- The plan writes only — execution + audit happen in separate dispatches. ✓

**Approach decision documented:** The plan picks Approach 1+2 (default-disposition directive + categorical taxonomy with explicit allow-list) and defends it in the Architecture section. Approach 3 (few-shot examples) is rejected with rationale (too brittle, harder to maintain, inflates the cached system block). ✓

**Verified facts cited with file:line:**
- SYSTEM_PROMPT location: `layer_b_engine.py:42-64`. ✓
- `evaluate()` signature: `layer_b_engine.py:118`. ✓
- `_parse_verdict` action mapping: `layer_b_engine.py:212-216`. ✓
- ESCALATE silent-PASS fall-through: `layer_b_engine.py:219`. ✓
- Layer B retroactive WARN/BLOCK-only gate: `verdict_pipeline.py:169`. ✓
- Layer B ESCALATE-only trigger: `verdict_pipeline.py:134`. ✓
- Layer A novel-pattern fall-through: `layer_a_engine.py:67`. ✓
- 5 shell rules in `rules/shared/`: verified via grep 2026-05-24. ✓
- Existing tests mock the LLM: `tests/unit/test_layer_b_engine.py:13-23`. ✓
- ESCALATE enum definition: `redrobin/domain/opsec.py:21`. ✓

**Interface stability check:** The plan does NOT change:
- `LayerBEngine.evaluate(*, command: str, raw: str) -> Verdict` (signature unchanged). ✓
- `_parse_verdict` JSON-action mapping (PASS/WARN/BLOCK only; ESCALATE unchanged-absent). ✓
- `complete_with_cache` keyword shape (`stable_system_prompt` + cacheable blocks + dynamic_state + user_message). ✓
- `VerdictPipeline._run_layer_b` flow (still gated on `verdict.action == VerdictAction.ESCALATE`). ✓
The plan ONLY changes the contents of the `SYSTEM_PROMPT` string constant. If a future revision discovers the chosen prompt requires a downstream parser change (e.g., LLM starts returning "ESCALATE"), the executing subagent must STOP per the planning prompt's guardrail.

**Placeholder scan:** No "TBD", no "fill in details", no "implement later", no "similar to Task N", no "add appropriate error handling". Every step contains the actual content the engineer needs. Code blocks are present where code steps require them. ✓

**Type / phrase consistency:**
- `<T1 SHA>` placeholder used in Task 2 Step 4 commit message, Task 3 Step 1, Task 3 Step 3 commit message, Task 4 Step 1 (capture), Task 4 Step 2/3 (apply), Task 4 Step 4 commit message. Single name throughout. ✓
- `<T2 SHA>`, `<T3 SHA>` introduced only in Task 4 Step 4 commit message, where they are also captured. ✓
- Recon command list in Task 1 AFTER block, Task 2 assertions, and Task 3 smoke cases — phrasing kept consistent (e.g., `ipconfig`, `tasklist`, `whoami`, `hostname`, `systeminfo`, `net view`, `route print`, `arp -a`, `netstat`). ✓
- The three categories (STANDARD_RECON, RISKY_PAYLOAD, UNAMBIGUOUSLY_DANGEROUS) named identically across Task 1, Task 2, Task 3. ✓

**Cross-Task-2 / Task-1 dependency:** Task 2's assertions check for phrases that must exist verbatim in Task 1's AFTER block. The plan explicitly notes that if the operator amends the AFTER block during Task 1 Step 1 (optional refinement), the corresponding assertions in Task 2 must also be updated. ✓

Ready for audit.
