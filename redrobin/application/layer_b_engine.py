"""redrobin/application/layer_b_engine.py — Layer B LLM judge with RAG.

Per spec §1.2.1 Stage 3 + §4 Componente 4 + §13.3 cache_control:
- System prompt: stable persona + JSON format spec (1h cache TTL)
- KB chunks: RAG context retrieved per query (5min cache TTL, default ephemeral)
- Engagement summary: per-engagement OPSEC posture from engagement.yaml,
  set once at boot via :meth:`LayerBEngine.set_engagement_summary` and surfaced
  as a labeled ``# Engagement Context`` block (5min cache TTL — slice 7).
- Dynamic state: per-turn working memory (uncached) — reserved for future use.

Returns a Verdict by parsing the LLM's JSON response. On parse failure,
returns ESCALATE/PASS (fail-open per §1.2.1 safety net design).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from typing import Awaitable, Callable

from redrobin.application.kb_service import KBService
from redrobin.application.redactor import Redactor
from redrobin.domain.llm import LLMProvider
from redrobin.domain.opsec import Verdict, VerdictAction
from redrobin.infrastructure.persistence.context_summary_repo import (
    ContextSummaryRepo,
)

logger = logging.getLogger(__name__)


# Retroactive callback wired by VerdictPipeline (Slice 5). The pipeline itself
# orchestrates Layer B today; the attribute exists so future callers can drive
# the retroactive emission path without going through the pipeline. Kwargs form
# matches ``VerdictPipeline._on_retroactive``.
RetroactiveVerdictHook = Callable[..., Awaitable[None]]


SYSTEM_PROMPT = """\
You are Red Robin, an OPSEC advisor for a red team operator using Cobalt Strike.

Given the operator's proposed Beacon command, the engagement state, and retrieved
relevant knowledge base chunks, decide whether this command should PASS, WARN, or
BLOCK. **Default to PASS unless the payload command itself is materially dangerous.**
Layer A's static rules already caught the high-signal patterns before you
ran; you are reviewing the residue — typically a novel argument to `shell`
or `powershell` that no rule matched. Most residue is benign recon.

# The shell vehicle is NOT a per-command signal

`shell <anything>` spawns `cmd.exe` as a child of the beacon. This is a known,
accepted detection cost the operator absorbed at engagement start. The mere
fact that a command runs via `shell` is NOT a reason to WARN. Mention the
vehicle cost at most once per engagement in the engagement summary — never
per command. **Do NOT WARN solely because cmd.exe is spawned by the beacon.**
Reserve WARN/BLOCK for risk that lives in the payload itself.

# Categorize the payload, not the vehicle

Place the command into ONE of three categories and respond accordingly:

1. **STANDARD_RECON → PASS.** Read-only host or network enumeration the
   operator runs in the first minutes of any engagement. These commands
   have low and well-understood detection cost and are expected in nearly
   every engagement. Treat the following as STANDARD_RECON by default:
   - `ipconfig`
   - `tasklist` (with or without `/v` / `/svc`)
   - `whoami` (with or without `/all` / `/groups` / `/priv`)
   - `hostname`
   - `systeminfo`
   - `ver`
   - `set`
   - `route print`
   - `arp -a`
   - `netstat -ano`
   - `net view`
   - `net user`
   - `net localgroup`
   - `query user`
   - `qwinsta`
   - `klist` (Kerberos ticket listing)
   - `nslookup`
   - `Resolve-DnsName`
   - `tracert`
   - `pathping`
   - `ping` (single target)
   - `dir` / `ls`
   - `where`
   - `type` / `cat` of a user-readable file
   - `findstr` / `Select-String`
   - `Get-Process`
   - `Get-Service`
   - `Get-ChildItem`
   - `Get-Location`
   - `Get-LocalUser`
   - `Get-LocalGroup`
   - `Get-NetIPAddress`
   - `Get-NetTCPConnection`
   - `Get-NetRoute`
   - `Get-ComputerInfo`
   - `Get-CimInstance` (preferred over deprecated alternatives)

   Return PASS with a one-sentence reason such as "standard recon — no
   payload-side risk".

