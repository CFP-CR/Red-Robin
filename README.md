# Red Robin

> **AI red team companion that thinks with you, not for you.**

Red Robin is a Python daemon + TUI + Cobalt Strike adapter that gates risky
Beacon commands before they reach the teamserver, suggests stealthier
alternatives grounded in your knowledge base, and accumulates engagement
context across operations.

**Status:** v0.1 (DEF CON 34 RTV / La Villa 2026 ship target). Active dev.

**License:** MIT.

## What it does

- **OPSEC pre-flight gate** for Cobalt Strike via Aggressor `alias` overrides
  — production-proven primitive used by SpecterOps OPSEC Profiles since 2018.
  Dangerous commands are intercepted on the CS Client side; the command never
  reaches the teamserver if blocked.
- **Two-tier verdict protocol**: Layer A YAML rules return synchronous verdicts
  in <150ms; Layer B LLM judge with RAG context runs async for novel patterns.
- **5-layer memory system**: working memory (per-engagement), episodic (lessons),
  long-term (cross-engagement facts), tradecraft graph (technique→bypass), and
  context management.
- **Adapter pattern**: the daemon is C2-agnostic. CS adapter (Aggressor `.cna` +
  Python REST client) is the v0.1 reference; Sliver-Mock ships for protocol
  validation + demo; Sliver-Real committed for v0.1.1 (2026-06-22).
- **Operator-owned data**: everything lives in `~/.redrobin/`. Zero teamserver
  footprint. API keys via env var only.

## What it does NOT do

- **Does not execute commands for you.** Red Robin observes, advises, gates.
  The operator owns every decision.
- **Does not work without a human.** This is augmentation, not autonomy.
- **Does not exfil engagement data.** The only outbound network calls are to
  Anthropic (LLM) and Voyage (embeddings) — operator opts in per engagement.
  v0.2 local mode (Ollama + sentence-transformers) eliminates these too.

## Quick start

`redrobin init` is the front door. It scaffolds `~/.redrobin/`, prompts for
your API keys (hidden input), drops a sample engagement, and writes a
bearer token for the TUI. Full walkthrough — including non-interactive use,
the CS adapter, and smoke-test — in
[`docs/operator-setup.md`](docs/operator-setup.md).

### Install

```bash
git clone https://github.com/CFP-CR/Red-Robin.git
cd Red-Robin
uv sync --all-extras
```

### Init + verify

```bash
uv run redrobin init      # interactive: prompts for keys + engagement slug
uv run redrobin doctor    # preflight: env keys + dependencies + connectivity
```

Non-interactive (CI, Git Bash):

```bash
uv run redrobin init \
  --non-interactive \
  --anthropic-key "sk-ant-..." \
  --voyage-key "pa-..." \
  --engagement-slug op-smoke
```

### Run

```bash
uv run redrobin daemon start    # in one terminal
uv run redrobin tui              # in another terminal
```

### Create an engagement

```bash
uv run redrobin engagement new acme-q2-2026 --client "Acme Corp"
$EDITOR ~/.redrobin/engagements/acme-q2-2026/engagement.yaml
uv run redrobin engagement use acme-q2-2026
```

### Load the CS adapter

In Cobalt Strike Client → Scripts → Load → select
`adapters/cobaltstrike/redrobin.cna`. Verify the console shows
`[Red Robin] handshake OK; session=ses_...`.

## Architecture

Full spec at `docs/architecture-design.md` (~2600 lines).

```
+-----------+      HTTP localhost      +-------------+      REST API     +-----------+
| CS Client | <----------------------> | Red Robin   | <---------------- | CS Team   |
| + .cna    |      verdict requests    | daemon      |   state/dispatch  | Server    |
+-----------+      output observation  +-------------+                   +-----------+
                                           |
                                           v
                                       +-------+
                                       |  TUI  | (WebSocket)
                                       +-------+
```

The CS adapter is the sole C2 boundary. Red Robin core speaks the wire protocol
only — it doesn't know about REST API endpoints, JWT, beacon IDs, etc.

## Status & roadmap

- **v0.1** (current — DEF CON ship): CS adapter (full alias gate), Sliver-Mock,
  Anthropic cloud LLM, Voyage cloud embeddings, prompt_toolkit TUI, 14 bootstrap
  rules, 5-layer memory.
- **v0.1.1** (2026-06-22): Sliver-Real adapter (protoc-generated gRPC client).
- **v0.2** (post-DEF CON): Local mode (Ollama + sentence-transformers), Havoc
  adapter, Mythic adapter (native `opsec_pre` integration), Tradecraft Graph
  expansions.

## Documentation

Operator-facing:

- [`docs/operator-setup.md`](docs/operator-setup.md) — first-time daemon
  setup walkthrough (clone → init → doctor → daemon → TUI → smoke test).
- [`docs/secrets-handling.md`](docs/secrets-handling.md) — rotation, leak
  response, file perms (incl. Windows ACL, Git Bash, dotenv path caveats).
- [`docs/sigma-support.md`](docs/sigma-support.md) — what works in v0.1.x
  Layer A, what's deferred to v0.2, example SigmaHQ rules.
- [`docs/operator-cli-conventions.md`](docs/operator-cli-conventions.md) —
  ADR for interactive vs non-interactive subcommands.
- [`docs/demo-runbook.md`](docs/demo-runbook.md) — DEF CON RTV / La Villa
  live-demo checklist + script.

Project / dev:

- Project status: `Projects/red-robin-copilot.md`
- Architecture: `docs/architecture-design.md`
- Developer onboarding: [`docs/developer-onboarding.md`](docs/developer-onboarding.md)
- Security model: `SECURITY.md`
- Contributing: `CONTRIBUTING.md`

## Why "Red Robin"

Augmenting the operator without replacing them. The thesis is in the name:
work *with* the operator, not for them. Don't hack for me, hack with me.
