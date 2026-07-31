# Security model

Red Robin is **advisory-only**: the adapter captures, the core advises. Nothing
the daemon does can stop a command from reaching the teamserver, so every
mitigation here is detective or contextual, never preventive.

## Threat model

| # | Threat | Where it stands |
|---|---|---|
| T1 | Operator laptop compromise | `~/.redrobin/` is `chmod 700` and `.env` `chmod 600` on POSIX; nothing is hardened on Windows. Full-disk encryption is the real control. |
| T2 | API key exfiltration | Keys come from the environment or `~/.redrobin/.env` only. Never logged. |
| T3 | Adapter spoofing | Bearer token in `~/.redrobin/transport.token`, `chmod 600`. |
| T4 | Engagement YAML poisoning | `summary`, scope and ROE from `engagement.yaml` are injected into the assistant's prompt. Whoever can write that file shapes its reasoning. File permissions are the only control. |
| T5 | KB poisoning | Operator-curated only; nothing is auto-fetched. |
| T6 | LLM hallucination | Advisory-only. Nothing the model says reaches the teamserver; the operator types every command. |
| T7 | Daemon crash | `events.jsonl` is an append-only write-ahead record; state is rebuilt by replay. |
| T8 | Network unavailable | The assistant goes quiet. Capture, persistence and working memory keep running. |
| T9 | ROE violation | Scope is context, not enforcement. `in_scope` / `out_of_scope` are projected into working memory so the assistant can flag drift; it cannot stop the command. |
| T10 | Sensitive data sent to Anthropic / Voyage | Documented and accepted. Captured command output is sent to the enricher and the assistant. |
| T11 | Coverage gaps | Red Robin only ever sees what a CS Client running the `.cna` observes. Team SOP is the mitigation. |

**Out of scope:** a compromised operator acting intentionally, a compromised
Anthropic or Voyage, and CS Client compromise.

## API keys

Loaded from the environment or `~/.redrobin/.env` — `ANTHROPIC_API_KEY` for the
assistant, enricher and summariser; `VOYAGE_API_KEY` for KB embeddings. Red
Robin reads no TOML or YAML config for secrets. Rotation and leak response:
[docs/secrets-handling.md](docs/secrets-handling.md).

## File permissions

Enforced on POSIX only (`redrobin/infrastructure/filesystem.py`):

| Path | Mode |
|---|---|
| `~/.redrobin/` and its `shared/`, `engagements/`, `kb-source/` subdirectories | 700 |
| `~/.redrobin/.env` | 600 (at `init`) |
| `~/.redrobin/transport.token` | 600 |

Nothing else is chmod'ed, and **nothing at all is enforced on Windows** — the
calls are skipped there. Databases, `events.jsonl` and working-memory JSON
inherit the 700 on their parent directory and no more. Use full-disk encryption.

## Network footprint

The daemon's only outbound connections are `api.anthropic.com` (LLM) and
`api.voyageai.com` (embeddings), and only when the corresponding key is set.

It never connects to the CS team server. The `.cna` runs inside the
already-authenticated CS Client and only POSTs captured activity inbound to the
daemon. There is no outbound C2 leg and no C2 credential on the Red Robin side.

## Reporting a vulnerability

Email security@red-robin.io, or open a GitHub Security Advisory if the repo is
public. Non-sensitive bugs go to GitHub Issues.

This is a v0.1 project with no formal CVE process. We acknowledge reports within
7 days and aim to fix critical issues within 30.