2. **RISKY_PAYLOAD → WARN.** The payload itself carries a meaningful
   detection cost or operational risk beyond the vehicle. Examples:
   credential discovery via `net group "Domain Admins"`, kerberoasting
   precursors (`setspn -q`), AppLocker bypass primitives (`rundll32
   url.dll`, `regsvr32 /i`, `mshta`), service enumeration in a way that
   pings every host on the subnet, large `net view /domain` sweeps.
   Return WARN with a reason that names the specific risky element (not
   the vehicle), and include 1-2 lower-noise alternatives in
   `suggestions`.

   **AMSI-visible credential cmdlets:** AMSI-visible powershell that loads
   a known-bad cmdlet (`Invoke-Mimikatz`, `Invoke-Kerberoast`,
   `Invoke-DCSync`) — WARN by default; if the engagement summary or KB
   chunks indicate AMSI is enabled or EDR is active, **escalate to BLOCK**
   since reaching Layer B implies a successful bypass and credential theft
   is imminent.

3. **UNAMBIGUOUSLY_DANGEROUS → BLOCK.** The command is near-certain
   detection, ROE-violating, or destructive. Examples: LSASS dump via
   mimikatz / procdump / nanodump that the existing Layer A rules
   somehow didn't catch (rare — usually a typo'd variant), `vssadmin
   delete shadows`, `wevtutil cl`, `reg add ... \\Run` persistence
   without prior approval, `sc create <remotehost> binPath=...` /
   `New-Service -ComputerName <remotehost>` (remote service install)
   without explicit operator approval in the engagement summary,
   `Set-MpPreference -DisableRealtimeMonitoring`,
   `Add-MpPreference -ExclusionPath` / `-ExclusionProcess` /
   `-ExclusionExtension` (Defender exclusion, T1562.001),
   `Stop-Service WinDefend` / `sc stop WinDefend` (T1562.001),
   `bcdedit /set {default} testsigning on` (T1553.006 — driver signature
   bypass), `fsutil usn deletejournal` (anti-forensics).
   Return BLOCK with a reason and 1-2 safer alternatives.

# When uncertain

If the command is novel and you cannot place it in a category with
confidence, prefer PASS over WARN. Layer A is the noisy gate; your job is
to be the calm second opinion. An over-cautious WARN drowns out genuine
high-signal verdicts and trains the operator to ignore Layer B.

# Engagement context overrides defaults

The engagement summary may explicitly elevate or lower a command's risk
(e.g., "CrowdStrike EDR confirmed on every host" raises STANDARD_RECON
items toward WARN, while "lab environment, no EDR" lowers RISKY_PAYLOAD
items toward PASS). Read the engagement context first; if it changes the
default disposition, say so explicitly in your reason.

# Format

Respond with ONLY a JSON code block in this exact format:

```json
{
  "action": "PASS" | "WARN" | "BLOCK",
  "reason": "1-2 sentence rationale. Name the payload risk, not the vehicle. Cite engagement context or KB chunks when relevant.",
  "suggestions": ["alternative 1", "alternative 2"]
}
```

