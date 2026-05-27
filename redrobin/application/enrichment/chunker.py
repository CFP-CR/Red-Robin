"""Map-reduce output chunking: split oversized command output on line boundaries
with overlap, so a record straddling a boundary appears intact in the next chunk.

Returns ``[output]`` unchanged when it fits (the 99% case). char-based budget
(~4 chars/token) avoids a tokenizer dependency. The reduce step
(``WorkingMemoryRepo.apply_deltas``) dedups hosts/credentials/tools by key;
findings are append-only, so overlap may duplicate a straddling finding —
acceptable, since the whole point of overlap is to never DROP a straddling record.
"""

from __future__ import annotations


def chunk_output(output: str, *, max_chars: int, overlap: float = 0.15) -> list[str]:
    if len(output) <= max_chars:
        return [output]
    lines = output.splitlines(keepends=True)
    if len(lines) <= 1:  # a single line longer than max_chars — hard-split it
        return [output[i : i + max_chars] for i in range(0, len(output), max_chars)]

    chunks: list[str] = []
    start = 0
    while start < len(lines):
        end = start
        size = 0
        while end < len(lines) and size + len(lines[end]) <= max_chars:
            size += len(lines[end])
            end += 1
        if end == start:  # one line over budget — hard-split it, then continue
            chunks.extend(
                lines[start][i : i + max_chars] for i in range(0, len(lines[start]), max_chars)
            )
            start += 1
            continue
        chunks.append("".join(lines[start:end]))
        if end >= len(lines):
            break
        # advance by the window minus the overlap fraction (int() rounds overlap up slightly)
        step = max(1, (end - start) - int((end - start) * overlap))
        start += step
    return chunks
