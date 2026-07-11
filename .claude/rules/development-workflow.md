---
# No `paths:` field → this rule loads every session (standing policy, same priority as .claude/CLAUDE.md).
---

# Red Robin — AI-Driven Development Lifecycle (AI-DLC)

Non-trivial changes follow this order. Each phase is a skill — **invoke it, don't improvise**.
Trivial/mechanical edits (a typo, a log line, a rename) skip the cycle; anything touching
`redrobin/` or `adapters/` does not.

1. **brainstorming** → a design spec in `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`
2. **writing-plans** → an executable plan in `docs/superpowers/plans/`
3. **auditing-plans** → verify the spec **and** the plan against source BEFORE coding — assume nothing
4. **subagent-driven-development** (or **executing-plans**) → implement task-by-task: a fresh subagent
   per task + a spec-and-quality review gate per task; a whole-branch review at the end
5. **operator smoke** (skill **smoke-test**) → the ONLY acceptance for `.cna` / gate / TUI changes
6. **finishing-a-development-branch** → merge / PR back to the integration branch

Per-phase gates (TDD, the 5 CI gates, atomic conventional commits, no AI co-author trailer) live in `CLAUDE.md`.

## Where these skills live

- **In this repo** (`.claude/skills/`, travel with a `git pull`): `auditing-plans`, `smoke-test`, `new-sigma-rule`.
- **From the `superpowers` plugin** (install separately): `brainstorming`, `writing-plans`,
  `subagent-driven-development`, `executing-plans`, `finishing-a-development-branch`.
- A collaborator without the plugin follows the same order manually.
