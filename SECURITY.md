# Red Robin — Security Model

## Threat model summary

Full T1-T11 threat model in `docs/architecture-design.md` §6.

**In scope:**
- T1: Operator laptop compromise (chmod 700/600, disk encryption recommendation)
- T2: API key exfiltration (env-var only — config.toml refuses api_key)
- T3: Adapter spoofing (bearer token in chmod 600 file)
- T4: Rules YAML poisoning (file permissions + hash-checking roadmap)
- T5: KB poisoning (operator-curated only; no auto-fetch)
- T6: LLM hallucination (Layer A deterministic floor; citations required)
- T7: Daemon crash (events.jsonl write-ahead log; snapshot+replay)
- T8: Network unavailable (graceful degradation to Layer A only)
- T9: ROE violation (scope_enforcer Stage 1 — out_of_scope hosts trigger BLOCK)
- T10: Sensitive data to Anthropic/Voyage (documented; v0.2 local mode opt-in)
- T11: Per-operator gate scope (Aggressor architectural limit — team SOP mitigation)

**Out of scope:**
- Compromised operator (intentional misuse)
- Compromised Anthropic/Voyage (supply chain)
- CS Client compromise

## API key handling

API keys are loaded **only** from environment variables:
- `ANTHROPIC_API_KEY` — required for Layer B LLM judge
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
