# Red Robin — Backlog (ARCHIVE + issue detail)

> **Live open work → [TODO.md](./TODO.md)** (lean roadmap, Plans H–K). This file is the **reference archive**: resolved-issue history (root causes + commit SHAs + lessons) and the deep detail behind each open issue. Long by design — scan TODO.md for *what's next*, read here for *why / how*. (Restructured 2026-05-25 per Claude Code best practices: lean live file + linked detail.)

Generated 2026-05-24 from in-session task tracker during the adapter event-stream + engagement-isolation work (commits `ec5dbe5` → `d949404`). Updated 2026-05-24 with current-sprint scope after critical-for-demo bugs cleared.

Each entry below includes the bug source (commit / smoke session), reproduction notes, and recommended fix scope.

**Goal reset (2026-05-24): production-grade, usable from day one — NOT demo-driven.** Open issues are prioritized **P0–P3 by product correctness/usability** (see "Production-grade roadmap" below), not by any conference date. The earlier "Critical for demo / fix before 2026-05-31" framing is superseded.

---

## Manual E2E verification — 2026-05-24 (live beacon, engagement `e2e-verify`)

Empirical verification of every component path against a live Cobalt Strike beacon on a standalone Windows lab host (`EC2AMAZ-IJVFMFN`, 10.100.0.240 / 10.100.0.0/24, **Defender OFF, no third-party EDR**). Engagement `e2e-verify` configured with correct loader schema + real scope + real defense-posture summary. Daemon HEAD `362ca35`. Goal: confirm what works, what's pending, file new issues. **Updated live as the session proceeds.**

### Component status

