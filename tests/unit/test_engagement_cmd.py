"""CLI helper tests for `engagement new` scope flags (Plan H, #135)."""
from __future__ import annotations

from redrobin.cli_helpers.engagement_cmd import _csv_list


def test_csv_list_flattens_repeats_and_commas():
    # --in-scope a,b --in-scope c  -> ["a", "b", "c"]
    assert _csv_list(["a,b", "c"]) == ["a", "b", "c"]


def test_csv_list_strips_and_drops_empties():
    assert _csv_list([" a , ,b ", ""]) == ["a", "b"]


def test_csv_list_none_is_empty():
    assert _csv_list(None) == []
