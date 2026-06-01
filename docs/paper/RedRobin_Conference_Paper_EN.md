# Red Robin: Don't Hack For Me, Hack With Me
## A Red Teamer's AI Companion for Real-Time Operational Guidance

**Proposal submitted to:** DEF CON 34 Red Team Village (2026-05-31 deadline)
**Track:** Red Team & Offensive Security
**Format:** Presentation + Live Demo (45 min)
**Status:** v0.1 implementation complete; demo lab provisioned

---

## Abstract (200-300 words)

The 2025–2026 wave of "AI hacking tools" — Villager, HexStrike, PentAGI, public
Cobalt-Strike-MCP servers wired into frontier LLMs — has settled the autopilot
question for serious red teamers: it fails. The b-ops.be public demonstration
captured it precisely: hand an LLM a fresh Beacon and a CS-MCP server, and the
first thing it proposes is `mimikatz logonpasswords` — a behaviour that would
end most real engagements in under a minute. The tools have no concept of
detection priors, no concept of engagement scope, no concept of the shell the
operator just spent a week earning.

Red Robin proposes the opposite model: AI as a **coach the operator can refuse**,
not an agent that types for them. It uses Cobalt Strike's Aggressor `alias`
mechanism — the same production-proven primitive SpecterOps' OPSEC Profiles have
relied on since 2018 — to intercept dangerous Beacon commands at the operator's
CS Client *before* they reach the teamserver. A two-tier verdict protocol returns
deterministic Layer A YAML-rule decisions in under 150ms (production-grade
synchronous gating), while a Layer B LLM judge with RAG-retrieved knowledge-base
context runs asynchronously for novel patterns, posting retroactive warnings
to the operator's TUI. Five layers of memory (in-engagement, cross-engagement,
operator-personal, KB, and Tradecraft Graph) make the verdicts engagement-aware
rather than generic.

The presentation will demonstrate Red Robin live against a Cobalt Strike +
GOAD-Light deployment, including the DANGER block that catches `vssadmin delete
shadows` in flight (Scene 7 of our demo runbook) and the engagement-aware
operator chat (Scene 8). We will be honest about what Red Robin *does not* do —
including the per-operator gate scope (T11) that surfaces from Cobalt Strike's
client-side scripting model — and what v0.2 will address.

## Description (600-800 words)

### Why current AI hacking tools fail in real engagements

The "AI for offensive security" market in 2025-2026 split into two camps:
**autopilots** that try to drive an engagement (Villager, HexStrike, PentAGI,
exploratory CS-MCP-server-plus-Claude demos) and **assistants** wired into
IDEs or chat windows (Burp Copilot, Caido AI). Both miss what senior operators
actually want from an AI co-pilot in a real engagement.

The b-ops.be researcher demonstrated the autopilot failure mode publicly in
2025: a Cobalt Strike MCP server is exposed to Claude Sonnet 4.5, post-recon
the LLM is asked "what's the best next step?" — and it confidently recommends
`mimikatz logonpasswords`, complete with the full Aggressor command syntax.
The model has no awareness that an EDR will catch it, no awareness of whether
the operator already pivoted, no awareness that LSASS is in PPL on modern
Windows. The autopilots are reading the room like an intern with a Hack The
Box subscription.

Meanwhile the IDE-bound assistants are useless on a live engagement because
they have no access to the engagement state — no Beacon output, no operator
notes, no knowledge of what the team is trying to achieve this week.

### Red Robin's architecture (4-stage verdict pipeline + 5-layer memory + adapter pattern)

Red Robin sits between the operator and the C2. It is **not** a replacement
for the operator's brain — it is an extra brain that catches the high-signal
mistakes and answers the high-signal questions, while leaving the routine work
untouched.

The architecture is built around four pillars:

1. **Adapter pattern.** A C2-agnostic wire protocol (v0.1) lets us drop in
   support for any C2 framework. v0.1 ships with the Cobalt Strike adapter
   (Aggressor + Python REST bridge) and a Sliver-Mock adapter for offline demo
   work. The Sliver-Real and Mythic adapters are on the v0.1.1 / v0.2 roadmap.

2. **Two-tier verdict protocol.** Layer A is a deterministic YAML rule engine
   that returns ALLOW / WARN / BLOCK / NEEDS_REVIEW verdicts within 150ms p95
   — fast enough to gate the operator's command synchronously. Layer B is an
   asynchronous LLM judge backed by a Voyage-embedding RAG over the Red Robin
   knowledge base (Sigma rules, ATT&CK detection priors, SpecterOps OPSEC
   profiles, operator tradecraft notes). Layer B posts retroactive WARN
   verdicts to the TUI for novel patterns Layer A didn't catch.

3. **5-layer memory.** In-engagement state (current Beacons, recent commands,
   pivots), cross-engagement learning (what worked / failed on similar targets),
   operator-personal preferences (your favoured tradecraft), the knowledge base,
   and the Tradecraft Graph (a normalised graph of techniques and their
   detection profiles). The TUI surfaces relevant memory at the moment of
   decision.

4. **Aggressor alias gate.** The technical centrepiece. SpecterOps' OPSEC
   Profiles, in production since 2018, demonstrated that Aggressor's
   `alias` mechanism can transparently intercept Beacon commands at the
   operator's CS Client. Red Robin uses the same primitive — 28 alias hooks
   in `redrobin.cna` covering the most-abused commands (mimikatz, logonpasswords,
   sekurlsa, vssadmin, shadow copies, NTDS extraction, Kerberos roasts, lateral
   movement, etc.). When the operator types `mimikatz logonpasswords` in the
   Beacon console, the alias fires the daemon, the daemon returns a verdict in
   <150ms, and the command either dispatches (with annotation) or is blocked
   before it ever reaches the teamserver.

