# Red Robin — TODO

Live tracker of **open work**. Resolved history + deep per-issue detail live in [backlog.md](./backlog.md); full specs for in-flight work live in [plans/](./plans/). Keep this file lean — when an item ships, move its detail to backlog.md and leave a one-line entry under "Recently shipped".

**Workflow (every plan):** real root cause → instrument, don't guess → production-grade fix (scalable + maintainable) → `/writing-plans` → `/auditing-plans` → `/subagent-driven-development` (Opus 4.7) → operator smoke.
**Verification gate:** live-beacon smoke for any gate / `.cna` / TUI change; unit tests + local daemon for Python. Atomic commits, no AI co-author.

---

## Now / Next
**Plan I (P1)** — TUI live state (#146 / #139 / #140 / #143). *(Not started — branch off `dev-juan`; needs a live beacon for the smoke.)*

---

## Open — Plans I–K

### Plan I · P1 · core operator UX — TUI live state
> Today 2/3 TUI panels are dead. Detail: [backlog.md](./backlog.md) (#139/#140/#143/#146).
- [ ] **#146** — working memory never updates after connect (snapshot sent once). Likely unified fix also closes #139.
- [ ] **#139** — snapshot frame emits 5/14 fields → Memory Inspector shows defaults.
- [ ] **#140** — Inspector has no findings/credentials section.
- [ ] **#143** — `.cna` never emits `beacon.initial` → Sessions panel always empty.
- **Accept:** WM panel + Inspector update live; Sessions populates; findings/creds visible. *(Live-beacon smoke.)*

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
- **Plan H** (#135 + #142) ✅ 2026-05-25 — `engagement new` writes loader-consumable YAML (unified the two `Engagement` schemas) + `--in-scope/--out-of-scope/--summary/--name` flags + empty-scope now loud (daemon WARN + banner + CLI notice), fail-closed. Merged to `dev-juan` via PR #1. [detail](./backlog.md)
- **Plan G** (#141 + #144) ✅ 2026-05-25 — gate fail-open fixed (root cause: Sleep `hasmatch` stateful iterator → stateless `ismatch`); Layer B retro carries the real command. [detail](./backlog.md)
- **Plans A–F** ✅ 2026-05-24 — defensive hardening, docs polish, Layer B trigger audit + prompt tuning, obsolete cleanup (#125/#127/#137/#131/#116/#120/#111/#130/#129/#110/#112).
- **#133 / #134 / #138** ✅ 2026-05-24 — bare-args guards, `$REDROBIN_HTTP` null fix, path-3 schema compliance.
- **#153** ✅ 2026-05-25 — working-tree cleanup + pre-#141 `?` data purge.

_Full root-cause records, commit SHAs, and lessons: [backlog.md](./backlog.md)._
