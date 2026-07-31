# Claude Code Project Setup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a best-practice, committed Claude Code configuration (`CLAUDE.md` + `.claude/{rules,skills,settings}`) to the Red Robin repo and migrate the one shared doc that lives only in the vault.

**Architecture:** Progressive disclosure per Anthropic's 2026 docs — a tight `CLAUDE.md` (<120 lines) that `@import`s `CONTRIBUTING.md`; five **path-scoped** `.claude/rules/` files that load only when Claude touches matching files; two on-demand skills; a committed `settings.json` permission allowlist. Personal context (the journal, `feedback_*`) stays out of the repo.

**Tech Stack:** Markdown + YAML frontmatter + JSON. No runtime code. Repo tooling: `uv`, `ruff`, `mypy`, `import-linter`, `pytest`.

## Global Constraints

- **This plan builds configuration/markdown, not runtime code.** The TDD test↔code↔commit cadence is adapted to **write ↔ verify ↔ commit**: each "verify" is a concrete check (`wc`/`grep`/JSON-validate/`git check-ignore`) or a marked manual runtime check (`/memory`, `/permissions`, live smoke). Every load-bearing factual claim in this plan was **verified against source on 2026-07-08** (`events.py`, `importlinter.cfg`, `ci.yml`, `redrobin.cna:197`, `config.py`, `redrobin/application/`).
- `CLAUDE.md` target **< 120 lines** (excluding the imported file). Move detail into a rule or skill, not `CLAUDE.md`.
- **Committed vs personal:** `CLAUDE.md` + `.claude/{rules,skills,settings.json}` are committed. The journal (`project_red_team_copilot.md`) and `feedback_*` stay in personal auto-memory — never committed.
- **No hooks, no MCP, no custom subagents, no `read-pyc` skill** (out of scope).
- **Commits:** conventional messages (`docs:`/`chore:`), atomic (one logical change per commit), **no AI co-author trailer**.
- **Branch:** work on a topic branch `feat-claude-setup` off `dev-juan`; do not commit to `dev-juan`/`main` directly.
- **⚠ Permission syntax:** confirm the exact `settings.json` permission pattern form against https://code.claude.com/docs/en/settings.md before writing Task 5 — the wrong form is a silent no-op.

---

### Task 1: Reconcile CONTRIBUTING.md + discoverability (prerequisite for the CLAUDE.md import)

**Files:**
- Modify: `CONTRIBUTING.md` (the "Pull request flow" / "Branch protection" section)

**Interfaces:**
- Produces: a contradiction-free `CONTRIBUTING.md` that `CLAUDE.md` will `@import` in Task 2.

- [ ] **Step 1: Create the topic branch**

Run:
```bash
git checkout dev-juan && git pull && git checkout -b feat-claude-setup
```
Expected: `Switched to a new branch 'feat-claude-setup'`.

- [ ] **Step 2: Verify the current contradiction**

Run: `grep -n "topic branch from" CONTRIBUTING.md`
Expected: matches `1. Create a topic branch from `main` (...)` — the line to fix.

- [ ] **Step 3: Edit the branch flow to match reality + add discoverability**

In `CONTRIBUTING.md`, replace the "Pull request flow" step 1 and append a discoverability note. Change:
```markdown
1. Create a topic branch from `main` (`feat-X`, `fix-Y`, `docs-Z`).
2. Open a PR against `main`.
```
to:
```markdown
1. Create a topic branch from `dev-juan` (`feat-X`, `fix-Y`, `docs-Z`).
2. Open a PR back into `dev-juan` (the integration branch). `main` is updated from `dev-juan` via PR at release.
```
Then add a short subsection at the end of the file:
```markdown
## Claude Code

This repo ships committed Claude Code config under `.claude/` (path-scoped `rules/`, `skills/`) plus a
root `CLAUDE.md`. Run `/memory` inside a session to see which instructions and rules are loaded.
```

- [ ] **Step 4: Verify no contradiction remains**

Run: `grep -n "topic branch from" CONTRIBUTING.md`
Expected: now reads `from `dev-juan``; the word `main` only appears in the release context. `grep -c "Claude Code" CONTRIBUTING.md` ≥ 1.

