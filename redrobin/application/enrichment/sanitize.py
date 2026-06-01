"""Strip Red Robin's own console annotations from beacon output before enrichment.

The ``.cna`` gate ``blog``s lines like ``[Red Robin] novel pattern — Layer B
reviewing async`` into the beacon's console, which Cobalt Strike then echoes back
into the output stream the enricher sees. Enriching those lines produces junk
findings (``novel_pattern`` / ``unusual_activity``) and extracts "Red Robin" as a
tool. This helper drops any line whose first non-whitespace content is the
self-annotation prefix, so only genuine command output reaches the LLM.

Defensive (enricher-side). The ROOT fix — stop the ``.cna`` from emitting the
annotation into the output stream at all — is the separate ``.cna`` plan.
"""

from __future__ import annotations

import re


def strip_self_annotations(output: str, *, prefix: str) -> str:
    """Return ``output`` with every line that starts (after optional leading
    whitespace) with ``prefix`` removed. ``prefix`` is matched literally
    (``re.escape``) so regex metacharacters in it (e.g. the brackets in
    ``[Red Robin]``) are not interpreted. Line structure of the surviving
    lines is preserved; the result is joined with ``\\n`` (a sole trailing
    newline is not re-added).

    An empty or whitespace-only ``prefix`` is treated as "no marker configured"
    and returns ``output`` unchanged — without this guard an empty prefix yields
    the pattern ``^\\s*`` which matches EVERY line, silently discarding all
    output (an invisible data-loss footgun reachable via the config env var)."""
    if not prefix.strip():
        return output
    pattern = re.compile(r"^\s*" + re.escape(prefix))
    kept = [line for line in output.splitlines() if not pattern.match(line)]
    return "\n".join(kept)


__all__ = ["strip_self_annotations"]