| Componente | Test | Estado | Evidencia |
|---|---|---|---|
| **#135** writer (`engagement new`) | YAML the CLI emits | ✅ RESOLVED (Plan H, `7f24e71`) | writer now emits the loader schema (top-level `slug`/`name`/`in_scope`/`out_of_scope`/`summary`); the two `Engagement` classes are unified onto one. Was: `id`/`client`/nested `scope:` → empty scope. |
| **#135** workaround (hand-edit YAML) | loader schema + restart | ✅ WORKS | banner `Engagement: E2E Verification Lab 2026-05-24`; scope loads (probes below) |
| ScopeEnforcer — in-scope CIDR | `jump psexec 10.100.0.240` | ✅ | not `roe` (scope passed) |
| ScopeEnforcer — out-of-scope CIDR | `jump psexec 10.99.0.5` | ✅ | `BLOCK` rule_id `roe` |
| ScopeEnforcer — strict-ROE (neither list) | `jump psexec 192.168.50.50` | ✅ | `BLOCK` rule_id `roe` |
| Layer A — exact command match | `command=jump`, `raw=jump psexec 10.100.0.240` | ✅ | `BLOCK psexec_lateral` (T1021.002) |
| Layer A — full-string vs bare-verb "anomaly" | curl shape | ✅ NOT A BUG | `rules.py:208` exact-matches `ctx.command`; earlier ESCALATE was a malformed curl (`command`=full string). `.cna` sends bare verb correctly (smoke #133/#129). |
| Layer B (real summary, async retro) | in-scope `jump psexec` escalated → Layer B WARN (id=4) | ✅ | ran async post-response; retro WARN; grounded reason |
| **B5** hallucination w/ real summary | same Layer B WARN reason | ✅ RESOLVED w/ real summary | reason cites *"even with Defender real-time protection off"* + *"Sysmon (status unknown)"* — NO invented EDRs (contrast tc4-smoke empty-summary → "CrowdStrike/Defender/SentinelOne confirmed"). Proves summary is load-bearing; B5 is downstream of #135. |
| **B4** Layer B retro loses command | episodic since `e68c01e` (#141) | ✅ RESOLVED | `_on_retroactive` now threads `command` into the `opsec.verdict` payload; episodic shows ZERO new `?` rows post-fix. Was: id=4 `command='?'` vs id=1 real command. |
| Console gate — PASS path | `shell whoami` via beacon | ✅ | executed → `ec2amaz-ijvfmfn\administrator`; silent pass-through; Layer A ESCALATE → Layer B PASS (no `[Red Robin]` line) |
| Console gate — BLOCK path | `mimikatz logonpasswords` (clean `.cna` load) | ✅ | `[Red Robin] DANGER` berror + command NOT executed (no dispatch). Layer A `mimikatz_logonpasswords` BLOCK. **Core value prop verified.** |
| Console gate — ESCALATE blog | `shell whoami` | ✅ | `[Red Robin] novel pattern — Layer B reviewing async` blog, then executes |
| Console gate — fail-OPEN on timeout (**#144**) | `mimikatz` ×18 re-smoke (`87f1b95`) | ✅ RESOLVED | **18/18 BLOCKED, zero toggle.** Root cause was Sleep `hasmatch` stateful iterator (NOT latency/keep-alive — daemon was always <77ms returning a correct BLOCK); fixed via stateless `ismatch`. Daemon-down → dangerous **fail-CLOSED** + benign **warn-proceed**. |
| Console gate — bare-args guard (#133) | bare `mimikatz` via beacon | ✅ | `[Red Robin] mimikatz requires arguments` + no execution + NO verdict POST (episodic count unchanged). |
| Reducer Path-2 — all 3 extraction paths | systeminfo (host) / klist (kerberos) / synthetic NTLM (cred) | ✅ | host=`EC2AMAZ-IJVFMFN` (name-only — OS/domain/IPs ignored); kerberos finding created (but #145 FP — fires on `Cached Tickets: (0)`); credential `CORP\alice`+hash extracted. Extraction machinery solid. |
| TUI Events panel | real beacon traffic | ✅ | command_input/output + Layer A verdicts stream live |
| TUI Memory Inspector (**B2**) | inspector overlay | 🐛 CONFIRMED LIVE | `Client: -`, `in_scope/out_of_scope: []` despite daemon holding real scope (curls prove it). `_build_initial_snapshot` omits the fields. |
| TUI Inspector findings/creds (**B3**) | inspector overlay | 🐛 CONFIRMED LIVE | no FINDINGS/CREDENTIALS section; 3 `opsec_block` findings in wm.json invisible. |
| TUI Sessions panel (**#143**) | active beacon | 🐛 NEW | "(no active sessions)" despite live beacon + traffic. `.cna` never emits `beacon.initial`. |
| TUI Working Memory live updates (**#146**) | findings 3→7 during session | 🐛 NEW CRITICAL | daemon sends `working_memory_snapshot` ONCE at connect (`tui_ws.py:34`), never re-pushed. Reducer mutations persist to wm.json but never reach TUI → WM panel + Inspector frozen. Compounds #139 + #140. |
| TUI Inspector "LESSONS" (**#147**) | clears each command | 🐛 NEW | `app.py:128-139` renders only `latest_verdict()` as a 1-item list — replaced each command, not accumulated; mislabeled (it's the last verdict, not episodic lessons). |
| Promoter (≥3 → promoted_lesson) | 3× mimikatz BLOCK in e2e-verify | ✅ | promoted (longterm `mimikatz/BLOCK count=3`); min=3 works. Keyed by `(command,verdict)` GLOBALLY (cross-eng aggregation, no per-eng attribution). **B4 blast radius** (✅ cleaned 2026-05-25): removed all `command='?'` rows -- 142 episodes across 4 episodic.dbs + 2 longterm `promoted_lessons` (144 total; the 13+15 estimate was low). |
| Summarizer (≥25 → context_summary) | deferred — proven historically | ⏳ (skip) | tc4-smoke has 44 context_summaries it generated; e2e-verify at ~14 episodes, would need ~11 more commands to trigger. Low value to re-verify. |

### #135 — empirical verdict: reclassify Round 6/v0.2 → **Critical for demo**

Confirmed twice (test-real-001, e2e-verify): `engagement new` writes a YAML the loader can't consume → daemon loads empty `in_scope` → post-Phase-2 strict ROE **BLOCKs every targeted command** with a generic `roe` reason, no warning anywhere. Any DEF CON attendee running `engagement new` + a `jump`/`psexec` hits this. Workaround (hand-edit YAML to loader schema) verified end-to-end. Fix scope = the #135 entry in the Deferred section **plus**: CLI flags (`--in-scope/--out-of-scope/--summary/--name`), a boot warning on empty `in_scope` (→ new **#142**), and one-shot migration of the 3 broken on-disk YAMLs (`tc4-smoke`, `spiedra-test`, `acme-q2-2026`).

### New issues to file (already confirmed today + DB inspection; live beacon will re-confirm + may add more)

- **#139** — ✅ **RESOLVED (Plan I, PR #2 `0ce6361`).** `working_memory_to_snapshot(wm) = asdict(wm)` (domain) now carries all 14 WM fields; both `tui_ws._build_initial_snapshot` and the reducer re-emit use it. Smoke: Inspector shows all engagement-state sections. _Was:_ TUI snapshot frame emits 5/14 fields (`tui_ws.py:_build_initial_snapshot`) → Memory Inspector shows scope/client/objectives/etc. as defaults regardless of daemon state. Independent of #135 (fixing #135 alone does NOT fix this — verified).
- **#140** — ✅ **RESOLVED (Plan I, PR #2 `0ce6361`).** `inspector.py` renders CREDENTIALS + FINDINGS sections. Smoke: Inspector shows `CREDENTIALS (1) CORP\alice` + `FINDINGS (148)`. _Was:_ `tui/inspector.py` render has no findings/credentials section → reducer-derived data (e.g. 7 `opsec_block` findings in test-real-001 wm.json) never visible in TUI.
- **#141** — ✅ **RESOLVED 2026-05-25 (commit `e68c01e`).** `_run_layer_b` now threads `command` into `_on_retroactive` → the `opsec.verdict` payload carries it → reducer no longer defaults to `?`. Verified: episodic shows ZERO new `?` rows since the fix (1 pre-fix artifact remains, inert). _Original:_ Layer B retroactive verdict omits `command` from the `opsec.verdict` payload (`verdict_pipeline.py:_on_retroactive`) → reducer defaults to `"?"` (`working_memory_reducer.py:166`) → episodes + findings + (via summarizer) L5 summaries record `command='?'`. ~1-line fix. Already flagged as a "pre-existing inconsistency" in test docstrings.
- **#142** — ✅ **RESOLVED (Plan H, `87af1f1`).** Daemon sets `app.state.scope_empty` + logs a WARNING at load when `in_scope` is empty; startup banner shows an EMPTY-scope line; `engagement new` prints a creation-time notice. Fail-closed preserved (empty = deny-all, not wildcard). _Was:_ no warning when an engagement loads with empty `in_scope`; operator only discovers it on the first generic `roe` BLOCK.
- **#143** — ✅ **RESOLVED (Plan I, PR #2 `0ce6361`).** `.cna` now has `on beacon_initial` + `beacons()` backfill on load (emits the 8-field `beacon.initial`); Sessions panel reads `beacon_id/hostname/user/integrity`. Smoke: 6 live beacons render (incl. `2047462324 EC2AMAZ-IJVFMFN Administrator [High]`). _Was:_ `.cna` never emits `beacon.initial` (verified: no `beacon_initial` handler / `beacons()` enumeration in `redrobin.cna`) → reducer `_on_beacon_initial` never fires → **TUI Sessions panel always empty** for real beacons + host/integrity never learned from beacon metadata (only fragile command-output regex). Worse for beacons established before `.cna` load. Confirmed live 2026-05-24: beacon `2047462324` active w/ command traffic, zero sessions. Fix: `on beacon_initial` handler + enumerate `beacons()` on `script_load` to backfill pre-existing beacons.
- **#144** — ✅ **RESOLVED 2026-05-25 (commit `87f1b95`).** ROOT CAUSE (final): **Sleep `hasmatch` is a STATEFUL iterator** — it caches its Matcher by string-value+pattern and toggles true/false across successive identical responses → BLOCK detection intermittently false → fail-open. NOT connection/timeout/JAR; those hypotheses (below) were WRONG. The first fix (retry + UNAVAILABLE) passed spec+quality code-review but FAILED the operator smoke; instrumenting the raw response (always `len=494`, byte-identical BLOCK) found the real cause. FIX: stateless `ismatch '.*PATTERN.*'` in `redrobin_query_quick` + handshake + error-class; reverted the misguided retry. Smoke: 18/18 mimikatz BLOCKED (zero toggle); daemon-down → dangerous **fail-CLOSED**, benign **warn-proceed**. _Historical investigation (parts superseded):_ **gate fails OPEN silently on verdict timeout** (CRITICAL). When the `/v1/verdict/quick` round-trip exceeds the `.cna`'s 200ms client cap (cold call / slow daemon / stale connection), `redrobin_query_quick` gets an empty response and falls through to PASS → `tier1a` dispatches, with NO operator-visible signal. Confirmed live 2026-05-24 as **INTERMITTENT**: identical `mimikatz logonpasswords` BLOCKED at 18:19 (✓) but EXECUTED at 18:14 AND 18:43 (DLL uploaded + ran) — daemon computed+persisted BLOCK each time (episodic ids 7, 14) while the `.cna` failed open; `systeminfo` 18:40 also fell open silently (id=12 ESCALATE, no blog). **Gate protection is ~50% reliable on the canonical dangerous command** — a demo-killer (typing `mimikatz` to show the block has a coin-flip chance of executing instead). **Measured 2026-05-24 (80 probes / 120s): daemon `/v1/verdict/quick` runs 22–77ms (p95=70), 0% over 200ms even under summarizer+promoter load — daemon latency is NOT the cause (original "bg tasks race the cap" hypothesis DISPROVEN).** Actual root cause is CLIENT-SIDE: `redrobin_query_quick` treats ANY POST failure (timeout / connect-error / non-2xx) as a silent PASS, and `RedRobinHttp` reuses a keep-alive `HttpClient` with a 200ms TOTAL request timeout. Strong hypothesis (fits the timing + operator "not connected" notes): a stale / idle-closed keep-alive connection (uvicorn drops idle conns ~5s; operators type sporadically) fails the first POST after a gap → silent fail-open — 18:19 mimikatz (1s after prior cmd, warm) BLOCKED ✓ vs 18:43 (24s gap, idle) executed ✗. Either way it's a PROTECTION failure, not a UX nicety. Fix: `redrobin_query_quick` must distinguish timeout/unreachable from PASS and fail-closed (or emit a loud per-command `berror`) for dangerous verbs. Relates to #130/#142.
- **#145** — reducer emitted a `kerberos_ticket_observed` finding on beacon `2047462324` with no obvious Kerberos output in the issued commands — possible `_KERBEROS_TICKET` regex false-positive. Needs the triggering command/output identified before confirming.
- **#146** — ✅ **RESOLVED daemon-side (Plan I, PR #2 `0ce6361`); CLIENT repaint split to #155.** Reducer re-emits a COMPLETE `working_memory_snapshot` after each mutating event (`working_memory_reducer.run` → `_publish_snapshot`); `tui_ws` forwards it. **Verified end-to-end via a direct `/v1/tui` WS probe** (`rr_probe.py`): the daemon pushes the connect snapshot then LIVE `opsec.verdict` BLOCK + `working_memory_snapshot` (findings 149→150) to any connected client; the operator smoke persisted wm.json 148→149. So pipeline→bus→reducer→re-emit→forward ALL work. **But the prompt_toolkit CLIENT does not always repaint the delivered frames** (intermittent — first frame repainted, later didn't, ~after the inspector overlay opened) → **split out as #155** (separate client-render bug, NOT in Plan I's changed files). _Was:_ TUI working memory never updates after connect (`tui_ws.py:32-41` sends `working_memory_snapshot` exactly once at connect, then only forwards raw bus events; nothing re-emits the snapshot). Reducer mutations persist to wm.json but never reach the TUI → WM panel + Inspector frozen at connect-time state. Compounds #139 + #140.
- **#147** — TUI Memory Inspector "LESSONS" is actually just the single latest verdict, not real lessons. **Root cause (investigated 2026-05-25; DEFERRED from Plan I — stays in Plan J):** `app.py:_render_inspector` (lines 128-140) takes `state.latest_verdict()` (the most-recent `opsec.verdict` from the events deque, `state.py:60-64`) and wraps it as a one-element `lessons` list; `inspector.py:99-103` renders it under the LESSONS header. So it is the *last verdict* reformatted — replaced every command, never accumulated, and mislabeled (it is NOT episodic/promoted lessons). **The REAL lessons — `PromotedLesson` in `longterm.db` + `Episode` in `episodic.db` (repos wired at `lifespan_helpers.py:170-174`) — are NEVER streamed to the TUI at all.** Why it's separate from Plan I: #146/#139/#140 all re-use the EXISTING working-memory snapshot pipeline (reducer writes WM → republish snapshot → panels). #147's data is not in that snapshot and lives in different repos, so fixing it needs a NEW data path: read `LongtermRepo`/`EpisodicRepo` → a new `lessons_snapshot` frame (or extend the WM snapshot) → `ws_client` route → inspector render — plus a design decision (all lessons vs recent-N; when to push). That new pipeline is the whole reason it's its own task, not part of Plan I's snapshot re-emit. Operator-observed: "se limpia cada vez que envío un comando."
- **#148** (cosmetic) — Events panel renders raw JSON envelopes and overflows horizontally into the Working Memory column (long `beacon.command_output` lines bleed across the panel boundary). Needs per-event formatting + width clip/wrap. Low functional impact, prominent in a demo. Operator-flagged.
- **#149** — reducer extracts hostname ONLY from rich output; `systeminfo` carries OS / domain / NIC IPs / build but only `Host Name` lands in the Host record (os/integrity/etc. stay None). Enhancement: parse OS + domain + NICs into Host attrs. P2.
- **#150** — `promoted_lessons` is keyed `(command, verdict)` GLOBALLY (no `engagement_id` column) → cross-engagement aggregation loses per-engagement attribution (a lesson from engagement A merges its count with B). Design characteristic; revisit if per-engagement long-term memory is wanted. P3.
- **#151** (P3) — `.cna` sends `source.engagement_id: "unknown"` on path-2 events. Harmless today (daemon routes by active engagement) but should carry the real engagement id for correct attribution / multi-engagement future.
- **#152** (P2) — beacon-originated `/v1/verdict/quick` has empty `source_event_id` → episode `correlation_id=''`. A Layer B retro WARN/BLOCK on a beacon command can't correlate back to its Layer A row (TUI `update_verdict`-by-correlation won't match; episode untieable to its verdict). curl probes set IDs so we missed it; real beacon escalations hit it. Fix: `.cna` generates a ULID `source_event_id` per gated command and threads it through.
- **#153** (P3, housekeeping) — ✅ **RESOLVED 2026-05-25**: 3 temp files removed; `TESTING.md` confirmed as a real 880-line testing guide and committed (`0797ebc`). _Was:_ stray untracked files in the working tree (`:TEMPinspect_*.txt`, `redrobin_postcommit.tmp.cna`) + `TESTING.md` to confirm.
- **#154** (P3) — daemon-synced danger classification at handshake: the daemon sends its DANGER-severity verb set to the `.cna`, which caches it for offline fail-closed decisions (#144). Eliminates drift between the `.cna`'s offline danger list (Plan G v1 = tier1a-derived) and the daemon's Layer A DANGER rules — single source of truth. Future hardening of the Plan G fix.
- **#155** (P1, NEW 2026-05-25) — **TUI client does not always repaint delivered frames.** Surfaced closing out Plan I #146. The daemon side is proven correct: a direct `/v1/tui` WS probe (`rr_probe.py`) received the connect snapshot then LIVE `opsec.verdict` BLOCK + `working_memory_snapshot` (findings 150); the operator smoke persisted wm.json 148→149. Yet the operator's running TUI stayed at `Events: 1` / `FINDINGS 148` on screen after a gate BLOCK. **INTERMITTENT:** a `beacon.command_input` DID tick Events 0→1 earlier in the same session; later frames (after the inspector overlay was opened) did not. Renderers are robust (`_format_event_line` uses `.get()`+defaults; inspector re-reads `state.snapshot` per paint via the `app.py:233` lambda; `handle_message` invalidates after every frame `ws_client.py:58`) → a delivered+applied frame WOULD repaint, so the frames aren't being applied/painted in the running app. NOT in Plan I's changed files (`memory.py`/reducer/`inspector.py`/`app.py`-sessions/`.cna`) → likely a pre-existing client-render/`invalidate` bug in `app.py:_amain` / `ws_client.run_client`. **Next (instrument, don't guess):** bisect with inspector CLOSED (does Events tick on a verdict?) + check TUI stderr for `ws_client reconnecting after error`; test whether the open inspector overlay suppresses repaint. First item of the TUI-hardening branch.
- **#156** (P1/P2, NEW 2026-05-25) — **WorkingMemory is never seeded from `engagement.yaml`.** Inspector shows `Client: X`, `SCOPE in_scope []` / `out_of_scope []`, `ROE (none defined)`, `OBJECTIVES (0)` — but `engagement.yaml` has `in_scope: [EC2AMAZ-IJVFMFN, 10.100.0.0/24, 100.103.208.204]`, `out_of_scope: [10.99.0.0/16]`, `name`, and ROE prose in `summary`. The ScopeEnforcer reads the yaml directly (out-of-scope `jump psexec 10.99.0.5` was correctly BLOCKED), but `working_memory.json` is a SEPARATE store never seeded from the engagement config at load / `engagement new`. Plan I correctly surfaces what's IN wm.json (empty). Fix: at engagement load, seed `WM.client/scope_in_scope/scope_out_of_scope/rules_of_engagement/objectives` from the loaded `Engagement`. `Client: X` = `engagement new` placeholder default (e2e-verify yaml has no `client:` key).

### Codebase-reality notes (verified this session — corrects stale assumptions, not bugs)

- Repos are **sync-sqlite3 only** (async SQLAlchemy path removed Slice 9), not dual-mode.
- `Lesson` + `Fact` dataclasses removed Slice 12; the `lessons` (episodic) + `facts` (longterm) tables are **dead schema** — all promotion flows through structured `PromotedLesson`.
- `longterm.db promoted_lessons` is **write-only in v0.1** — nothing in the verdict path or Layer B reads it back (Layer B only reads per-engagement `context_summaries` via `list_recent(eng_id, limit=1)`). The tc4-smoke "CrowdStrike/Defender/SentinelOne" hallucination is therefore contained per-engagement, NOT active cross-engagement poisoning.

---

## Production-grade roadmap — Plans G–K (2026-05-24)

> **Authoritative live roadmap now lives in [TODO.md](./TODO.md).** The table below is a historical snapshot (kept for the Plan G close-out record + acceptance criteria). When they diverge, TODO.md wins.

**Goal reset:** target is a **production-grade product, usable from day one**, not a demo. Every open issue is addressed to production quality (real-infra verified), prioritized by product correctness/usability — no conference-date gate.

**Workflow per plan (mandatory order):** `/writing-plans` → `/auditing-plans` → `/subagent-driven-development` (Opus 4.7). Atomic commits, no Claude co-author.

**Verification gate (non-negotiable):** operator-smoke against the LIVE beacon for any gate/`.cna`/TUI change. Unit tests + curls provably miss these — this session had 393 tests green while the gate failed open (#144) and the TUI was frozen (#146). The "Manual E2E verification" component table above IS the regression spec; each fix re-runs its row to green on a live beacon.

| Plan | Priority | Issues | Theme / acceptance |
|---|---|---|---|
| **G** ✅ **DONE 2026-05-25** | **P0 — verdict-path correctness & safety** | #144, #141 | ✅ Shipped (`e68c01e`, `87f1b95`). `mimikatz` ×18 → 100% block (was ~50%); episodes/findings carry the real command (zero new `?`). #144 root cause = Sleep `hasmatch` stateful iterator → stateless `ismatch`. Fail-closed on daemon-down for dangerous verbs. |
| **H** ✅ **DONE 2026-05-25** | **P0 — usability blocker** | #135, #142, +CLI flags | ✅ Shipped (PR #1 → `dev-juan`; `7f24e71`..`367744f`). Unified the two `Engagement` schemas, writer emits loader schema, `--in-scope/--out-of-scope/--summary/--name` flags, loud empty-scope (WARN+banner+notice), fail-closed. (YAML migration dropped — old test engagements deleted + `init` already loader-schema.) |
| **I** ✅ **DONE 2026-05-25** | **P1 — core operator UX** | #146, #139, #140, #143 | ✅ Shipped (PR #2 → `dev-juan`, `0ce6361`). Live-state plumbing verified daemon-side (live smoke + `/v1/tui` WS probe). Follow-on on the next TUI branch: client repaint **#155** + WM-seed **#156** + a UX best-practices pass. |
| **J** | **P2 — robustness** | #145, #147, #91, #109, #149, #152 | kerberos FP, lessons relabel, BOM-tolerant rules, handshake retry classification, host-rich extraction, beacon `source_event_id` correlation. |
| **K** | **P3 — polish + tech-debt** | #148, #126, #113, #114, #100, #150, #151, #153, #154 | events formatting, `bid`/`beacon_id` naming, fork→sendAsync, CS REST systemd, promoter per-engagement attribution, engagement_id on path-2 events, working-tree cleanup, daemon-synced danger list. |

**Sequence:** P0 (G + H — disjoint files, parallelizable) → P1 (I) → P2 (J) → P3 (K). "Done" = production-grade + live-beacon smoke green, not a date.

**Re-evaluated from old "Deferred" framing:** #91 (silent rule-load failure on BOM) + #109 (fragile reconnect) graduate to P2 — a day-one operator hits them. #126/#113/#114/#100 remain lowest (internal/infra debt). **#135 is NOT a v0.2 deferral — it's a P0 day-one usability blocker.**

---

## Current sprint scope (2026-05-24) — Rounds 1-4

Decided 2026-05-24 to clear the v0.1.x hardening + docs + Layer B + obsolete-cleanup backlog before pivoting to the real-time assistant work. Each Plan runs through `writing-plans` → `auditing-plans` → `subagent-driven-development` (Opus 4.7) workflow.

| Plan | Round | Items | Effort | Status |
|---|---|---|---|---|
| A | 1 — defensive hardening | `#125`, `#127`, `#137` | ~30 LOC, ~1.5h | **DONE 2026-05-24** (4 commits `7a75836`→`b8be0d8`) |
| B | 1 — operator helper | `#131` | ~10 LOC + decision | **DONE 2026-05-24** (commit `926e5da`) |
| C | 2 — docs polish | `#116`, `#120`, `#111` | docs + smoke desc | **DONE 2026-05-24** (commits `62b5cbc` + `3147629` + `58af456` + `9667668`) |
| D | 3 — Layer B trigger investigation | `#130` | read-only audit, ~30 min | **DONE 2026-05-24** (commit `83f696f`) |
| E | 3 — Layer B prompt tuning (CONDITIONAL on D) | `#129` | prompt engineering, ~1-2h | **DONE 2026-05-24** (commits `dee86f5` + `d0e532a` + `b1846e1`) |
| F | 4 — close obsoletes | `#110`, `#112` | verify + close, ~15 min | **DONE 2026-05-24** (commits `4a9d26b` + `82a2a12`) |

**Round 5-7 (refactors + v0.2 + infrastructure) deferred** to post-demo / separate sprint. See "Deferred — post-Round 4" section below.

---

## Resolved — formerly "critical for demo" (framing superseded 2026-05-24; all items below shipped)

These materially affect the live demo experience or shipping correctness.

### #133 — bare-args alias guards (RESOLVED 2026-05-24 commit `b3ed579`)
**Original symptom:** Operator types `mimikatz` (no subcommand). Layer A "novel pattern" + Layer B BLOCK verdicts surface but `&bmimikatz($1, "")` is rejected by CS Aggressor with `[!] Function call &bmimikatz failed: argument command is missing`. Beacon never executes. UX misleading (verdicts appear as if it dispatched), Layer A/B wasted on input that couldn't run anyway.
**Audit found same vulnerability in 18 other aliases** (tier1a_gate.sleep: kerberos_ticket_use, psexec, wmi, winrm, jump, spawn, bypassuac, elevate, inject, dllinject, psinject; tier1b_wrapper.sleep: shell, powershell, powerpick, execute-assembly, inline-execute, run, execute). dcsync already had its own guard; hashdump/logonpasswords/getsystem/screenshot/keylogger/screenwatch are zero-args. tier2 browserpivot/socks deferred (bare-call may be valid — proxy stop/list semantics).
**Resolution:** Added DRY helper `redrobin_alias_require_args($bid, $cmd, $args)` in tier1a_gate.sleep (loaded first by redrobin.cna so visible to tier1b). Uses Sleep's full-string-anchored `ismatch '\s*'` to detect empty/whitespace-only. On rejection: `berror($bid, "[Red Robin] " . $cmd . " requires arguments. Type 'help " . $cmd . "' in the beacon console for usage.")` + return false (caller skips both gate AND dispatch). Wrapped 19 alias bodies with the guard. dcsync's existing custom guard preserved. Cost: ~35 LOC total across 2 files.
**Smoke validation 2026-05-24 (operator-run, 7 cases):**
  - A `mimikatz` (bare): red guard message + cero POST /v1/verdict/quick ✓
  - B `shell` (bare): same ✓
  - C `spawn` (bare): same ✓
  - D `mimikatz logonpasswords`: guard pass → Layer A returned BLOCK by Sigma rule T1003.001 (rules engine working) ✓
  - E `shell whoami`: guard pass → Layer A novel pattern → Layer B WARN → dispatch → beacon output ✓
  - F `getsystem` (zero-args): guard NOT invoked (correct), Layer A/B flow normal ✓
  - G `hashdump` (zero-args): guard NOT invoked, Layer A → Layer B BLOCK normal ✓
**Bonus observations** (not bugs): Layer A Sigma rules block specific subcommands directly (e.g. `mimikatz logonpasswords` → T1003.001 BLOCK without Layer B), Layer B references engagement-summary context (`5+ shell whoami instances per episode summary`) and cross-command memory (`near-identical telemetry to already-blocked mimikatz`). v0.1 memory + summary loop functioning end-to-end.

### #134 — `$REDROBIN_HTTP null` at `redrobin.cna:83` (RESOLVED 2026-05-24 in commit `b7c7c69`)
**Original symptom:** `[!] Attempted to call a non-static method on a null reference at redrobin.cna:83` firing 2s after every beacon-output blog. Beacon output was completely suppressed because Sleep's filter semantics treat a raise inside `set BEACON_OUTPUT { ... }` as "filter aborted" → the body's `return $2;` never executes.
**Actual root cause (verified):** NOT the hook scope. The hooks themselves (`set BEACON_OUTPUT`, `on beacon_tasked`) run on the main script thread with full global visibility. The bug was an internal `fork({...}, \$body)` inside `redrobin_push_event_async` at lines 232-238 that captured only `$body`, not `$REDROBIN_HTTP`. Inside the fork's isolated ScriptInstance scope, `redrobin_http_post`'s `global('$REDROBIN_HTTP ...')` lookup returned null. Same root cause as Task #110 (heartbeat fork) + T11 (Layer B verdict fork).
**Fix (commit `b7c7c69`):** removed the internal `fork({...})` entirely; `redrobin_push_event_async` now POSTs synchronously. Cost: up to ~200ms blocking per beacon event (JAR enforces HTTP timeout). Acceptable for v0.1; filable as v0.2 if mass-enum workloads expose visible latency. The catch/swallow stays in place so daemon outages don't break beacon UX.
**Follow-on bug surfaced:** see #138 — the events DO now reach the daemon, but get 400 Bad Request because the wire envelope (id, ts) and event payloads (operator/command/task_id missing) don't match the v0.1 schemas. Path-3 telemetry is broken at the schema layer; demo unaffected because errors are swallowed at the HttpException catch.

---

### #138 — path-3 observation events fail envelope + payload schema (RESOLVED 2026-05-24 commits `006e83a`→`cf37d36`)
**Original symptom:** After #134 unblocked POSTs to `/v1/events`, every one returned `400 Bad Request`. Four schema mismatches verified by curl: envelope `id` was Java UUID (schema wants ULID), envelope `ts` was Java `Date.toString()` (schema wants RFC3339), `beacon.command_input` was missing `operator` + `command`, `beacon.command_output` was missing `task_id`.
**Resolution:** Five-commit subagent-driven sequence (`006e83a` ULID tests RED → `bc72cac` ULID impl GREEN → `5fce8f0` build scripts 1.0.2 → `0b85321` ship 1.0.2 JAR → `cf37d36` atomic .cna refactor). Added `RedRobinHttp.ulid()` instance method (Crockford b32, 48-bit ts + 80-bit randomness, JDK-only — `java.security.SecureRandom`). Replaced `dstamp(ticks())` with `[[java.time.Instant now] toString]`. Split monolithic `redrobin_push_event_async` into `redrobin_envelope` + `redrobin_emit_command_input` + `redrobin_emit_command_output` with schema-required fields encoded in each sub's signature. Switched `on beacon_tasked` → `on beacon_input` (gains `operator`=$2, `raw`=$3, command=first token of $3, signatures verified against vysecurity/AggressorScripts-1/All_In_One.cna). `set BEACON_OUTPUT` now surfaces `$4` (jobid from CS Aggressor — verified via `default.cna:807-810`) as `task_id`.
**Smoke validation 2026-05-24 (operator-run):** `shell whoami` on a live beacon → daemon log shows 4× `POST /v1/events HTTP/1.1" 200 OK`, zero 400s. `jcsp\jcspi` visible in beacon console. tc4-smoke episodic.db gains rows 42+43 for Layer A ESCALATE + Layer B WARN. Working memory reducer extracted 1 credential + 3 findings from path-3 events. Script Console clean (no NPE, no daemon-non-2xx warns).

---

## v0.1.x hardening — schedule for next milestone

Not blocking the demo, but visible operator pain points or known latent bugs.

### #125 — `RedRobinEventStream.close()` on script unload (RESOLVED 2026-05-24 commit `b8be0d8`)
Plan A T4 shipped. Added `on script_unload { if ($REDROBIN_EVENT_STREAM !is $null) { [$REDROBIN_EVENT_STREAM close]; } }` to redrobin.cna. Verified close() is idempotent (volatile shutdown flag handles repeated calls). Citation: Aggressor Script Manual (Fortra) + community precedent in harleyQu1nn/AggressorScripts.

### #127 — WS endpoints don't handle `bus.subscribe()` exceptions cleanly (RESOLVED 2026-05-24 commit `ae65a20`)
Plan A T3 shipped. Both `tui_ws.py` and `adapter_events_ws.py`: initialized `queue = None` before try, moved `bus.subscribe()` inside try, guarded `finally` with `if queue is not None`. Happy path tests (6/6 WS) still green; no regressions in 187-test integration suite.

### #129 — Layer B is over-cautious WARN for benign recon (RESOLVED 2026-05-24 commits `dee86f5` + `d0e532a` + `b1846e1`)
Plan E shipped. Rewrote `layer_b_engine.py` SYSTEM_PROMPT (lines 42-64) with a three-category taxonomy: **STANDARD_RECON → PASS** by default (explicit allow-list of ~30 read-only enumeration commands including `ipconfig`, `tasklist`, `whoami`, `hostname`, `systeminfo`, `nslookup`, `tracert`, `klist`, etc.); **RISKY_PAYLOAD → WARN** for AppLocker LOLBINs, AD recon, AMSI-visible credential cmdlets (with conditional escalation to BLOCK when KB/engagement indicate AMSI/EDR active); **UNAMBIGUOUSLY_DANGEROUS → BLOCK** including LSASS dump variants, `vssadmin delete shadows`, Defender tamper patterns (`Add-MpPreference -ExclusionPath`, `Stop-Service WinDefend`), `bcdedit testsigning`, `fsutil usn deletejournal`, remote service install. Explicit "shell vehicle is NOT a per-command signal" directive. "When uncertain prefer PASS" tiebreaker. Engagement-context override clause. T2 added `tests/unit/test_layer_b_engine_prompt.py` with 9 structural regression tests (all pass; 19/19 Layer B suite green; 258/258 unit suite). T3 added "Layer B prompt tuning smoke" section to `test_cna_smoke.md` with T3-S1..S5. Backlog's "cross-reference Layer A's rule_id" suggestion was OUT OF SCOPE — verified Plan D that rule_id is NOT forwarded to Layer B and Layer B only fires on ESCALATE (Layer A PASS never reaches Layer B). **Operator smoke 2026-05-24 confirmed**: `shell ipconfig` → Layer B PASS silent; `shell tasklist /v` → Layer B PASS silent; `shell whoami` → Layer B PASS silent (T14 regression cleared); `shell mimikatz logonpasswords` → Layer B BLOCK with detailed engagement-context reason (T1003.001 + multi-vendor EDR + LSASS access EID 10 — proves new prompt active); `shell net group "Domain Admins" /domain` → Layer A WARN (control case, Layer B silent). Auditor caught 4 MAJOR + 4 MINOR issues before execution (wmic dropped from STANDARD_RECON as deprecated LOLBIN, EDR-tamper patterns added to BLOCK, AMSI cmdlet conditional escalation, "outside engagement scope" reframed as detectable pattern).

### #130 — Layer B fires even when Layer A is silent (RESOLVED 2026-05-24 commit `83f696f`)
Plan D shipped the investigation report at `docs/investigations/2026-05-24-layer-b-triggers.md`. Both #130 hypotheses ruled out: (A) the preamble's conditional at `redrobin.cna:228` is action-equality only, never reason-text-gated; (B) `_run_layer_b` has exactly one caller (`verdict_pipeline.py:139`), gated on `verdict.action == ESCALATE` — Layer B does NOT fire on `/v1/events` ingestion, the reducer, or any other path. Most likely actual root cause: client-side 200ms HTTP timeout in `RedRobinHttp.java:30,37` while the daemon completed Layer A → ESCALATE and scheduled Layer B before the response returned. The retroactive WARN reached the beacon console ~10s later via the WS event stream (`redrobin.cna:475-486`), independent of the gate POST. Backlog entry mis-cited two facts (corrected in the report): preamble lives in `redrobin.cna:229` (not `tier1a_gate.sleep`), and the preamble conditional is action-equality only (not reason-text-gated). Recommended fix scoped for a future Plan G: ~5 LOC in `redrobin.cna` to surface gate degradation to the beacon console; not implemented here. Plan E (#129) scope confirmed unchanged — pure prompt tuning at `layer_b_engine.py:SYSTEM_PROMPT`.

### #131 — `_inspect_episodic.py` crashes on Windows cp1252 (RESOLVED 2026-05-24 commit `926e5da`)
Plan B T1 shipped. Created `tools/inspect_episodic.py` — UTF-8-safe in-repo replacement for the operator's local `_inspect_episodic.py`. Adds `sys.stdout.reconfigure(encoding="utf-8")` at start (fixes the cp1252 crash on Layer B Unicode reasons), CLI arg for DB path with `~/.redrobin/episodic.db` as the default, and an explicit missing-DB error instead of sqlite3 auto-creating an empty file. T2 updated `docs/operator-setup.md` to point at the in-repo helper.

### #137 — broaden `start_engagement` exception handling (RESOLVED 2026-05-24 commits `7a75836` + `c92710a`)
Plan A T1+T2 shipped (TDD red→green). `lifespan_helpers.py:start_engagement` now catches `yaml.YAMLError`, `OSError`, `UnicodeDecodeError` in addition to `FileNotFoundError`. Same fallback (engagement=None, wildcard scope) with a distinct log line that names the exception type for diagnosis. Regression coverage: `tests/integration/test_engagement_malformed_yaml.py` (2 cases — malformed YAML + bad-UTF-8 encoding).

### #111 — strengthen token-sanitization smoke + close defensive gap (RESOLVED 2026-05-24 commits `58af456` + `9667668`)
Plan C T3a + T3b shipped. Two-part fix. (1) Defensive (T3a, `58af456`): `redrobin_sanitize_error` in `adapters/cobaltstrike/redrobin.cna` gained a third redaction layer for `?token=<value>` / `&token=<value>` URL query params (`RedRobinEventStream` builds its WS URL as `ws://.../v1/adapters/events?token=<bearer>` per `RedRobinEventStream.java:51`; today the JAR doesn't log the URL so the leak is latent, but the regex closes the future-proofing gap). (2) Smoke (T3b, `9667668`): added new TC5 to the Layer B verdict visibility section of `adapters/cobaltstrike/tests/test_cna_smoke.md` with 5 explicit grep / Sleep probes covering Script Console, daemon log, sanitize_error sub direct-exercise, and a Bearer-header regression guard; same commit also fixes a dangling TC4 reference to the operator-local `_inspect_episodic.py` path (now points at the in-repo `tools/inspect_episodic.py` from Plan B #131). Probe 4 is the canonical operator-run validation since the .cna has no offline Sleep linter. Backlog title was misleading — original "TC5 token-sanitization" in tracker referred to a TC that was actually "Daemon offline (fail-open behavior)"; the real token-sanitization TCs live at §5 of 2026-05-14 smoke + §6 of 2026-05-17 smoke + the new TC5 of 2026-05-20 smoke shipped here.

### #116 — operator-setup.md: correct jdk.internal bug docs (RESOLVED 2026-05-24 commit `62b5cbc`)
Plan C T1 shipped. Original framing of #116 (stale `jdk.internal` guidance) had already been resolved by `e056c28` (JAR helper documented as zero-flag) and `289f98c` (1.0.0 → 1.0.1 JAR filename purge) during the 2026-05-17 → 2026-05-20 sprint, so the JVM-flags prose at lines 202-248 of `docs/operator-setup.md` is clean: "No JVM flags required" (line 208), JAR helper + URLClassLoader architecture described (lines 209-212), and a correctly-scoped Historical note at lines 244-248. However the JAR filename references in that same section were stale at `redrobin-aggressor-http-1.0.1.jar` (4 occurrences at lines 209, 221, 229, 234) — the 2026-05-23 `0b85321` ship-commit bumped the JAR to 1.0.2 for the path-3 ulid envelope (#138), but operator-setup.md was missed in the second bump. T1 fixes that: `replace_all` 1.0.1.jar → 1.0.2.jar across all 4 occurrences. New operators following the guide now find the artifact at its actual on-disk filename.

### #120 — helper-jar README + spec polish (RESOLVED 2026-05-24 commit `3147629`)
Plan C T2 shipped. README rewritten to match the 1.0.2 shipped JAR: kept the WHY-it-exists rationale focused on `jdk.internal.net.http.*` (the actual production reflection blocker) while pointing readers at spec §1.1 for the full audit of prior approaches that also failed access checks; added "Shipped classes (as of 1.0.2)" section documenting `RedRobinHttp` (with `ulid()` for #138), `RedRobinEventStream`, and `VerdictEvent`; added "Dependencies (vendored in `lib/`)" section calling out the org.json 20240303 runtime dependency (was missing); bumped the output filename from `1.0.0.jar` to `1.0.2.jar`; added `lib/json-20240303.jar` to the test-only run commands so `RedRobinEventStreamTest` compiles + runs. Spec at `docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md` got a §13 "Post-spec additions (2026-05-17 → 2026-05-24)" audit appendix listing the shipped delta (EventStream + VerdictEvent + org.json + ulid + version bumps) with commit SHAs — spec body left intact since it's a historical design doc.

### #110 — re-enable heartbeat fork via lambda() closure capture (RESOLVED 2026-05-24 commit `4a9d26b`)
Plan F T1 closed as SUPERSEDED. Empirical evidence from T14 smoke 2026-05-23 (documented in operator's vault memory `feedback_sleep_fork_async_pattern.md`): Sleep `fork(lambda(...))` does NOT propagate captured globals into the spawned ScriptInstance — the bound symbol is null inside the fork's scope. The proper pattern is `on heartbeat_5s` (main script thread, full global scope access), now established at `adapters/cobaltstrike/redrobin.cna:475-486` as the Layer B verdict drain. The disabled fork block at `redrobin.cna:434-458` retains an inline rationale for future operators. Heartbeat fork was disabled in commit `b7c7c69` (the `#134` fix — which references `#110` directly in its commit message: *"its resolution path is now empirically clear: same lesson — use heartbeat_Xs hooks, not fork(), for any Sleep code that needs access to globals or persistent Java references"*) and never needs to be re-enabled — its original purpose (daemon-liveness probe every 30s) is adequately covered by `redrobin_http_post`'s inline warn-on-failure error reporting (`redrobin.cna:88-91`).

### #112 — reconcile CS-version-to-Java-version claim across 3 sources (RESOLVED 2026-05-24 commit `82a2a12`)
Plan F T2 VERIFIED-CONSISTENT. Triangulation of the three named sources at HEAD `b1846e1`: (1) `docs/operator-setup.md:203-206` — Java 11+ floor; CS 4.12 bundles OpenJDK 17 per [Fortra Java dependency docs](https://www.cobaltstrike.com/help-java-dependency). (2) `adapters/cobaltstrike/helper-jar/README.md:40` — "Requires JDK 11+ on `PATH`" as a build prerequisite only; makes NO CS-version-to-Java-version claim. (3) `docs/superpowers/specs/2026-05-17-cna-jar-helper-design.md:568-569` (§7 Java compatibility) — Floor Java 11 (HttpClient API minimum); Tested Java 17 (CS 4.12 bundled JVM, per Fortra docs). All three agree: floor is Java 11, CS 4.12 ships OpenJDK 17. Same Fortra primary source cited by sources 1 and 3. The build pipeline (`adapters/cobaltstrike/helper-jar/build.ps1:30-33` + `build.sh:14-16`) uses `javac --release 11` confirming the Java 11 floor is also encoded in the actual JAR build. Backlog entry's "slightly different mappings" framing was either incorrect at the time it was filed (no specific conflict was identified in the original ticket beyond a generic concern) OR was resolved by the 2026-05-24 docs sprint that touched `operator-setup.md` (Plan C T1, JAR filename 1.0.1 → 1.0.2 bump) and `helper-jar/README.md` (Plan C T2, 1.0.2 shipped-classes rewrite) — both touched files were independently verified for the version mapping during the present (Plan F) pass. **No docs edits required.**

---

## Deferred — post-Round 4 work (Rounds 5-7, schedule after demo)

These items are real but were deliberately scoped OUT of the current sprint (2026-05-24) because they require their own focused session and don't block the DEF CON CFP demo. Each is sized at ~1-3 hours and benefits from a clean workspace.

### Round 5 — v0.1.x refactors (~2-3h)

#### #91 — v0.1.x: BOM-tolerant rules loader
**Source:** Pre-existing (filed before current session).
**Symptom:** YAML rules files with a UTF-8 BOM (`﻿` at file start) fail to parse cleanly. Operators editing rules in Notepad or VSCode may unknowingly add a BOM.
**Files:** `redrobin/application/rules_loader.py` (or whichever module loads YAML rules — verify before fix).
**Fix:** Strip BOM at read time, OR open with `encoding="utf-8-sig"` which auto-strips. Test against a BOM'd fixture.

#### #109 — v0.1.x followup: handshake retry — distinguish network vs response-shape failure
**Source:** Pre-existing.
**Symptom:** `.cna`'s `redrobin_handshake_with_retry` treats all failure modes equally (network refused, daemon returned 4xx, bad JSON shape). Retry behavior should differentiate: retry on network errors, fail-fast on response-shape errors.
**Files:** `adapters/cobaltstrike/redrobin.cna` (handshake retry block).
**Fix:** Categorize the catch errors by class (ConnectException / HttpException / parse error), retry only network-class, log and stop on response-shape errors.

#### #114 — v0.1.x followup: migrate `adapters/cobaltstrike/spike/` scripts to HttpClient
**Source:** Pre-existing.
**Symptom:** Spike (experimental) `.cna` scripts in `adapters/cobaltstrike/spike/` still use the legacy direct `import java.net.*` + URLConnection pattern. Production code migrated to RedRobinHttp helper JAR in Task #115; spikes never followed.
**Files:** `adapters/cobaltstrike/spike/*.cna`.
**Fix:** Refactor each spike to use `[$REDROBIN_HTTP post: ...]` pattern. Low priority — spikes are experimental and don't ship.

### Round 6 — v0.2 architectural (~4-6h)

#### #126 — `beacon_id` vs `bid` naming inconsistency across protocol schemas
**Source:** T4 code review 2026-05-20
**Symptom:** `opsec_verdict.schema.json` uses `beacon_id` (Daemon code path emits this). Sibling event schemas (`beacon_command_output`, `beacon_command_input`, `beacon_initial`, `credential_observed`, `host_observation`) all use `bid` (3 letters). Protocol-wide naming is split across event types.
**Files:** `protocol/schemas/v0.1/events/*.schema.json` + Daemon emitters.
**Fix:** Pick one canonical name (probably `bid` for terseness, but `beacon_id` is more self-documenting). Migrate the other side. Touches schema files + Daemon publishers + JAR parser (`VerdictEvent.beaconId`).

#### #135 — two `Engagement` classes with disjoint YAML schemas
**Source:** B2 code review 2026-05-24
**Symptom:** Two distinct classes both named `Engagement` coexist:
  1. `redrobin.domain.engagement.Engagement` — used by `EngagementService` (CLI). Fields: `.id`, `.client`. Writes YAML with `id`, `client`, `scope.in_scope`, `objectives`.
  2. `redrobin.application.engagement_loader.Engagement` — used by daemon lifespan. Fields: `.slug`, `.name`, `.in_scope`, `.out_of_scope`, `.summary`. Reads YAML with `slug`, `name`, top-level `in_scope`.
Result: `redrobin engagement new X` writes a YAML the loader can't fully consume — daemon loads the engagement with empty `in_scope`/`out_of_scope`/`summary` regardless of what the operator put in the file.
**Files:** Both `Engagement` classes + `EngagementLoader` + `EngagementService.new()` template.
**Fix:** Unify on one `Engagement` class with one YAML schema. Either migrate the writer (`EngagementService.new()` template) or the loader. Touch test fixtures that assume the current shape. Includes a `redrobin migrate engagement-yaml` step if pre-2026-05-24 YAML files need to be normalized.

#### #113 — v0.2 followup: migrate `fork{}` async paths to `HttpClient.sendAsync()`
**Source:** Pre-existing.
**Symptom:** Several `.cna` paths still use Sleep `fork({...})` for async HTTP (now confirmed broken due to scope isolation, #134 + #110). The proper v0.2 migration is to push async to the Java side via `HttpClient.sendAsync()` rather than juggling Sleep forks.
**Files:** `adapters/cobaltstrike/helper-jar/src/com/redrobin/aggressor/RedRobinHttp.java` (add `postAsync` returning CompletableFuture); affected `.cna` paths.
**Fix:** Bigger architectural change. Move all async dispatch from Sleep fork() to Java ExecutorService-backed `HttpClient.sendAsync()`. Sleep callers become synchronous (or use Aggressor's `on heartbeat_Xs` pattern for polling, as already established in #134 fix).

### Round 7 — infrastructure (~1h)

#### #100 — CS REST API bring-up in Ansible role
**Source:** Pre-existing.
**Symptom:** `csrestapi` dies on droplet reboot (verified empirically multiple times during smoke 2026-05-22+). Currently manually respawned via SSH; should be systemd-unit-managed and provisioned by the Red Team Playground Ansible role.
**Files:** `red-team-playground/ansible/roles/cobaltstrike-teamserver/` (verify path — outside this repo).
**Fix:** Systemd unit file template + role task that installs+enables it. Tested via `terraform destroy && terraform apply` cycle.

---

---

## Status of related closed tasks (for reference)

- **#123** — v0.1.x BLOCKER: wire .cna to receive opsec.verdict (Layer B) events. **CLOSED** 2026-05-24 — keystone TC1+TC2+TC3+TC4 all pass.
- **#128** — T14 operator-run smoke gate. **CLOSED** 2026-05-24 — full smoke validation passed.
- **#132** — reducer no-ops when no engagement set. **CLOSED** by B2 (`8e2d280`).
- **#133** — bare-args alias guards. **CLOSED** 2026-05-24 (`b3ed579`) — 7-case smoke verified.
- **#134** — `$REDROBIN_HTTP null` in hooks. **CLOSED** 2026-05-24 (`b7c7c69`) — fork removed.
- **#136** — clarify single-root episodic.db architecture. **CLOSED** — superseded by B2 implementation of per-engagement persistence + B3 docs.
- **#138** — path-3 schema compliance. **CLOSED** 2026-05-24 (`006e83a`→`cf37d36`) — 5-commit sequence shipped ULID + RFC3339 + emit-sub refactor.

---

## How to read this file

Each entry has:
- A `#NNN` task ID from the in-session tracker (may not match GitHub issues if filed separately).
- **Source**: the commit, code review, or smoke session that surfaced the bug.
- **Symptom**: what an operator or test sees.
- **Files**: pointers into the codebase.
- **Fix**: recommended scope, ranked toward cheapest viable path.

When picking the next slice: do **Critical for demo** first, then v0.1.x hardening, then v0.2 cleanup. Each item is sized to be a single atomic commit unless explicitly marked otherwise.