- [ ] **Step 5: Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs: reconcile CONTRIBUTING branch flow with dev-juan integration + note .claude config"
```

---

### Task 2: Root CLAUDE.md

**Files:**
- Create: `CLAUDE.md`

**Interfaces:**
- Consumes: `@CONTRIBUTING.md` (Task 1).
- Produces: the always-on project context; points to `.claude/rules/wire-protocol.md` (Task 3) and `docs/architecture-design.md` (Task 6).

- [ ] **Step 1: Write `CLAUDE.md`**

Create `CLAUDE.md` with exactly this content:
```markdown
# Red Robin — Claude Code Instructions

Red Robin is an **operation-aware AI copilot behind your C2** for red-team operators: a Python
daemon + Textual TUI + Cobalt Strike adapter. The OPSEC pre-flight gate (CS Aggressor `alias`
override) is the first feature and the capture hook that feeds the assistant — not the whole
product. Core is C2-agnostic; **adapters are the sole C2 boundary**.

@CONTRIBUTING.md

## Commands

- Setup: `uv sync --all-extras`
- CI gates (all must pass — mirrors `.github/workflows/ci.yml`):
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run mypy redrobin/domain redrobin/application`
  - `uv run lint-imports --config importlinter.cfg`
  - `uv run pytest tests/ -v --tb=short`
- Adapter tests are **separate** and NOT in the CI `pytest tests/` run — run them when you touch an
  adapter: `uv run pytest adapters/<name>/tests/`
- Local gotchas:
  - `uv run --no-sync ...` when the daemon is running (it locks `redrobin.exe`, so `uv run` re-sync fails).
  - `REDROBIN_ALLOW_DEGRADED=1` for tests that hit the boot-gate without API keys.

## Workflow

Superpowers methodology for any non-trivial change:
brainstorming → writing-plans → auditing-plans → subagent-driven-development → **operator smoke**.

- **`.cna` changes are validated by a live-beacon operator smoke, NOT by CI** — CI never runs the `.cna`.
- TDD, atomic commits, conventional messages, **no AI co-author trailer** (see CONTRIBUTING).

## Layout & pointers

- Architecture spec: `docs/architecture-design.md` (v1.2 design; some dates historical).
- Canonical wire API (event types, routes, enums, field names): `.claude/rules/wire-protocol.md`.
- Deeper dev walkthrough: `docs/developer-onboarding.md`.
- **Two unrelated "rules" dirs:** top-level `rules/` = Sigma/OPSEC Layer-A rules (product data);
  `.claude/rules/` = Claude Code path-scoped instructions (tooling).

## Branching (actual flow)

- `main` is protected (v0.1 shipped 2026-06-01). Do not push directly.
- `dev-juan` / `dev-jose` are operator integration branches.
- Topic branches off `dev-juan` (`feat-*`, `fix-*`, `docs-*`), merge back to `dev-juan`; `main` via PR at release.

<!-- Keep this file under ~120 lines. Move detail into .claude/rules/ (path-scoped) or a skill, not here. -->
```

- [ ] **Step 2: Verify size + import + no placeholders**

Run: `wc -l CLAUDE.md`
Expected: < 120 lines.
Run: `grep -n "@CONTRIBUTING.md" CLAUDE.md`
Expected: 1 match (the import).
Run: `grep -niE "TODO|TBD|FIXME" CLAUDE.md`
Expected: no output.

- [ ] **Step 3: Verify load (manual, runtime)**

