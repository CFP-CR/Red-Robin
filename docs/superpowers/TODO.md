# Red Robin — TODO

Live tracker of **open work**. Resolved history + deep per-issue detail live in [backlog.md](./backlog.md); full specs for in-flight work live in [plans/](./plans/). Keep this file lean — when an item ships, move its detail to backlog.md and leave a one-line entry under "Recently shipped".

**Workflow (every plan):** real root cause → instrument, don't guess → production-grade fix (scalable + maintainable) → `/writing-plans` → `/auditing-plans` → `/subagent-driven-development` (Opus 4.7) → operator smoke.
**Verification gate:** live-beacon smoke for any gate / `.cna` / TUI change; unit tests + local daemon for Python. Atomic commits, no AI co-author.

---

## Now / Next
**TUI → Textual redesign** — migrating `redrobin/tui/` prompt_toolkit → **Textual** (spec `specs/2026-05-25-tui-textual-redesign-design.md`; 6-phase breakdown). **#155 dissolved** (reactive render); **#156** (WM-seed) lands in Phase 6. **Phases 1–3 ✅ merged to `dev-juan`; Phase 4 (Inspector `ModalScreen`) ✅ done** on branch `tui/phase-4-inspector` (539 suite green) → PR to `dev-juan`. **Next: Phase 5 — navigation** (Command Palette providers + `BINDINGS` + `?` help + Tab focus-cycling).

---

## Open — Plans (TUI next, then J–K)