### Per-operator scope honest acknowledgement (T11)

We will not oversell this. The alias gate is scoped to operators who load the
`redrobin.cna` script. An operator on the same teamserver who has not loaded
the script bypasses the gate. **This is an architectural property of Cobalt
Strike's client-side scripting model, not a Red Robin design choice — and we
document it openly.** Mythic's native `opsec_pre` hook is server-side and avoids
this entirely, which is why we are building the Mythic adapter for v0.2. Team
SOP recommends all operators on an engagement load the script; the daemon also
publishes a per-operator gate-presence event to the TUI so the team lead can
see who is and is not gated.

### Live demo overview (11 scenes per demo runbook)

The talk demos against a live Cobalt Strike teamserver + Beacon + GOAD-Light AD
lab, in 11 scenes mapped to `docs/demo-runbook.md`. Highlights:

- Scene 4: warm-path discovery (allowed, annotated).
- Scene 7: **DANGER block** — operator attempts `vssadmin delete shadows /all`,
  Layer A catches it, BLOCK verdict, command never reaches teamserver.
- Scene 8: **engagement-aware operator chat** — operator asks "what's the
  cleanest path from here?" and the LLM judge replies grounded in the current
  Beacon state, the engagement's documented goals, and the Tradecraft Graph's
  detection priors.

### v0.1 vs. v0.2 roadmap

v0.1 (presented at DEF CON 34) ships: CS adapter, Sliver-Mock adapter, daemon,
TUI, 28 alias hooks, Layer A rule engine, Layer B LLM judge, 5-layer memory,
KB-RAG retrieval, end-to-end demo. v0.1.1 (June 22 target): Sliver-Real adapter.
v0.2 (late 2026): local-mode (no API egress), Mythic native `opsec_pre` adapter,
Tradecraft Graph expansions, multi-operator team coordination.

## Outline (numbered, ~5-7 sections)

1. **The current landscape: man vs. machine** (5 min)
   - AI hacking explosion post-2025
   - Where they fail: Villager, HexStrike, CS-MCP-server + Claude Sonnet 4.5
   - The b-ops.be Mimikatz example
   - Why operators don't trust autopilot

2. **What real red teaming looks like** (5 min)
   - The shell you can't afford to lose
   - Adaptive decisions under time pressure
   - The case for augmentation over automation

3. **Introducing Red Robin** (5 min)
   - Origin: frustration with existing tools
   - Core philosophy: don't hack for me, hack with me
   - High-level architecture (single slide)

4. **Technical deep-dive** (10 min)
   - Wire protocol v0.1 (adapter-agnostic)
   - Aggressor alias mechanism (production-proven primitive)
   - Two-tier verdict protocol (Layer A YAML <150ms + Layer B LLM async)
   - 5-layer memory + cross-engagement learning
   - Per-operator scope honest discussion (T11)

5. **Live demonstration** (15 min)
   - 11 scenes per demo runbook
   - DANGER block centerpiece (Scene 7 — vssadmin blocked)
   - Operator chat with engagement-aware reasoning (Scene 8)

6. **Red Robin vs. the existing toolset** (3 min)
   - Side-by-side: Red Robin vs. BAS tools vs. autonomous scanners
   - What Red Robin intentionally does NOT do — and why that's a feature

7. **The future of AI for red teamers** (2 min)
   - v0.2 roadmap: local mode, Mythic adapter, Tradecraft Graph
   - Open questions for the community

## Speaker information

The presenter is an active red team operator with field experience across
enterprise environments and adversary simulation engagements. Background spans
C2 framework development (Sliver and Cobalt Strike infrastructure), malware
development with documented EDR bypass work, OSINT, and the practical
integration of AI tooling into live offensive operations. Currently pursuing
the CRTO certification (active) and operating a Terraform/Ansible-driven red
team lab (GOAD-Light + Cobalt Strike) for ongoing research. Prior community
contributions include open-source tooling around red team infrastructure
automation. Red Robin is the speaker's first public talk on AI tooling
specifically; the open-source release will accompany the presentation. GitHub
repository link will be provided in the submission.

## Key takeaways for attendees

- A clear, honest assessment of what current AI hacking tools can and cannot do
- A new mental model for AI as operational companion rather than autonomous agent
- Hands-on familiarity with Red Robin's TUI and verdict output
- Practical insight into integrating AI assistance into a real red team workflow
- A framework for evaluating future AI tools: does it replace judgment or support it?
- Open-source release prior to the presentation

## Sources & prior art

- Spec doc: `docs/architecture-design.md` (~2600 lines)
- SpecterOps OPSEC Profiles (alias gate prior art, 2018+)
- Mythic `opsec_pre` native gate (SpecterOps, 2023+)
- b-ops.be "Cobalt Strike, MCPs, and the Illusion of Push-Button Intrusions" (2025+)
- MITRE ATT&CK detection mappings: T1003.001 (LSASS dump), T1003.006 (DCSync),
  T1490 (shadow copy deletion), T1558.003 (Kerberoasting), among others

## Demo prerequisites

- DEF CON 34 schedule slot of at least 45 minutes
- Stable wifi (Anthropic API + Voyage API outbound) — personal hotspot as backup
- 4K projector with text-readable from back of room
- Operator laptop + backup laptop (both pre-configured with daemon + TUI + CS Client)
- Acme Corp engagement pre-recorded as Sliver-Mock scenario (network-failure backup)
- Pre-recorded Anthropic response file for Scene 8 (rate-limit backup)