In a fresh Claude session at the repo root, run `/memory`.
Expected: `CLAUDE.md` and the imported `CONTRIBUTING.md` appear in the loaded list.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "chore(claude): add project CLAUDE.md (imports CONTRIBUTING, CI commands, pointers)"
```

---

### Task 3: .claude/rules/ — five path-scoped rules

**Files:**
- Create: `.claude/rules/architecture.md`, `.claude/rules/wire-protocol.md`, `.claude/rules/aggressor-cna.md`, `.claude/rules/sigma-rules.md`, `.claude/rules/testing.md`

**Interfaces:**
- Produces: path-scoped facts consumed by any future task touching `redrobin/**`, `protocol/**`, `adapters/**/*.cna`, `rules/**`, `tests/**`. Referenced by `CLAUDE.md` (wire-protocol) and both skills.

- [ ] **Step 1: (Re)verify the wire facts before writing** (they are the highest-risk content)

Run:
```bash
grep -nE "class (Beacon|Opsec|Severity|Adapter|Credential|Host|Operator|Engagement)" redrobin/domain/events.py
grep -n "prefix=\"/v1/verdict\"\|@router.post(\"/quick\")" redrobin/api/verdicts_router.py
grep -n "REDROBIN_PORT\|default=7777" redrobin/infrastructure/config.py
grep -rn "\"action\":\"BLOCK\"\|hasmatch is a STATEFUL" adapters/cobaltstrike/redrobin.cna
```
Expected: confirms `OpsecVerdictEvent(source_event_id, severity, reason, rule_id?, suggestions[])`, beacon events use `bid`, route `POST /v1/verdict/quick`, port default 7777, and the `.cna` `ismatch` comment. If anything differs from the content below, adjust the rule text to match source.

- [ ] **Step 2: Write `.claude/rules/architecture.md`**

```markdown
---
paths:
  - "redrobin/**"
---

# Red Robin — Architecture Rules

Layered Clean/DDD architecture; dependency direction enforced in CI by import-linter
(`importlinter.cfg`). Four contracts (do not violate):

1. **Layered Architecture**: `api | tui` → `application` → `domain`.
2. **Domain never imports infrastructure** (nor application/api/tui). `redrobin/domain` is pure.
3. **Application never imports presentation** (`api`/`tui`).
4. **Presentation never imports infrastructure directly (must go via application)** — `api`/`tui` reach
   infra only through an application facade (`allow_indirect_imports`).

## Layers on disk

`redrobin/{api, tui, application, domain, infrastructure}` + `cli.py` + `cli_helpers/`.

## Facade pattern

`api`/`tui` reach infrastructure only via application facades: `application/*_lifecycle.py`
(`cs_adapter_lifecycle`, `longterm_lifecycle`, `rules_lifecycle`, `working_memory_lifecycle`) and
`application/transport_auth.py`. Never `from redrobin.infrastructure import ...` inside `api/` or `tui/`.

## Conventions

- **One ABC only**: `LLMProvider` (`redrobin/domain/llm.py`). Everything else concrete + duck typing.
- **mypy strict** runs only on `redrobin/domain` + `redrobin/application` (gradual in infrastructure).
- Do not confuse the top-level `rules/` (Sigma/OPSEC rule YAMLs — product data) with `.claude/rules/`
  (Claude Code instructions — tooling).
```

- [ ] **Step 3: Write `.claude/rules/wire-protocol.md`**

```markdown
---
paths:
  - "protocol/**"
  - "redrobin/domain/events.py"
---

# Red Robin — Wire Protocol (canonical; verified against redrobin/domain/events.py)

The daemon speaks only the wire protocol; adapters translate C2 formats. Event dataclasses live in
`redrobin/domain/events.py` and mirror `protocol/schemas/v0.1/` (round-trip enforced by
`tests/unit/test_domain_events.py`). All 12 schemas are `additionalProperties: false`, so `/v1/events`
rejects unknown keys. Field names are canonical — match them exactly.

## The 11 events (+ Envelope)

| type | fields |
|---|---|
| `adapter.connected` | `manifest`, `session_id` |
| `adapter.heartbeat` | (none) |
| `beacon.initial` | `bid`, `host`, `user`, `integrity_level`, `arch`, `pid`, `ppid` |
| `beacon.command_input` | `bid`, `operator`, `command`, `raw`, `task_id?`, `correlation_id?` |
| `beacon.command_output` | `bid`, `task_id`, `output`, `exit_status?`, `correlation_id?` |
| `host.observation` | `bid`, `host_attr` |
| `credential.observed` | `bid`, `kind` (ntlm/kerberos/plaintext), `account`, `value_redacted_flag` |
| `opsec.verdict` | `source_event_id`, `severity`, `reason`, `rule_id?`, `suggestions[]` |
| `operator.question` | `text`, `context_refs[]` |
| `operator.answer` | `question_id`, `text`, `citations[]` |
| `engagement.milestone` | `phase`, `milestone_id`, `status`, `progress_pct` |

Beacon events use **`bid`**, never `beacon_id`. `Envelope` = `{id, ts, source, event, spec_version,
extensions}`; `Envelope.to_dict()` drops `None` optionals.

## Two enums — do NOT conflate

- `Severity` (on events): `INFO | WARN | DANGER | ESCALATE`.
- `VerdictAction` (verdict pipeline / `/v1/verdict/quick` response): `PASS | WARN | BLOCK | ESCALATE`.
  **There is no `ALLOW`** (only a legacy fallback string in episodic records).

## Routes / ports

- Quick verdict: `POST /v1/verdict/quick` (bearer-protected; router `prefix="/v1/verdict"`).
- CS dispatch (adapter side): `POST /api/v1/beacons/{bid}/consoleCommand`.
- Daemon default port **7777** (`REDROBIN_PORT`).

## Gotchas

- `/v1/events` MUST publish to the EventBus **after** JSONL persistence (persist-without-republish =
  adapter traffic never reaches the reducer; Phase-2 Slice-16 bug).
- The internal **bus** verdict payload (what the reducer consumes) may add fields beyond the wire
  `OpsecVerdictEvent` (e.g. a beacon id, `source=layer_a|layer_b`). Verify in `verdict_pipeline.py`
  before relying on them — they are NOT on the wire event.
```

- [ ] **Step 4: Write `.claude/rules/aggressor-cna.md`**

```markdown
---
paths:
  - "adapters/**/*.cna"