### TUI → Textual redesign · P1 · core operator UX  *(branch `tui/textual-redesign`)*
> Migrating `redrobin/tui/` to **Textual** (the production-grade Python TUI framework; research + in-domain market validated). Spec: [specs/2026-05-25-tui-textual-redesign-design.md](./specs/2026-05-25-tui-textual-redesign-design.md). 6 phases, each its own writing→auditing→subagent-driven→smoke cycle. Built ALONGSIDE the legacy prompt_toolkit client; entrypoint swap is Phase 6.
- [x] **Phase 1 — foundation** ✅ — `redrobin/tui/textual_ui/`: app shell + `theme.tcss` tokens + frame-routing registry (seam) + reactive store + WS worker + reserved `#advisor-dock`. **#155 dissolves** (reactive render; daemon↔registry contract test green). Plan: [plans/2026-05-25-tui-textual-foundation.md](./plans/2026-05-25-tui-textual-foundation.md). *(audit-clean; 15 tests; 492 suite green.)*
- [x] **Phase 2 — panels** ✅ — Sessions `DataTable`; Verdicts/Events `RichLog` (color-coded, Layer B in-place correction by correlation_id, no raw JSON → **#148 fixed**); Working Memory pane — all bound to the reactive store. *(branch `tui/phase-2-panels`; 13 tests; 508 suite green. Visual SVG snapshot test deferred — cross-platform baseline fragility; behavior covered by Pilot + integration tests.)*
- [x] **Phase 3 — color + chrome** ✅ — TopBar (connection dot + label + live verdict counts), Working Memory out-of-scope in red (`$scope-out`), focus accent (`:focus-within`), responsive 80×24 hint; all 16-color-safe (label + weight + color) and driven by the reactive store. Plan: [plans/2026-05-26-tui-textual-phase3-color-chrome.md](./plans/2026-05-26-tui-textual-phase3-color-chrome.md). *(branch `tui/phase-3-color-chrome`; audit-clean; 12 new tests; 522 suite green. Footer left as-is — its Inspector/Palette/Sort bindings arrive in Phases 4–5.)*
- [x] **Phase 4 — Inspector `ModalScreen`** ✅ — `i` opens / `Esc` closes; left section nav with counts; filter `Input`; group-by kind|beacon for findings; native scroll (`OptionList`); copy-row (`y` → clipboard). dict-based section helpers + markup-safe `Text` prompts. Plan: [plans/2026-05-26-tui-textual-phase4-inspector.md](./plans/2026-05-26-tui-textual-phase4-inspector.md). *(branch `tui/phase-4-inspector`; audit-clean; 16 new tests; 539 suite green. Open key is `i` not `^I` — Ctrl+I == Tab in terminals.)*
- [ ] **Phase 5 — navigation** — Command Palette providers (seam) + `BINDINGS` + `?` help/keymap + focus model.
- [ ] **Phase 6 — #156 + entrypoint swap** — seed `WorkingMemory` from `engagement.yaml` at load (daemon-side) + swap `redrobin tui` → the Textual app + retire the legacy prompt_toolkit client.
- [ ] **(future, separate plan)** — AI Advisor *logic* (UI mapped in the spec §3.6; bus-subscriber LLM agent reacting to WM+lessons+notes → next-step recommendations; can't bypass the gate). Its seam (frame-routing registry + `#advisor-dock`) is reserved by Phase 1.
- **Accept (per phase):** tests green + live operator smoke. Final: TUI visibly updates live (no reconnect), verdicts color-coded, Inspector scroll/filter, engagement scope/client/ROE shown.

### Plan J · P2 · robustness
- [ ] **#145** — kerberos finding false-positive (fires on `Cached Tickets: (0)`).
- [ ] **#147** — Inspector "LESSONS" is just the latest verdict — mislabeled + not accumulated. *(Investigated 2026-05-25, **deferred from Plan I**: real lessons live in `longterm`/`episodic` DBs that are NOT in the WM snapshot and are never streamed to the TUI → needs a NEW lessons-streaming pipe, not the Plan I snapshot re-emit. Stays its own task here in J. Full root cause → [backlog.md](./backlog.md) #147.)*
- [ ] **#91** — rules loader fails on UTF-8 BOM → open with `utf-8-sig`.
- [ ] **#109** — `.cna` handshake retry should distinguish network vs response-shape failure.
- [ ] **#149** — reducer lands only `Host Name` from `systeminfo` (drops OS / domain / NIC IPs).
- [ ] **#152** — beacon `/v1/verdict/quick` sends empty `source_event_id` → Layer B retro can't correlate to Layer A. `.cna` should mint a ULID per gated command.

### Plan K · P3 · polish + tech-debt
- [ ] **#148** — Events panel renders raw JSON, overflows into the WM column.
- [ ] **#126** — `bid` vs `beacon_id` naming split across protocol schemas.
- [ ] **#113** — migrate `.cna` async paths to Java `HttpClient.sendAsync()`.
- [ ] **#114** — migrate `spike/` `.cna` scripts to the RedRobinHttp helper.
- [ ] **#100** — CS REST API as a systemd unit (Red Team Playground Ansible role).
- [ ] **#150** — `promoted_lessons` keyed `(command, verdict)` globally — no per-engagement attribution.
- [ ] **#151** — `.cna` sends `source.engagement_id: "unknown"` on path-2 events.
- [ ] **#154** — daemon-synced danger list at handshake (hardens Plan G's offline fail-closed).

**Sequence:** P1 (I) → P2 (J) → P3 (K). "Done" = production-grade + smoke green, not a date. *(P0 done: Plans G + H.)*

---

## Recently shipped
- **Plan I** (#139 + #140 + #143 + #146) ✅ 2026-05-25 — TUI live-state plumbing: complete `working_memory_snapshot` serializer (#139), Inspector CREDENTIALS/FINDINGS sections (#140), `.cna beacon.initial` + backfill + Sessions panel field-fix (#143), reducer re-emits snapshot on each mutation (#146). Merged to `dev-juan` via **PR #2** (`0ce6361`). Live smoke + direct `/v1/tui` WS probe verified the daemon side end-to-end (wm.json 148→149→150; live `opsec.verdict` + `working_memory_snapshot` delivered). Visible-refresh in the prompt_toolkit client → follow-on **#155**. [detail](./backlog.md)
- **Plan H** (#135 + #142) ✅ 2026-05-25 — `engagement new` writes loader-consumable YAML (unified the two `Engagement` schemas) + `--in-scope/--out-of-scope/--summary/--name` flags + empty-scope now loud (daemon WARN + banner + CLI notice), fail-closed. Merged to `dev-juan` via PR #1. [detail](./backlog.md)
- **Plan G** (#141 + #144) ✅ 2026-05-25 — gate fail-open fixed (root cause: Sleep `hasmatch` stateful iterator → stateless `ismatch`); Layer B retro carries the real command. [detail](./backlog.md)
- **Plans A–F** ✅ 2026-05-24 — defensive hardening, docs polish, Layer B trigger audit + prompt tuning, obsolete cleanup (#125/#127/#137/#131/#116/#120/#111/#130/#129/#110/#112).
- **#133 / #134 / #138** ✅ 2026-05-24 — bare-args guards, `$REDROBIN_HTTP` null fix, path-3 schema compliance.
- **#153** ✅ 2026-05-25 — working-tree cleanup + pre-#141 `?` data purge.

_Full root-cause records, commit SHAs, and lessons: [backlog.md](./backlog.md)._
