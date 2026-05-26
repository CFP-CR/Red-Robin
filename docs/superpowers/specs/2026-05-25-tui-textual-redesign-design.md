# TUI Textual Redesign — Design Spec

**Date:** 2026-05-25
**Branch:** `tui/textual-redesign` (off `dev-juan`)
**Status:** Design approved (brainstorming) — ready for `/writing-plans`
**Supersedes the prompt_toolkit TUI client** (`redrobin/tui/`). The daemon, bus, reducer, verdict pipeline, and the `.cna` are **untouched** — with one small, scoped exception: the `#156` engagement-load seed (see §2).

---

## 1. Context & motivation

Plan I shipped the live-state *plumbing* (the daemon emits complete `working_memory_snapshot` + `opsec.verdict` frames over `/v1/tui`, verified end-to-end via a WS probe). But the **prompt_toolkit client** is not production-grade UX:

1. **No semantic color / hierarchy** — BLOCK / WARN / PASS / ESCALATE render identically. For an OPSEC gate the verdict is the #1 signal and must be unmistakable.
2. **Events panel dumps raw JSON** (`json.dumps(payload)`) and overflows into other columns (#148).
3. **#155 — client doesn't reliably repaint** delivered frames (manual `invalidate()` path; intermittent).
4. **Inspector is an unpaginated dump** of 148 findings — no scroll/filter/grouping.
5. **Sessions panel cramped** — columns truncate, integrity wraps.
6. **No keybinding discoverability** — `Ctrl+I` exists but nothing surfaces it.
7. **Minimal status** — no daemon-connection state, no verdict counts.

### Decision: migrate the TUI client to **Textual**

Research-backed (see §9). The production-grade bar for TUIs (k9s, lazygit, btop, **Posting**, Harlequin, Toolong) is set by full UI frameworks, not building-block libraries. `prompt_toolkit` is a building-block library (it powers IPython/ptpython REPLs); the reference-class Python TUIs are all **Textual**. Market validation in-domain: **InfraGuard** (a red-team C2-redirection manager) is a Textual TUI with live API polling, a color-coded log, and a single aggregated dashboard — exactly this pattern. A TUI over SSH is also an opsec win for red-team tooling (no web surface, no X-forwarding).

Textual gives natively what we need: CSS-like centralized theming, `DataTable`/`RichLog` with native scroll, `Header`/`Footer`, a command palette, constraint-based responsive layout, and **flicker-free reactive rendering** — which **dissolves #155 by design** (no manual repaint).

The migration is **contained**: the layered architecture already separates the client from the daemon via the `/v1/tui` WS + snapshot frames. A Textual client consumes the same frames; only `redrobin/tui/` changes.

---

## 2. Scope

### In scope (build now)
- Rebuild `redrobin/tui/` on Textual: **Sections S1–S5** below (layout/panels, color system, inspector, navigation, data-flow/worker).
- **#155** dissolves as a consequence (reactive rendering) — and is an acceptance criterion.
- **#156** — seed `WorkingMemory` (`client`, `scope_in_scope`, `scope_out_of_scope`, `rules_of_engagement`, `objectives`) from `engagement.yaml` at engagement load (daemon-side; surfaced in the Working Memory panel + Inspector). This is the one daemon-side change in scope.
- **Extensibility seams (§4)** as first-class requirements so future features (the AI Advisor and others) drop in without redesign.

### Mapped but deferred
- **S6 — AI Advisor (UI/UX only).** The dock UI/UX is **mapped** (§3.6) and the layout **reserves its region + frame-routing seam**, but **no advisor logic is implemented** in this effort. The advisor agent (LLM), its triggers, mode toggle, and a notes store are a **future plan**. Open decisions recorded in §3.6 are deferred with that logic.

### Out of scope / untouched
- Daemon bus, reducer, verdict pipeline, `domain`/`infrastructure`/`api` (except the #156 seed), and the `.cna`. The `tui_ws` endpoint is unchanged (it already forwards all bus events).

### Suggested phasing (for `/writing-plans` to break out)
1. Scaffold the Textual `App` + layout shell + `.tcss` theme + WS worker + reactive store (the seams).
2. Panels: Sessions (DataTable), Verdicts/Events (RichLog), Working Memory — wired to reactive state. + #155 acceptance.
3. Color system + Header/Footer + status affordances.
4. Inspector `ModalScreen` (nav + filter + group + scroll).
5. Command palette + keybindings + help.
6. #156 (WM-seed) + entrypoint swap + test suite (Pilot + snapshots).

---

## 3. Design

> ASCII sketches below are the canonical record. (Higher-fidelity HTML mockups were produced during brainstorming under `.superpowers/brainstorm/` — gitignored, local-only.)

### 3.1 Layout & panels (S1)

```
┌ Red Robin · e2e-verify · ●daemon connected · ■3 BLOCK ■1 WARN ■12 PASS ──── Header (docked)
│ Sessions ▾      │ Verdicts / Events            │ Working Memory
│ 2047462324 High │ 14:51 WARN  net group "DA"   │ phase  recon
│ 1200206136 Med  │ 15:08 PASS  whoami           │ SCOPE (seeded #156)
│ 94581934   Med  │ 15:12 ESCAL seatbelt ⟳       │  in  EC2AMAZ-IJVFMFN
│ …(DataTable)    │ 15:12 →PASS seatbelt         │      10.100.0.0/24
│                 │ 15:22 BLOCK mimikatz …       │  out 10.99.0.0/16
│                 │   └ T1003.001 lsass touch    │ hosts 1 · creds 1 · find 148 ▸
├─────────────────┴──────────────────────────────┴──────────────────
│ «reserved #advisor-dock region — empty/hidden; S6 seam»
├────────────────────────────────────────────────────────────────────
│ ^I Inspector  ^P Palette  / Find  ⇥ Focus  s Sort  q Quit ──── Footer (docked)
```

- **Header** (docked top): engagement id, daemon-connection status dot, verdict counts (situational awareness; did not exist before).
- **Sessions**: `DataTable` — columns `BID / HOST / USER / INT`; sortable (`s`); focusable (accent border + cursor row); integrity color-coded (`High` orange).
- **Verdicts / Events**: `RichLog` — one line per event: `time · VERDICT(color) · command · short reason`. Layer B retroactive corrections rewrite the original line in place by `correlation_id` (rendered `→ PASS/BLOCK`), not duplicated. Newest at bottom, auto-scroll. **No raw JSON** (fixes #148).
- **Working Memory**: engagement scope (populated via #156), phase, counters (hosts/creds/findings); out-of-scope in red.
- **Footer** (docked bottom): most-used keybindings, always visible.
- **`#advisor-dock`**: a reserved layout region between the main row and the footer, empty/hidden — the S6 seam.
- **Responsive**: min 80×24; constraint-based widths; graceful collapse on narrow terminals (a resize hint below minimum).

Defaults are as shown (BID-first columns, newest-at-bottom auto-scroll, density as in the approved mockup); all are easily tunable in `.tcss`/widget config.

### 3.2 Color system (S2)

Semantic tokens defined once in a centralized `.tcss`; widgets reference tokens by **function, not appearance**.

| Token | Meaning | Color | 16-color |
|---|---|---|---|
| `$verdict-block` | dangerous, not executed | `#f85149` | red |
| `$verdict-warn` | proceed with judgment | `#e3b341` | yellow |
| `$verdict-pass` | approved | `#3fb950` | green |
| `$verdict-escalate` | Layer B reviewing | `#58a6ff` | blue |
| `$sev-high` | high integrity | `#f0883e` | — |
| `$scope-out` | out-of-scope | `#f85149` | red |
| `$status-ok / -warn / -err` | daemon connection | green / amber / red | — |
| `$text / $text-muted / $accent` | body / metadata / interactive | `#c9d1d9 / #8b949e / #58a6ff` | — |

Principles (from research): **16-color-safe** — every verdict is color **+ text label + weight**, so it survives non-truecolor terminals and color-blindness; **accent reserved** for focus/interaction only (never competes with verdicts); one theme file changes the whole look.

### 3.3 Inspector (S3) — `ModalScreen`

`^I` opens, `Esc` closes. Solves the 148-findings dump:
- **Left section nav** with counts (ENGAGEMENT / SCOPE / ROE / OBJECTIVES / HOSTS / ACCOUNTS / DEFENSES / TOOLS / CREDENTIALS / FINDINGS) — jump directly.
- **Filter/search** input — narrows the active section (e.g. `kerberos` → 6 of 148).
- **Group-by** `kind` (default) or `beacon`, with collapsible counts.
- **Native scroll** + scrollbar.
- **Copy row** (`y`) — copy a finding/cred for the report.

### 3.4 Navigation (S4)

- **Command Palette** (`^P`) — Textual-native, fuzzy, **provider-based** (each feature registers its commands). Delivers the k9s-style quick-switch *on top of* the always-visible dashboard.
- **Focus model** — `⇥/⇧⇥` cycle panels (active = accent border), `↑↓` within, `↵` act, `/` filter active panel.
- **Discoverability in layers** — Footer (frequent keys) → `?` (full keymap) → `^P` (search a command by name if you forget the key).

### 3.5 Data flow & migration (S5)

```
Cobalt Strike        Daemon — UNCHANGED (Plan A–I)                 Textual client (NEW)
 redrobin.cna  ──▶  /v1/events ─▶ bus ─▶ reducer ─▶ WorkingMemoryRepo    reactive state
 gate alias         verdict_pipeline ─▶ opsec.verdict ─▶ bus              ├ DataTable (sessions)
                    reducer re-emits working_memory_snapshot ─▶ bus       ├ RichLog (verdicts)
                    tui_ws ───────────────────────▶ WS /v1/tui ──▶        ├ Working Memory pane
                                                                          └ Inspector (ModalScreen)
   frames: working_memory_snapshot · opsec.verdict ──▶ set reactive ⇒ widget re-render
```

- **Reused** (logic/contracts): WS protocol + frame types; `working_memory_from_snapshot` adapter; renderer *logic* (fields, verdict-line format, inspector sections); i18n `Labels`; `OperatorDispatcher` (httpx command POST).
- **Rewritten** (`redrobin/tui/` only): `app.py` → Textual `App` + `compose()` + `.tcss`; panels → widgets; `inspector.py` → `ModalScreen`; `ws_client.run_client` → a Textual **worker** that updates reactive state (no `invalidate()`); keybindings → `BINDINGS` + palette providers.
- **Untouched**: daemon bus/reducer/pipeline, `domain`/`infrastructure`/`api`, the `.cna`, `tui_ws` — **except** the single `#156` change (seed `WorkingMemory` from the loaded `Engagement` at engagement-load time).
- **#155 dissolves**: prompt_toolkit needed `handle_message → invalidate() → repaint` (the fragile path). Textual: `self.sessions = …` (reactive) → compositor re-renders, flicker-free. No manual repaint = no repaint bug.
- **Migration is safe**: build the Textual client alongside the existing one; swap the `redrobin tui` entrypoint when ready. Zero risk to the production daemon during development.
- **Tests**: Textual `Pilot` (`App.run_test()`) to drive keys + assert widget state; `pytest-textual-snapshot` for SVG UI-regression snapshots; keep the existing WS-roundtrip integration tests (they exercise the unchanged daemon).

### 3.6 AI Advisor (S6) — **UI/UX MAPPED, LOGIC DEFERRED**

> Not implemented in this effort. Documented so S1–S5 reserve the seams.

**UI/UX (mapped):** a bottom **collapsible dock** (`^A`), below the three panes — the only addition to the S1 layout. Collapsed = top suggestion + hint; expanded = a recommendation card:

```
✦ Advisor · next steps                          ⟳ after BLOCK mimikatz · 2s ago   ^A collapse
  mimikatz was BLOCK (lsass / T1003.001). Credentials without the lsass signature:
   ▸ 1. nanodump BOF — no OpenProcess on lsass, evades the EDR signature   [accept ↵]
   ▸ 2. rubeus /tgtdeleg — Kerberos-only, no lsass touch                   [accept]
  memory: Defender real-time OFF on this host (engagement summary) → lower risk.
  scope: EC2AMAZ in-scope (already High). Avoid 10.99.0.0/16.
  confidence ●●●○ · sources: WM · verdict · engagement summary · 2 lessons · 1 note
```

**Intended behavior (future):** the advisor is a **new bus subscriber** (like the reducer) running an LLM **server-side** (API keys stay on the daemon; the TUI stays a thin client). On working-memory/verdict changes it reads `WorkingMemory` + lessons (episodic/longterm) + operator notes + engagement context, and emits an `advisor.recommendation` event → `tui_ws` (already forwards all events) → the dock. **Accepting a suggestion pre-fills the input but still passes the verdict gate** — the advisor suggests, the operator decides, the gate checks (human-in-the-loop; cannot bypass).

**Deferred decisions (with the logic):**
1. Trigger policy — auto on significant events (BLOCK / new High beacon / phase change) **+** on-demand `^A`, vs on-demand only (LLM cost/noise). *(leaning: auto-on-significant + `^A`)*
2. Mode toggle — `auto / on-demand / off`. *(leaning: yes, default auto)*
3. Notes store — no operator-notes store exists today; add later (key `n`, advisor reads them). *(leaning: ship advisor on WM+memories first; notes as a later iteration)*

---

## 4. Extensibility seams (first-class requirements)

The S1–S5 implementation MUST be modular/scalable enough that features like the Advisor drop in without redesign:

1. **Frame routing = a registry**, not hardcoded `if/elif`. A `{frame_type: handler}` table; adding `advisor.recommendation` (or any future type) = register a handler. No change to existing routing.
2. **Composable widgets + a reserved dock region.** Each panel is an independent `Widget` (own module, clear interface). The layout reserves `#advisor-dock` (empty/hidden). Adding the Advisor = mount a widget into that region; no restructuring.
3. **Reactive state store** widgets observe. Producers (the WS worker) update reactive attributes; widgets watch. A new widget reads existing reactive state (WM, events) + its own, without touching producers.
4. **Additive command/keybinding registration** via palette providers. A feature contributes its commands/keys from its own module; no central keymap edits.
5. **Daemon already extensible (the proof).** The bus accepts new subscribers (the advisor agent will be one); `tui_ws` already forwards all bus events (Plan I). A future `advisor.recommendation` reaches the TUI with **no transport change**.

**Acceptance for extensibility:** adding a new frame type *and* a panel that renders it requires **no edits to existing widgets or the routing table** (only registration + a new widget module).

---

## 5. Risks & mitigations

- **Rewrite scope/risk** → contained to `redrobin/tui/`; build alongside the current client; swap the entrypoint when ready; daemon untouched.
- **Textual on Windows Terminal** (operator's environment) → supported; verify truecolor + Unicode in the smoke; the 16-color-safe palette is the fallback.
- **Learning curve** → Textual is well-documented; Posting/Harlequin/Toolong as reference implementations.
- **Behavior parity** → the renderer *logic* is reused; Pilot + snapshot tests + the operator smoke guard against regressions.

---

## 6. Acceptance criteria

- Panels render live with real data; **updates appear without reconnect** (#155 gone) — verified by the operator smoke **and** Pilot tests.
- Verdicts are color-coded and 16-color-safe; no raw-JSON overflow.
- Inspector: scroll + filter + group + section nav over 148+ findings.
- Command palette + Footer + `?` help present; focus cycling works.
- Working Memory shows engagement scope/client/ROE (**#156**).
- **Extensibility acceptance** (§4) holds.
- Tests green: Pilot + `pytest-textual-snapshot` + existing WS-roundtrip.

---

## 7. References

- TUI best practices (color/layout/responsiveness): blog.tng.sh "Build Beautiful Terminal UIs in Python", jensroemer.com "TUI design".
- Reference-class TUIs & frameworks: rothgar/awesome-tuis; Real Python "Textual"; Textual layout guide (Grid for dashboards; "explicitly supports multiple visible panels").
- In-domain market: Bishop Fox "2025 Red Team Tools & C2 Frameworks"; **InfraGuard** (Textual red-team TUI — color-coded live log + single dashboard); Toad (Textual creator's terminal UI for agentic tooling).