---

# Red Robin — Cobalt Strike Aggressor (.cna) Rules

`adapters/cobaltstrike/redrobin.cna` runs in the CS Client (Sleep language), not the daemon. It is
the sole pre-flight gate + capture hook.

## Sleep gotchas (hard-won)

- **`hasmatch` is a STATEFUL iterator** — it caches its Matcher by string+pattern and toggles
  true/false across successive identical strings; used as a one-shot boolean it caused a fail-open
  (#144). Use stateless `ismatch '.*PATTERN.*'` (anchored) or `indexOf`. See `redrobin.cna:197`.
- In `redrobin.cna` the operator command line is hook arg **`$3`** (command name = `split(' ', $3)[0]`).
  The file uses **no `$0`/`$2` alias-arg parsing**; the only `substr` (~`redrobin.cna:325`) strips the
  trailing `" *"` elevated-user marker from `binfo`, not args.
- CS beacon hooks carry an **undocumented `$5` correlation id** — identical across the
  `input`/`tasked`/`output` of one command, unique per command. `beacon_output` `$4` = job id.
- Output observation uses `set BEACON_OUTPUT` (a filter hook returning `$2`), not `on beacon_output`.
- `alias` (parser plane) and `&b*` Sleep functions are **independent namespaces** — no recursion.

## Workflow rules

- **A `.cna` edit requires a Script Manager reload in CS** to take effect (a stale script looks like a
  code bug — it isn't).
- **`.cna` changes are validated ONLY by a live-beacon operator smoke, never by CI.** See the
  `smoke-test` skill.
```

- [ ] **Step 5: Write `.claude/rules/sigma-rules.md`**

```markdown
---
paths:
  - "rules/**"
---

# Red Robin — Layer A OPSEC Rules (rules/)

`rules/` holds the Sigma/OPSEC **Layer-A rules** (product data) — NOT Claude instructions (that's
`.claude/rules/`). YAML in `rules/shared/<rule_id>.yaml` (or `rules/personal/` for operator-private).
Loaded by `infrastructure/rules_loader.py`, consumed by `LayerAEngine`, schema in
`redrobin/domain/rules.py`. Hot-reloaded (watchdog on the rules dir).

## Two match shapes (see domain/rules.py)

1. **Native**: `match.command` + optional `match.arg_pattern`.
2. **Sigma-style**: `match_predicates` (tuple of `MatchPredicate`, AND-wise) supporting
   `CommandLine|contains`, `Image|endswith` (`.exe` basename coercion), plus `equals`/`startswith`/
   `endswith`, case-insensitive, list-OR. Unsupported fields/modifiers become a `_NeverMatchPredicate`
   (loads but never fires — warn, don't silently mis-match).

## Rule fields

`id`, `match.command`, `match.arg_pattern?`, `severity` (INFO/WARN/DANGER), `reason`, `suggestions?`,
`sources`.

## Adding a rule

Use the `new-sigma-rule` skill (or CONTRIBUTING "Adding a rule"). Add a test in
`tests/integration/test_bootstrap_rules.py` for core rules.
```

- [ ] **Step 6: Write `.claude/rules/testing.md`**

```markdown
---
paths:
  - "tests/**"
  - "adapters/**/tests/**"
---

# Red Robin — Testing Rules

- **TDD cadence**: failing test → verify red → minimal implementation → verify green → commit.
- **Layout**: `tests/unit/` (pure), `tests/integration/` (HTTP/integration), `adapters/<name>/tests/`
  (adapter — NOT run by CI's `pytest tests/`; run explicitly when touching an adapter).
- **Canonical enums in assertions**: `VerdictAction` = PASS/WARN/BLOCK/ESCALATE (**no `ALLOW`**);
  `Severity` = INFO/WARN/DANGER/ESCALATE.
- **Local gotchas**: `uv run --no-sync ...` when the daemon runs (locked `redrobin.exe`);
  `REDROBIN_ALLOW_DEGRADED=1` for boot-gated tests.
- Integration tests of the async reducer read `app.state.working_memory_repo` and poll — not a
  hardcoded working-memory path.
- mypy strict covers only `redrobin/domain` + `redrobin/application`.
```

- [ ] **Step 7: Verify frontmatter + no placeholders**

Run: `for f in .claude/rules/*.md; do echo "== $f =="; head -4 "$f"; done`
Expected: each file starts with `---` / `paths:` / a glob / `---`.
Run: `grep -rniE "TODO|TBD|FIXME" .claude/rules/`
Expected: no output.

- [ ] **Step 8: Verify load-on-match (manual, runtime)**

In a session: open `redrobin/domain/events.py`, then `/memory` → `wire-protocol.md` should be listed
as loaded (it wasn't before opening a matching file). Repeat opening a `*.cna` → `aggressor-cna.md`.

- [ ] **Step 9: Commit**

```bash
git add .claude/rules/
git commit -m "chore(claude): add 5 path-scoped rules (architecture, wire-protocol, cna, sigma, testing)"
```

---

### Task 4: .claude/skills/ — smoke-test + new-sigma-rule

**Files:**
- Create: `.claude/skills/smoke-test/SKILL.md`, `.claude/skills/new-sigma-rule/SKILL.md`

**Interfaces:**
- Consumes: `.claude/rules/aggressor-cna.md`, `.claude/rules/sigma-rules.md`, and existing `docs/smoke-test.md` / `docs/sigma-support.md` (referenced, not duplicated).

- [ ] **Step 1: Write `.claude/skills/smoke-test/SKILL.md`**

```markdown
---
name: smoke-test
description: Run the live-beacon operator acceptance smoke for a gate / .cna / TUI change in Red Robin
argument-hint: "[engagement-slug]"
---

# Live-Beacon Smoke Test

The ONLY real acceptance test for `.cna`, gate, and TUI changes (unit tests and CI cannot exercise the
CS client). Requires a live Cobalt Strike beacon and the operator.

## Procedure

1. Start the daemon on the working branch: `uv run redrobin daemon start` — use a clean engagement
   (e.g. `manual-smoke`) to avoid polluting `e2e-verify`. Set API keys (or `REDROBIN_ALLOW_DEGRADED=1`
   for gate-only tests).
2. In CS Client → Script Manager, **load/reload** `adapters/cobaltstrike/redrobin.cna` (a `.cna` edit
   does NOT take effect without a reload).
3. Confirm the handshake line in the console: `[Red Robin] handshake OK; session=ses_...`.
4. Run the acceptance cases for the change under test (BLOCK a dangerous command, PASS a benign one,
   confirm output observation reaches working memory, etc.).
5. Watch for the failure modes recorded in the runbooks.

## References (read, don't duplicate)

- `docs/smoke-test.md` — the full smoke procedure.
- `docs/superpowers/runbooks/` — per-feature acceptance runbooks.
- `.claude/rules/aggressor-cna.md` — the `.cna` gotchas.
```

- [ ] **Step 2: Write `.claude/skills/new-sigma-rule/SKILL.md`**

```markdown
---
name: new-sigma-rule
description: Add and validate a Red Robin Layer A OPSEC rule (rules/)
argument-hint: "<rule_id>"
---

# Add a Layer A OPSEC Rule

1. Create `rules/shared/<rule_id>.yaml` (or `rules/personal/` for operator-private):
   ```yaml
   id: <rule_id>
   match:
     command: <command>
     arg_pattern: <optional regex>
   severity: WARN   # INFO | WARN | DANGER
   reason: <why this is risky>
   suggestions:
     - <stealthier alternative>
   sources:
     - <detection reference / URL>
   ```
2. (Sigma-style alternative) use `match_predicates` — see `.claude/rules/sigma-rules.md`.
3. Validate: the rule loads (start the daemon or run the loader) and `LayerAEngine` matches the
   intended command. Unsupported fields degrade to a never-match predicate with a warning — check logs.
4. Add a test in `tests/integration/test_bootstrap_rules.py` for a core rule.
5. Run gates: `uv run pytest tests/integration/test_bootstrap_rules.py -v` + the full 5 CI gates.

## References

- `.claude/rules/sigma-rules.md`, `docs/sigma-support.md`, CONTRIBUTING "Adding a rule".
```

- [ ] **Step 3: Verify frontmatter**

Run: `for f in .claude/skills/*/SKILL.md; do echo "== $f =="; grep -E "^(name|description):" "$f"; done`
Expected: each SKILL.md has a `name:` and a `description:` line.
Run: `grep -rniE "TODO|TBD|FIXME" .claude/skills/`
Expected: no output.

- [ ] **Step 4: Verify discoverability (manual, runtime)**

In a session, type `/` and confirm `smoke-test` and `new-sigma-rule` appear in the skill/command menu.

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/
git commit -m "chore(claude): add smoke-test + new-sigma-rule skills (thin, reference existing docs)"
```

---

### Task 5: .claude/settings.json + .gitignore hygiene

**Files:**
- Create: `.claude/settings.json`
- Modify: `.gitignore`

**Interfaces:**
- Produces: a committed permission allowlist; a `.gitignore` that keeps shared `.claude/` tracked while ignoring personal overrides.

- [ ] **Step 1: Permission syntax (verified 2026-07-08 — no action needed unless the docs changed)**

Confirmed against https://code.claude.com/docs/en/permissions.md: Bash prefix rules use space-wildcard
`Bash(cmd *)` (the `:*` suffix is an equivalent trailing wildcard); file rules use gitignore-style paths
(`Read(.env)` matches at any depth, not `.env.example`). The JSON in Step 2 already uses the verified
form. Only revisit if the docs have changed.

- [ ] **Step 2: Write `.claude/settings.json`**

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "allow": [
      "Bash(uv run pytest *)",
      "Bash(uv run ruff *)",
      "Bash(uv run mypy *)",
      "Bash(uv run lint-imports *)",
      "Bash(uv sync *)",
      "Bash(git status *)",
      "Bash(git diff *)",
      "Bash(git log *)",
      "Bash(git add *)",
      "Bash(git commit *)",
      "Bash(gh pr *)",
      "Bash(gh issue *)"
    ],
    "deny": [
      "Read(.env)",
      "Edit(.env)",
      "Write(.env)",
      "Read(.env.local)",
      "Edit(.env.local)",
      "Write(.env.local)"
    ]
  }
}
```
Notes (verified against the permissions docs 2026-07-08):
- Space-wildcard `Bash(cmd *)` is the canonical form (what the "don't ask again" dialog writes); the
  `:*` suffix is an equivalent trailing wildcard. `Bash(uv run pytest *)` matches both `uv run pytest`
  and `uv run pytest tests/` (word boundary after the prefix).
- `Read(.env)` follows gitignore semantics — matches `.env` at any depth but **not** `.env.example`
  (different filename), so the committed template stays readable. `.env.local` is denied explicitly.
- **Deny-first precedence + workspace trust:** deny rules always win; project `allow` rules only take
  effect after the operator accepts the workspace-trust dialog (the allowlist may still prompt until
  then — not a bug). `git add`/`git commit` are auto-allowed to cut prompts — drop those two lines if
  you'd rather commits prompt.

- [ ] **Step 3: Validate JSON**

Run: `python -m json.tool .claude/settings.json > /dev/null && echo OK`
Expected: `OK` (valid JSON).

- [ ] **Step 4: Add gitignore hygiene**

First confirm `.claude/` is not already wholesale-ignored:
Run: `git check-ignore .claude/settings.json`
Expected: **no output** (it is NOT ignored → will be committed).
Then append to `.gitignore`:
```
# Claude Code — personal/local overrides (do not commit; shared .claude/ config IS committed)
.claude/settings.local.json
.claude/*.local.json
CLAUDE.local.md
```

- [ ] **Step 5: Verify ignore/track split**

Run: `git check-ignore .claude/settings.local.json CLAUDE.local.md`
Expected: both paths printed (ignored).
Run: `git check-ignore .claude/settings.json .claude/rules/architecture.md; echo "exit=$?"`
Expected: no paths printed, `exit=1` (they are tracked, not ignored).

- [ ] **Step 6: Verify the allowlist takes effect (manual, runtime)**

In a session, run an allowed command (e.g. `uv run ruff check .`) and confirm it runs **without** a
permission prompt. If it still prompts, the pattern form is wrong — revisit Step 1.

- [ ] **Step 7: Commit**

```bash
git add .claude/settings.json .gitignore
git commit -m "chore(claude): add settings.json permission allowlist + gitignore personal overrides"
```

---

### Task 6: Migrate the architecture spec into the repo

**Files:**
- Create: `docs/architecture-design.md` (copied from the vault, with a status-note header)

**Interfaces:**
- Consumes: `C:\Users\jcspi\Documents\spiedra-vault\Projects\red-robin-architecture-design.md` (source).
- Produces: `docs/architecture-design.md` referenced by `CLAUDE.md`.

- [ ] **Step 1: Copy the source file into the repo**

Run:
```bash
cp "C:/Users/jcspi/Documents/spiedra-vault/Projects/red-robin-architecture-design.md" docs/architecture-design.md
wc -l docs/architecture-design.md
```
Expected: ~2574 lines copied.

- [ ] **Step 2: Prepend the status-note header**

Insert at the very top of `docs/architecture-design.md`:
```markdown
> **Status note (2026-07-08):** This is the v1.2 *design* spec (authored 2026-05-10). Some dates and
> scope statements are historical — v0.1 shipped to `main` 2026-06-01. For current canonical API
> surfaces (event types, routes, enums) see `.claude/rules/wire-protocol.md`; for the shipped state
> see the repo git history. Do not "fix" this doc's internal contradictions here — that is a separate
> reconciliation task.

---

```
(Do NOT edit the original spec body — only prepend the note.)

- [ ] **Step 3: Verify**

Run: `head -8 docs/architecture-design.md`
Expected: shows the status note.
Run: `grep -c "Mission" docs/architecture-design.md` (or another known heading)
Expected: ≥ 1 (the original body is intact).

- [ ] **Step 4: Commit**

```bash
git add docs/architecture-design.md
git commit -m "docs: migrate v1.2 architecture-design spec into the repo (with status note)"
```

---

### Task 7: Personal memory housekeeping (NOT committed to the repo)

**Files:**
- Modify (outside the repo): `C:\Users\jcspi\.claude\projects\C--Users-jcspi-Documents-dev-projects-Red-Robin\memory\redrobin-source-is-pyc-only.md` and `MEMORY.md`

**Interfaces:**
- These are personal auto-memory files, not tracked by the repo. No git commit.

- [ ] **Step 1: Verify the stale claim**

Run: `find redrobin -name '*.py' | wc -l`
Expected: 94 (real `.py` source exists → the "pyc-only" memory is stale).

- [ ] **Step 2: Correct the memory**

Delete `redrobin-source-is-pyc-only.md` (the working tree has normal `.py` source). Remove its line
from the memory `MEMORY.md` index. If any nuance is worth keeping (e.g. a *release artifact* ships
`.pyc`), rewrite it precisely instead of deleting.

- [ ] **Step 3: Verify index consistency**

Confirm `MEMORY.md` no longer references the deleted file, and still lists
`redrobin-project-journal-location.md` and `redrobin-llm-architecture.md`.

- [ ] **Step 4: (No commit)** — personal memory is not part of the repo.

---

### Task 8: Final verification against acceptance criteria

**Files:** none (verification only)

- [ ] **Step 1: Repo integrity — CI gates still green**

Run:
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy redrobin/domain redrobin/application && uv run lint-imports --config importlinter.cfg && uv run pytest tests/ -q
```
Expected: all pass (config/markdown additions do not touch Python; this confirms nothing regressed).

- [ ] **Step 2: Nothing personal committed**

Run: `git log --oneline dev-juan..feat-claude-setup` and `git diff --stat dev-juan..feat-claude-setup`
Expected: only `CLAUDE.md`, `.claude/**`, `.gitignore`, `CONTRIBUTING.md`, `docs/architecture-design.md`.
No journal / `feedback_*` / status files.

- [ ] **Step 3: Behavioral spot-check (manual, runtime)**

In a fresh session, confirm Claude answers *from the config*: the 5 CI gates (from `CLAUDE.md`); the
`hasmatch`→`ismatch` gotcha (after opening a `*.cna`); `bid` vs `beacon_id` on `beacon.command_output`
(after opening `redrobin/domain/events.py`). Confirms rules load and are cited.

- [ ] **Step 4: Structural acceptance checklist**

Confirm: `CLAUDE.md` < 120 lines with `@CONTRIBUTING.md`; 5 rule files with `paths:`; 2 skills with
`name`/`description`; `settings.json` valid + allowlist effective; `.env` denied but `.env.example`
not; `.gitignore` ignores `*.local.*`, tracks shared `.claude/`; `docs/architecture-design.md` present
with header; `CONTRIBUTING.md` branch flow reconciled.

- [ ] **Step 5: Open the PR (when the operator approves)**

```bash
git push -u origin feat-claude-setup
gh pr create --base dev-juan --title "Claude Code project setup (CLAUDE.md + .claude/)" --body "Implements docs/superpowers/specs/2026-07-08-claude-code-setup-design.md"
```

---

## Self-Review

**1. Spec coverage:**
- §5.1 CLAUDE.md → Task 2. §5.2 rules (5) → Task 3. §5.3 skills (2) → Task 4. §5.4 settings → Task 5.
  §5.5 doc migration → Task 6. §5.6 CONTRIBUTING reconcile + discoverability → Task 1. §5.7 personal
  memory → Task 7. §5.8 gitignore → Task 5. §6 out-of-scope → honored (no hooks/MCP/agents/read-pyc).
  §7 acceptance → Task 8. All covered.

**2. Placeholder scan:** No `TODO`/`TBD`/"add appropriate…". Every file's full content is inline;
`<rule_id>`/`<command>`/`<name>` inside the `new-sigma-rule` skill are intentional user-supplied
template slots (that skill's purpose), not plan placeholders.

**3. Type/name consistency:** `VerdictAction` (PASS/WARN/BLOCK/ESCALATE, no ALLOW) and `Severity`
(INFO/WARN/DANGER/ESCALATE) used consistently across `wire-protocol.md`, `testing.md`, and the spec.
Event field names (`bid`, `source_event_id`, etc.) match `events.py` verified in Task 3 Step 1. Facade
filenames (`cs_adapter_lifecycle`, `longterm_lifecycle`, `rules_lifecycle`, `working_memory_lifecycle`,
`transport_auth`) match `redrobin/application/`. Route `POST /v1/verdict/quick` and port `7777` match
source.
