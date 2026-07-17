"""Unit tests for the map-reduce output chunker."""

from __future__ import annotations

from redrobin.application.agents.enricher.chunker import chunk_output


def test_small_output_is_single_chunk():
    out = "line1\nline2\nline3\n"
    assert chunk_output(out, max_chars=1000) == [out]


def test_large_output_splits_on_line_boundaries_with_overlap():
    lines = [f"line-{i:03d}\n" for i in range(400)]  # ~3200 chars
    out = "".join(lines)
    chunks = chunk_output(out, max_chars=800, overlap=0.15)
    assert len(chunks) > 1
    for c in chunks:
        assert c.endswith("\n")
        assert len(c) <= 800
    body = "".join(chunks)
    for i in range(400):
        assert f"line-{i:03d}" in body


def test_overlap_repeats_boundary_lines():
    lines = [f"L{i}\n" for i in range(100)]
    chunks = chunk_output("".join(lines), max_chars=120, overlap=0.2)
    for i in range(len(chunks) - 1):
        a = set(chunks[i].splitlines())
        b = set(chunks[i + 1].splitlines())
        assert a & b, f"chunks {i},{i + 1} have no overlap"


def test_single_oversized_line_is_hard_split():
    out = "x" * 5000  # one line, no newline
    chunks = chunk_output(out, max_chars=1000)
    assert len(chunks) >= 5
    assert "".join(chunks) == out


def test_empty_output_returns_single_empty_chunk():
    assert chunk_output("", max_chars=100) == [""]


def test_zero_overlap_produces_non_overlapping_tiling():
    lines = [f"L{i}\n" for i in range(100)]
    chunks = chunk_output("".join(lines), max_chars=120, overlap=0.0)
    assert len(chunks) > 1
    for i in range(len(chunks) - 1):
        a = set(chunks[i].splitlines())
        b = set(chunks[i + 1].splitlines())
        assert not (a & b), f"chunks {i},{i + 1} overlap despite overlap=0.0"
    assert "".join(chunks) == "".join(lines)  # full coverage, no loss
