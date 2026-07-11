# Claude Code Project Setup — Design Spec

- **Date:** 2026-07-08
- **Status:** approved (brainstorming), pending implementation plan
- **Author:** Juan (spiedra) + Claude
- **Scope:** Add a best-practice Claude Code configuration to the Red Robin repo (`CLAUDE.md`, `.claude/rules/`, `.claude/skills/`, `.claude/settings.json`) and migrate the one missing shared doc. Grounded in Anthropic's official 2026 Claude Code docs (see §9).

---

## 1. Problem & Goal

The Red Robin repo has **zero Claude Code configuration** — no `CLAUDE.md`, no `.claude/`. The project's accumulated knowledge is scattered across four places: the repo `docs/`, the Obsidian vault (`spiedra-vault/Projects/`), Claude auto-memory (the `project_red_team_copilot.md` journal + `feedback_*` files), and this repo's small auto-memory. A fresh Claude session (or the co-speaker Jose's Claude) starts blind to the project's conventions, architecture, and the many hard-won `.cna`/wire-protocol gotchas.

**Goal:** Give any Claude session working in this repo an optimized, correct starting context using Anthropic best practices — **committed, shareable** config only — while keeping personal/session context (the internal journal, personal feedback) out of the shared repo.

**Non-goal:** Rewriting or reconciling the historical planning docs; building automation nobody needs (hooks, MCP, custom agents).

## 2. Scope Decisions (locked)

- **Committed vs personal:** `CLAUDE.md` + `.claude/` (rules/skills/settings) are committed and shared. The internal journal (`project_red_team_copilot.md`) and `feedback_*` preferences **stay personal** in Claude auto-memory — not in the public repo.
- **Migrate the architecture spec** (`red-robin-architecture-design.md`) into the repo — it is the only shared doc that lives *only* in the vault.
- **Project status stays personal;** only stable thesis/architecture goes in the committed `CLAUDE.md`.
- **`permissions.deny` for `.env`/secrets** in `settings.json` — yes.
- **No hooks** (operator decision, 2026-07-08). No MCP, no custom subagents, no `read-pyc` skill.

## 3. Best-Practice Principles Applied (Anthropic, verified §9)

1. **`CLAUDE.md` tight (<200 lines)** — loaded in full every session; longer files reduce adherence and cost context.
2. **Progressive disclosure** — path-scoped `.claude/rules/` load only when Claude touches matching files; skills load on-demand. Keep always-on context minimal.
3. **Single source of truth** — `CLAUDE.md` imports `@CONTRIBUTING.md` rather than duplicating conventions.
4. **Skills over commands** — Anthropic deprecates `.claude/commands/` in favor of `.claude/skills/` (same `/name`, plus file bundling).
5. **Build/test commands → `CLAUDE.md`; multi-step procedures → skills; path-specific guidance → rules.**
6. **Specificity** — every instruction concrete and verifiable (exact commands, exact contract names).

## 4. Target Structure

```
Red-Robin/
├── CLAUDE.md                       # NEW — <120 lines, imports @CONTRIBUTING.md
├── .claude/
│   ├── settings.json               # NEW — permission allowlist + .env deny; no hooks
│   ├── rules/                       # NEW — 5 path-scoped rule files
│   │   ├── architecture.md          #   paths: redrobin/**
│   │   ├── wire-protocol.md          #   paths: protocol/**, redrobin/domain/events.py
│   │   ├── aggressor-cna.md          #   paths: adapters/**/*.cna
│   │   ├── sigma-rules.md            #   paths: rules/**
│   │   └── testing.md                #   paths: tests/**, adapters/**/tests/**
│   └── skills/                      # NEW — on-demand
│       ├── smoke-test/SKILL.md
│       └── new-sigma-rule/SKILL.md
└── docs/
    └── architecture-design.md      # MIGRATED from vault (with a stale-note header)
```

Personal (NOT committed): the journal + `feedback_*` remain in Claude auto-memory.

## 5. Component Specs

### 5.1 `CLAUDE.md` (root, target <120 lines)

Sections:
- **Identity & thesis** (≤6 lines): what Red Robin is + the evolved thesis ("operation-aware AI copilot behind your C2"; the OPSEC gate is the first feature + the capture hook, not the whole product).
- **Commands** (exact, from `ci.yml`):
  - Setup: `uv sync --all-extras`
  - The 5 CI gates: `uv run ruff check .` · `uv run ruff format --check .` · `uv run mypy redrobin/domain redrobin/application` · `uv run lint-imports --config importlinter.cfg` · `uv run pytest tests/ -v --tb=short`
  - Note: **adapter tests are separate** (`adapters/<name>/tests/`) and are NOT in the CI `pytest tests/` run; run them explicitly when touching an adapter.
  - Local gotchas: `uv run --no-sync` when the daemon is running (it locks `redrobin.exe`); `REDROBIN_ALLOW_DEGRADED=1` for tests that hit the boot-gate without API keys.
- **`@CONTRIBUTING.md`** import — reuses commit conventions (atomic, conventional, **no AI co-author**), tooling, TDD, layering, "adding a rule/adapter". *Prerequisite:* reconcile CONTRIBUTING's branch section first (see §5.6) so no contradiction enters context.
- **Workflow**: superpowers methodology (brainstorm → writing-plans → auditing-plans → subagent-driven-development → **operator smoke**). Hard rule: **`.cna` changes are validated by a live-beacon smoke, NOT by CI** (CI never runs the `.cna`).
- **Pointers**: architecture spec → `docs/architecture-design.md`; canonical API surfaces → `.claude/rules/wire-protocol.md`; deeper dev walkthrough → `docs/developer-onboarding.md` (ensure no contradiction with this file).
- **Naming disambiguation** (one line): the top-level `rules/` directory holds **Sigma/OPSEC Layer-A rules** (product data); `.claude/rules/` holds **Claude Code path-scoped instructions** (tooling). They are unrelated.
- **Branching (actual)**: `main` protected (v0.1 shipped 2026-06-01); `dev-juan`/`dev-jose` are working branches; topic branches off `dev-juan`, merge back to `dev-juan`.

Uses block-level HTML comments (stripped from context) for any maintainer-only notes.

> **Always-on context budget:** `CLAUDE.md` (~120 lines) + the `@CONTRIBUTING.md` import (~80) ≈ 200 lines loaded every session. The 5 rules are path-scoped and do **not** count against this. Keep future additions within this budget; move detail to a rule or skill instead of growing `CLAUDE.md`.

### 5.2 `.claude/rules/` — 5 path-scoped files

Each begins with YAML frontmatter `paths:` so it loads only when Claude reads matching files.

**architecture.md** — `paths: ["redrobin/**"]`
- The 4 import-linter contracts, by name, from `importlinter.cfg`: *Layered Architecture* (`api|tui → application → domain`), *Domain never imports infrastructure*, *Application never imports presentation*, *Presentation never imports infrastructure directly (must go via application)* (`allow_indirect_imports`).
- Layer dirs: `redrobin/{api, tui, application, domain, infrastructure}` + `cli.py`, `cli_helpers/`.
- **Facade pattern**: presentation reaches infra via `application/*_lifecycle.py` (e.g. `rules_lifecycle`, `working_memory_lifecycle`, `longterm_lifecycle`, `cs_adapter_lifecycle`) and `application/transport_auth.py` — never `from infrastructure import` in `api/`/`tui/`.
- **One ABC only**: `LLMProvider` (`domain/llm.py`); everything else concrete + duck typing.
- **mypy strict** only on `redrobin/domain` + `redrobin/application` (gradual in infrastructure).
- **Do not confuse** the top-level `rules/` (Sigma/OPSEC Layer-A rule YAMLs — product data) with `.claude/rules/` (Claude Code instructions — tooling).

**wire-protocol.md** — `paths: ["protocol/**", "redrobin/domain/events.py"]`
- The 11 canonical wire events + fields **as defined in `redrobin/domain/events.py`** (verified against source 2026-07-08). Notably: `beacon.command_output` = `bid` (NOT `beacon_id`) + `task_id` + `output` + optional `exit_status`/`correlation_id`; `beacon.initial` = `bid, host, user, integrity_level, arch, pid, ppid`; `beacon.command_input` = `bid, operator, command, raw` (+ optional `task_id`/`correlation_id`); `opsec.verdict` (`OpsecVerdictEvent`) = `source_event_id, severity, reason` + optional `rule_id` + `suggestions[]`.
- **Two distinct enums — do not conflate**: `Severity` = INFO/WARN/DANGER/ESCALATE (on events); `VerdictAction` = PASS/WARN/BLOCK/ESCALATE (pipeline/router response). **There is no `ALLOW`** (only a legacy fallback string). Assertions use canonical names.
- Verdict route: `POST /v1/verdict/quick` (bearer-protected: `prefix="/v1/verdict"` + `@router.post("/quick")`). Daemon default port **7777** (`REDROBIN_PORT`). Schemas are `additionalProperties: false` (12 schema files) — `/v1/events` rejects unknown keys on ingest.
- **Wire vs bus:** the internal EventBus verdict payload consumed by the reducer may carry more/other fields (e.g. a beacon id, `source=layer_a|layer_b`) than the wire `OpsecVerdictEvent`. The journal's "beacon_id/source/correlation_id" described the *bus* payload — verify in `verdict_pipeline.py` before relying on those, don't assume they're on the wire event.
- `/v1/events` MUST publish to the EventBus **after** JSONL persistence (Phase-2 Slice-16 bug).

> ⚠ Correction (verified 2026-07-08): an earlier draft of this bullet used journal-sourced `opsec.verdict` fields (`beacon_id`/`verdict`/`source`) that do **not** match the wire `OpsecVerdictEvent`. The plan bakes the verified content above.

**aggressor-cna.md** — `paths: ["adapters/**/*.cna"]`
- **`hasmatch` is a stateful iterator** — never use as a one-shot boolean; use `ismatch '.*PATTERN.*'` (anchored, stateless) or `indexOf`.
- In `redrobin.cna`, operator args come from hook `$3` (command = `split(' ', $3)[0]`), correlation id `$5`; the file uses **no** `$0`/`$2` alias-arg parsing (the lone `substr`, ~`redrobin.cna:325`, strips the `" *"` elevated-user marker, not args).
- CS beacon hooks carry an **undocumented `$5` correlation id** (same across input/tasked/output of one command, unique per command); `beacon_output` `$4` = job id.
- Output observation uses `set BEACON_OUTPUT` (filter hook, `return $2`), not `on beacon_output`.
- `alias` (parser plane) and `&b*` Sleep functions are **independent namespaces** — no recursion.
- **A `.cna` edit REQUIRES a Script Manager reload in CS** to take effect.
- **`.cna` changes are smoke-only, not covered by CI.** Live-beacon operator smoke is the *only* real acceptance test for `.cna` changes.

**sigma-rules.md** — `paths: ["rules/**"]`
- Native schema: `rules/shared/<rule_id>.yaml` (or `rules/personal/` for operator-private): `id`, `match.command`, `match.arg_pattern` (optional), `severity` (INFO/WARN/DANGER), `reason`, `suggestions` (optional), `sources`.
- Sigma-style: `match_predicates` (AND-wise) supporting `CommandLine|contains`, `Image|endswith` (`.exe` basename coercion), `equals`/`startswith`/`endswith`, case-insensitive, list-OR.
- Unsupported fields/modifiers load as `_NeverMatchPredicate` (loads, never fires) — warn, don't silently mis-match.
- Hot-reload: the loader watches `rules_dir` and calls `update_rules`.

**testing.md** — `paths: ["tests/**", "adapters/**/tests/**"]`
- TDD cadence: write failing test → verify red → implement → verify green → commit (atomic).
- Assertions use canonical `VerdictAction` names (no `ALLOW`).
- Layout: `tests/unit/` (pure), `tests/integration/` (HTTP), `adapters/<name>/tests/` (adapter).
- `uv run --no-sync` when the daemon runs (locked exe); `REDROBIN_ALLOW_DEGRADED=1` for boot-gated tests.
- Integration tests of the async reducer should read `app.state.working_memory_repo` and poll — not a hardcoded WM path.

### 5.3 `.claude/skills/` — 2 skills

**`smoke-test/SKILL.md`**
- Frontmatter: `name: smoke-test`, `description: Run the live-beacon operator acceptance smoke for a gate/.cna/TUI change` (auto-invocable), `argument-hint: [engagement-slug]`.
- Body: thin orchestrator — start daemon (`wm/*` branch or dev-juan), load/reload the `.cna` in CS Script Manager, use a clean engagement, run the acceptance cases, watch-items. **References existing `docs/smoke-test.md` + `docs/superpowers/runbooks/`** rather than duplicating them.

**`new-sigma-rule/SKILL.md`**
- Frontmatter: `name: new-sigma-rule`, `description: Add and validate a Layer A OPSEC rule`, `argument-hint: <rule_id>`.
- Body: thin — schema + example (from §5.2), then the validate/test procedure (rule loads, `LayerAEngine` matches, add a test in `tests/integration/test_bootstrap_rules.py`). **References `CONTRIBUTING.md` "Adding a rule" + `docs/sigma-support.md`.**

### 5.4 `.claude/settings.json` (committed)

- `permissions.allow`: conservative allowlist to cut permission prompts (shared with Jose): `Bash(uv run pytest*)`, `Bash(uv run ruff*)`, `Bash(uv run mypy*)`, `Bash(uv run lint-imports*)`, `Bash(uv sync*)`, `Bash(git status*)`, `Bash(git diff*)`, `Bash(git log*)`, `Bash(git add*)`, `Bash(git commit*)`, `Bash(gh pr*)`, `Bash(gh issue*)`.
- `permissions.deny`: `.env` + `.env.local` for Read/Edit/Write (gitignore-style; `.env` does **not** match the committed `.env.example`). Deny-first precedence; project `allow` rules apply only after the workspace-trust dialog is accepted (verified against permissions docs 2026-07-08 — space-wildcard `Bash(cmd *)`, `:*` equivalent).
- No `hooks`. No `model` override (inherit session). Personal overrides go in `settings.local.json` (gitignored — see §5.8).
- **⚠ Verify permission syntax at implementation time** against the official settings docs (https://code.claude.com/docs/en/settings.md). Modern Claude Code uses prefix rules like `Bash(uv run pytest:*)`; the wrong pattern form is a **silent no-op** — permissions simply never match, with no error. Confirm the exact form before writing the file, and validate that one allowed command runs without a prompt.

### 5.5 Doc migration

- Copy `spiedra-vault/Projects/red-robin-architecture-design.md` → `docs/architecture-design.md`.
- Prepend a 3-line header: "v1.2 design spec (2026-05-10). Some dates are historical. For current canonical API surfaces see `.claude/rules/wire-protocol.md`; for the shipped state see the repo git history."
- Do **not** fix the spec's internal contradictions now (separate reconciliation task).
- Already in the repo (untouched): `docs/superpowers/{specs,plans,runbooks}`, `docs/paper/`.
- Vault `Projects/` docs: left as-is (personal vault). Optional (out of scope): a one-line "superseded by repo" pointer.

### 5.6 CONTRIBUTING reconciliation (prerequisite for the @import)

`CONTRIBUTING.md` says "Create a topic branch from `main`", which contradicts the actual `dev-juan` integration flow. Fix that one section to match reality (topic branches off `dev-juan`, merge back to `dev-juan`; `main` via PR at release) so the `@CONTRIBUTING.md` import introduces no contradiction into context.

**Discoverability:** add one line to `CONTRIBUTING.md` (and optionally `README.md`) noting the repo ships Claude Code config under `.claude/`, and that `/memory` lists which rules/instructions are loaded — so Jose and any contributor know it exists and how to inspect it.

### 5.7 Personal memory housekeeping (not committed)

- **Correct the stale memory** `redrobin-source-is-pyc-only.md`: the current working tree has real `.py` source (94 files); update or delete it.
- Keep the `redrobin-project-journal-location.md` pointer and `redrobin-llm-architecture.md` (verify models: sonnet-4-6 judge, haiku-4-5 enricher/summarizer, voyage-4-large).
- **Bridge note:** the full project journal lives under the *vault's* Claude project memory, not this repo's — a fresh session in this repo does not auto-load it. The already-added `redrobin-project-journal-location.md` pointer (in this repo's auto-memory) is the intentional bridge; keep it.

### 5.8 `.gitignore` hygiene

When committing `.claude/`, ensure the repo `.gitignore` **ignores personal/local files** while keeping shared ones tracked:

- **Ignore:** `.claude/settings.local.json`, `CLAUDE.local.md` (and `.claude/*.local.json`).
- **Keep tracked:** `CLAUDE.md`, `.claude/settings.json`, `.claude/rules/**`, `.claude/skills/**`.

This prevents personal overrides or local secrets from being committed. First confirm the current `.gitignore` doesn't already ignore all of `.claude/` (which would silently drop the shared config from git).

## 6. Out of Scope (YAGNI)

Hooks, MCP (`.mcp.json`), custom subagents (`.claude/agents/`), a `read-pyc` skill, a `ci-gates` skill (the commands live in `CLAUDE.md`), and any rewrite of historical planning docs. Each can be added later if a concrete need appears.

## 7. Acceptance Criteria

- `CLAUDE.md` exists at repo root, <120 lines (excluding the imported file), imports `@CONTRIBUTING.md`, and contains the exact CI commands.
- `.claude/rules/` has the 5 files, each with correct `paths:` frontmatter; opening a matching file surfaces the rule (verify via `/memory`).
- `.claude/skills/` has `smoke-test` and `new-sigma-rule`, each with valid `name`/`description` frontmatter; they reference existing docs rather than duplicating them.
- `.claude/settings.json` allowlist reduces prompts for the listed commands; `.env` is denied.
- `docs/architecture-design.md` exists with the stale-note header.
- `CONTRIBUTING.md` branch section matches the `dev-juan` flow; no contradiction with `CLAUDE.md`.
- All new instructions are specific and verifiable; no placeholders/TODOs; no contradictions across `CLAUDE.md` + rules (best-practice consistency check).
- Nothing personal (journal, `feedback_*`, session status) is committed to the repo.
- **`.gitignore`** ignores `.claude/settings.local.json` + `CLAUDE.local.md`, and the shared `.claude/` files are tracked (`git status` / `git check-ignore` confirm).
- **Behavioral spot-check** (not just structural): in a fresh session, Claude answers correctly *from the config* — e.g. the 5 CI gates; the `hasmatch`→`ismatch` `.cna` gotcha (after opening a `.cna` file); `bid` vs `beacon_id` on `beacon.command_output` (after opening a wire file). Confirms the rules actually load and are cited.
- The permission allowlist takes effect (an allowed command runs without a prompt); syntax verified per §5.4.

## 8. Implementation Order (for the plan)

1. Reconcile `CONTRIBUTING.md` branch section (§5.6).
2. `CLAUDE.md` (§5.1).
3. `.claude/rules/` × 5 (§5.2).
4. `.claude/skills/` × 2 (§5.3).
5. `.claude/settings.json` (§5.4).
6. Migrate `docs/architecture-design.md` (§5.5).
7. `.gitignore` hygiene (§5.8) + discoverability line in `CONTRIBUTING.md`/`README.md` (§5.6).
8. Personal memory housekeeping (§5.7) — outside the repo, not part of the commit.
9. Verify acceptance criteria (§7): `/memory` check that rules load on matching files, the behavioral spot-check, and the permission + `.gitignore` checks.

## 9. References (Anthropic official, verified 2026-07-08)

- Memory / CLAUDE.md / `.claude/rules/` / auto-memory: https://code.claude.com/docs/en/memory.md
- `.claude` directory reference: https://code.claude.com/docs/en/claude-directory.md
- Skills (SKILL.md, progressive disclosure): https://code.claude.com/docs/en/skills.md
- Extend Claude Code (when to use which mechanism): https://code.claude.com/docs/en/features-overview.md
- Best practices (context window): https://code.claude.com/docs/en/best-practices.md
- Hooks (deferred): https://code.claude.com/docs/en/hooks-guide.md

Repo facts verified against: `pyproject.toml`, `.github/workflows/ci.yml`, `importlinter.cfg`, `CONTRIBUTING.md`, `redrobin/` tree (2026-07-08).