If no useful suggestions exist, return an empty list. Use BLOCK sparingly —
only when detection is near-certain or ROE is at risk.
"""


@dataclass(frozen=True)
class EngagementContext:
    """Dynamic engagement state passed to Layer B per call (uncached)."""

    summary: str  # text representation of working memory (hosts/accounts/defenses/etc.)


class LayerBEngine:
    """LLM judge with RAG context retrieval."""

    def __init__(
        self,
        *,
        llm: LLMProvider,
        kb: KBService,
        top_k_kb_chunks: int = 5,
        context_provider: type[EngagementContext] = EngagementContext,
        summary_repo: ContextSummaryRepo | None = None,
        engagement_id_provider: Callable[[], str] | None = None,
    ):
        self._llm = llm
        self._kb = kb
        self._top_k = top_k_kb_chunks
        self._context_cls = context_provider
        self._engagement_summary = ""  # set per-engagement by daemon (Task 28+)
        # Slice 12 — sanitize the operator's command before it is embedded in
        # the user prompt sent to Anthropic. Closes gap #19 (secrets leak at
        # the LLM boundary). Verdict flow unchanged; only wire payload is
        # affected.
        self._redactor = Redactor()
        # Slice 13 — inject the latest L5 sliding-window summary into a
        # separate cached system block so long engagements stay coherent
        # after the most recent verdicts roll off the in-memory window.
        # ``engagement_id_provider`` returns the active engagement so the
        # repo lookup picks the right summary even after a mid-session
        # engagement switch. Both kwargs default to ``None`` so the legacy
        # call sites (unit tests, build-without-summarizer paths) keep
        # working — the cached block collapses when no summary is found.
        self._summary_repo = summary_repo
        self._engagement_id_provider = engagement_id_provider
        # Retroactive hook — bound by the factory to
        # ``VerdictPipeline._on_retroactive`` so callers that drive Layer B
        # outside the pipeline still surface retroactive verdicts on the bus.
        # Today the pipeline orchestrates Layer B itself, so this attribute is
        # set-but-unused unless someone bypasses the pipeline.
        self.on_retroactive_verdict: RetroactiveVerdictHook | None = None

    def set_engagement_summary(self, summary: str) -> None:
        """Called when active engagement changes; rebuilds dynamic state block."""
        self._engagement_summary = summary

    async def evaluate(self, *, command: str, raw: str) -> Verdict:
        """Retrieve KB context, call LLM, parse JSON verdict."""
        try:
            kb_hits = await self._kb.retrieve(raw, top_k=self._top_k)
        except Exception:
            logger.exception("KB retrieval failed; proceeding without RAG context")
            kb_hits = []

        kb_chunks = [text for text, _source, _score in kb_hits]
        # Slice 7 — surface the engagement.yaml summary as a *cached* system
        # block so every retroactive verdict in the session amortizes the
        # engagement-specific OPSEC posture (low-detection priority, scope
        # nuances, prior tradecraft constraints). The block is stable across
        # the session — only invalidated on engagement switch — so the 5min
        # ephemeral cache pays for itself across a single operator workflow.
        engagement_block = (
            f"# Engagement Context\n{self._engagement_summary}"
            if self._engagement_summary
            else ""
        )
        # Slice 13 — pull the latest L5 sliding-window summary for the
        # active engagement. The repo lookup is one indexed sqlite query
        # (engagement_id + LIMIT 1) per evaluate() — well below the per-call
        # LLM latency floor, so this stays a fixed-cost addition.
        recent_summary_block = ""
        if (
            self._summary_repo is not None
            and self._engagement_id_provider is not None
        ):
            try:
                eng_id = self._engagement_id_provider()
                summaries = self._summary_repo.list_recent(eng_id, limit=1)
                if summaries:
                    recent_summary_block = summaries[0].text
            except Exception:
                logger.exception(
                    "Layer B: failed to load recent summary; continuing without it"
                )
        # Reserved for future per-turn working memory (e.g., hosts seen this
        # tick). Today the daemon does not feed working memory into Layer B,
        # so dynamic_state stays empty and Anthropic skips the block entirely.
        dynamic_state = ""
        # Slice 12 — scrub secrets from both the parsed command and raw input
        # before embedding them in the user prompt. The LLM's verdict reasoning
        # is unaffected: it can still see flag names (/password) and command
        # shapes, just not the cleartext value.
        safe_command = self._redactor.redact(command)
        safe_raw = self._redactor.redact(raw)
        user_message = (
            f"Proposed command: {safe_command}\n"
            f"Raw input: {safe_raw}\n\n"
            "Decide: PASS / WARN / BLOCK with rationale + suggestions."
        )

        try:
            response = await self._llm.complete_with_cache(
                stable_system_prompt=SYSTEM_PROMPT,
                cacheable_kb_chunks=kb_chunks,
                cacheable_engagement_summary=engagement_block,
                cacheable_recent_summary=recent_summary_block,
                dynamic_state=dynamic_state,
                user_message=user_message,
            )
        except Exception:
            logger.exception("LLM call failed in Layer B; returning PASS (fail-open)")
            return Verdict(
                action=VerdictAction.PASS,
                reason="Layer B unavailable; falling back to Path 3 observation",
                rule_id=None,
            )

        return self._parse_verdict(response.text)

    @staticmethod
    def _parse_verdict(text: str) -> Verdict:
        """Extract JSON block from LLM response and map to Verdict."""
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if json_match is None:
            # Fallback: try parsing whole response as JSON
            blob = text.strip()
        else:
            blob = json_match.group(1)
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError:
            logger.warning("Layer B response not parseable JSON; returning PASS")
            return Verdict(
                action=VerdictAction.PASS,
                reason="Layer B returned unparseable response (fail-open)",
                rule_id=None,
            )

        action_str = str(payload.get("action", "PASS")).upper()
        try:
            action = {
                "PASS": VerdictAction.PASS,
                "WARN": VerdictAction.WARN,
                "BLOCK": VerdictAction.BLOCK,
            }[action_str]
        except KeyError:
            logger.warning("Layer B returned unknown action %r; returning PASS", action_str)
            action = VerdictAction.PASS

        return Verdict(
            action=action,
            reason=payload.get("reason", ""),
            rule_id=None,  # Layer B verdicts have no rule_id; rule_id is Layer A only
            suggestions=list(payload.get("suggestions", [])),
        )
