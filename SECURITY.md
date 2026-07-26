# Red Robin — Security Model

## Threat model summary

Full T1-T11 threat model in `docs/architecture-design.md` §6. That spec predates
the 2026-07-26 removal of the OPSEC pre-flight gate, so its T4 / T6 / T8 / T9 /
T11 entries still describe blocking behaviour Red Robin no longer has. The
summary below is the current one.

Red Robin is **advisory-only**: the adapter captures, the core advises. No
control the daemon has can stop a command from reaching the teamserver, so
every mitigation below is detective or contextual, never preventive.

**In scope:**
- T1: Operator laptop compromise (chmod 700/600, disk encryption recommendation)
- T2: API key exfiltration (env-var only — config.toml refuses api_key)
- T3: Adapter spoofing (bearer token in chmod 600 file)
- T4: Engagement YAML poisoning (file permissions). The `summary` and scope
  blocks in `engagement.yaml` are injected into the assistant's prompt, so
  whoever can write that file shapes its reasoning. (This slot previously held
  "rules YAML poisoning"; the Layer A rule pack was deleted with the pre-flight
  gate on 2026-07-26 and Red Robin ships no rule YAML any more.)
- T5: KB poisoning (operator-curated only; no auto-fetch)
- T6: LLM hallucination (advisory-only — nothing the model says reaches the
  teamserver; the operator types every command; citations required)
- T7: Daemon crash (events.jsonl write-ahead log; snapshot+replay)
- T8: Network unavailable (the assistant goes quiet; capture, persistence and
  working memory keep running)
- T9: ROE violation (scope is context, not enforcement — `in_scope` /
  `out_of_scope` are projected into working memory so the assistant can flag a
  drift; Red Robin does not stop the command)
- T10: Sensitive data to Anthropic/Voyage (documented; v0.2 local mode opt-in)
- T11: Coverage depends on the `.cna` being loaded (Aggressor architectural
  limit — team SOP mitigation). The per-operator *gate* scope this entry
  originally described no longer exists; what remains is that Red Robin only
  ever sees what a CS Client running the script observes

**Out of scope:**
- Compromised operator (intentional misuse)
- Compromised Anthropic/Voyage (supply chain)
- CS Client compromise

## API key handling

API keys are loaded **only** from environment variables:
- `ANTHROPIC_API_KEY` — required for the assistant
- `VOYAGE_API_KEY` — required for KB embeddings

The config loader **programmatically refuses** any `api_key` field in
`config.toml`. Attempting to put a key there raises `ValueError` at daemon
startup with a clear error message.

If you ever see an API key checked into git, `.gitignore` patterns are not
enough — rotate the key immediately.

## File permissions

Enforced at daemon startup via `redrobin/infrastructure/filesystem.py`:

| Path | Permission |
|---|---|
| `~/.redrobin/` | 700 |
| `~/.redrobin/transport.token` | 600 |
| `~/.redrobin/redrobin.log` | 600 |
| `~/.redrobin/engagements/<slug>/events.jsonl` | 600 |
| `~/.redrobin/shared/longterm.db` | 600 |
| `~/.redrobin/engagements/<slug>/episodic.db` | 600 |
| `~/.redrobin/config.toml` | 600 |

Windows permission model differs — Red Robin does not enforce chmod on
Windows. Operators should use full-disk encryption (BitLocker / FileVault /
LUKS).

## Network footprint

The daemon's only outbound connections (per engagement, if enabled):
- `api.anthropic.com` (LLM)
- `api.voyageai.com` (embeddings)
- `<teamserver>:50443` (CS REST API — for TUI dispatch + state recovery)

In v0.2 local mode, the Anthropic + Voyage endpoints are eliminated.

## Reporting vulnerabilities

For security-sensitive issues, please email security@red-robin.io
(or open a GitHub Security Advisory if the repo is public).
For non-sensitive bugs, open a GitHub issue.

## Disclosure timeline

This is a v0.1 project. We don't have a formal CVE process yet. We will
acknowledge reports within 7 days and aim to fix critical issues within 30
days.
