# Red Robin — DEF CON Demo Runbook

This runbook is the operator-facing checklist + script for the Red Robin live
demo at DEF CON RTV / La Villa @ DC34. It follows the storyboard in
`Projects/red-robin-architecture-design.md` §4 Componente 6 (Demo Environment).

**Target deadlines:**
- Red Team Village CFP: 2026-05-31
- La Villa @ DC34: 2026-06-01

## Pre-talk checklist (T-24h before)

- [ ] **Laptop bring-up**
  - [ ] Stable Python 3.11+
  - [ ] `uv sync --all-extras` succeeds
  - [ ] `ANTHROPIC_API_KEY` exported (export in shell rc, NOT in repo)
  - [ ] `VOYAGE_API_KEY` exported
  - [ ] `redrobin init && redrobin doctor` — all checks green
- [ ] **Demo lab**
  - [ ] Red Team Playground (GOAD-Light) deployable in <10 min via Terraform
  - [ ] CS Client + teamserver running on a hardened cloud instance (DO/AWS)
  - [ ] At least one beacon checking in from a GOAD workstation
  - [ ] `adapters/cobaltstrike/redrobin.cna` loaded in CS Client, handshake verified
- [ ] **Engagement fixtures**
  - [ ] `redrobin engagement new acme-q2-2026 --client "Acme Corp"`
  - [ ] Edit `~/.redrobin/engagements/acme-q2-2026/engagement.yaml`:
    - Set scope.in_scope to GOAD subnet
    - Set scope.out_of_scope to a deliberate "trip wire" subnet (proves ROE Stage 1)
    - Set 3 objectives (Compromise DC / Access fileshare / Persistence)
    - Set rules_of_engagement (no destructive, no DoS, working hours)
  - [ ] `redrobin engagement use acme-q2-2026`
- [ ] **Mock backup** (in case live lab fails on stage)
  - [ ] `uv run python adapters/sliver-mock/adapter.py --scenario acme_corp --daemon http://127.0.0.1:7777`
  - [ ] Verify TUI shows the scenario play out
- [ ] **Backup laptop** pre-configured + personal hotspot as third fallback
- [ ] **TUI** verified: `redrobin tui` opens 3-panel layout

## Talk scenes (per spec §4 Componente 6 storyboard)

### Scene 1 — Sliver-Mock Apertura (2-3 min)
```bash
# Linux scenario demo (cross-C2 proof — protocol agnostic)
uv run python adapters/sliver-mock/adapter.py --scenario linux_demo --daemon http://127.0.0.1:7777 --speed 2.0
```
Talk cue: *"Same daemon, same TUI, different C2. Protocol agnostic — demonstrated live."*

### Scene 2 — Switch to CS / Idle (30s)
- Focus CS Client window
- TUI shows Working Memory empty (engagement reset)
- Engagement Progress block shows: phase=not_started, 3 objectives at 0%

### Scene 3 — First beacon (1-2 min)
- Beacon checks in from DC01 (GOAD-Light)
- Type `getuid` in CS console
- Output: `NT AUTHORITY\SYSTEM (admin)`
- Talk cue: *"Beacon checks in. Red Robin auto-detected initial_access milestone."*

### Scene 4 — INFO event / light recon (1-2 min)
- Type `whoami /priv`
- INFO annotation in CS console + TUI event stream
- Working memory updates user + privileges

### Scene 5 — WARN event / AD recon (2-3 min)
- Type `net group "Domain Admins" /domain`
- WARN inline: rule `domain_admin_enumeration` fires
- Operator sees alternatives in TUI chat panel via `:rr alts`
- Operator picks BOF alternative
- Talk cue: *"WARN — soft confirmation. Red Robin offered three alternatives, ranked by stealth. Operator chose BOF path."*

### Scene 6 — EDR observation (30s)
- Type `tasklist /v`
- Output reveals `CSAgent.exe` (CrowdStrike Falcon)
- Working memory updates `defenses_observed`
- Talk cue: *"Working Memory now knows DC01 has CrowdStrike. Future verdicts contextualize accordingly."*

### Scene 7 — DANGER event / vssadmin BLOCKED (3-4 min) ⭐ CENTERPIECE
- Type `vssadmin list shadows`
- Alias `shell` fires → `redrobin_gate()` returns BLOCK
- `berror` shows: `[!] [Red Robin] DANGER: VSS enumeration matches ransomware precursor TTP. T1490 + EID 8222 high-signal. Override: ':rr override <reason>'`
- Command NEVER reaches teamserver (verify via task list)
- TUI shows full verdict + episodic context
- Talk cue: ⭐ **"This is what we mean by 'augmented operator.' Red Robin caught a command that would have burned the engagement in under 5 minutes."**

### Scene 8 — Operator chats (2-3 min)
- `:rr explain` → why is this DANGER?
- `:rr alts` → operator types in TUI chat:
  *"tengo SYSTEM en DC01 con CrowdStrike. quiero dump creds sin tocar lsass. opciones?"*
- Layer B returns ranked alternatives with engagement-aware reasoning
- Talk cue: *"Notice the response goes beyond TTPs — it reasons against engagement objectives. obj_1 60%, obj_3 0% → strategic recommendation."*

### Scene 9 — Cred extraction / clean execution (1-2 min)
- Run `execute-assembly nanodump.exe --dump-creds`
- Output success — Working memory updates `tools_used: nanodump (no detection)`
- Auto-detected milestone: credential_access advanced
- Talk cue: *"Red Robin auto-detects milestones. kill_chain progress reflects in TUI."*

### Scene 10 — Memory inspector deep-dive (1-2 min)
- `Ctrl+I` → expanded view shows:
  - 3 hosts, 2 accounts (3 captured), defenses observed, tools used
  - Lessons (auto + tagged), kill chain
- Talk cue: *"All this state lives locally. Operator can audit, export, or share with team."*

### Scene 11 — Wrap (1 min)
- `:rr promote "nanodump invisible to CrowdStrike on Win 2022"`
- Episodic → longterm
- `redrobin engagement close acme-q2-2026` → 12 lessons summary
- Talk cue: *"What was learned today is available for future engagements. This is the augmented operator over time."*

## Total runtime
~18-22 minutes on stage.

## Recovery contingencies

| Symptom | Recovery |
|---|---|
| DEF CON wifi fails | Switch to personal hotspot. Sliver-Mock replay works offline (no Anthropic). |
| Beacon never checks in | Skip Scene 3-7; pivot to Sliver-Mock acme_corp scenario |
| Anthropic rate limit | Pre-recorded responses for Scene 8 — JSON file in `demo-prep/` |
| TUI render glitches | Resize terminal to 132×40 minimum; switch to single-panel mode |

## Post-talk

- [ ] Save engagement: `redrobin engagement archive acme-q2-2026`
- [ ] Promote 5-10 lessons to longterm
- [ ] Capture audience Q&A → episodic memory for v0.2 priorities
