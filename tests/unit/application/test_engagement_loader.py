"""Unit tests for EngagementLoader parsing of rules_of_engagement + objectives (#103)."""

from __future__ import annotations

from pathlib import Path

from redrobin.application.engagement_loader import EngagementLoader


def _write(tmp_path: Path, slug: str, body: str) -> None:
    eng_dir = tmp_path / slug
    eng_dir.mkdir(parents=True, exist_ok=True)
    (eng_dir / "engagement.yaml").write_text(body, encoding="utf-8")


def test_load_parses_rules_of_engagement_and_objectives(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "eng-x",
        "slug: eng-x\nname: X\n"
        "rules_of_engagement:\n  - No DoS\n  - Business hours only\n"
        "objectives:\n"
        "  - {id: o1, goal: Domain admin on CORP, priority: high}\n"
        "  - {id: o2, goal: Exfil HR data}\n",
    )
    eng = EngagementLoader(tmp_path).load("eng-x")
    assert eng.rules_of_engagement == ["No DoS", "Business hours only"]
    assert len(eng.objectives) == 2
    assert eng.objectives[0].id == "o1"
    assert eng.objectives[0].goal == "Domain admin on CORP"
    assert eng.objectives[0].priority == "high"
    # interpretation fields default clean — they are the assistant's to write
    assert eng.objectives[0].status == "not_started"
    assert eng.objectives[0].progress_pct == 0
    # objective without a priority falls back to the model default
    assert eng.objectives[1].priority == "medium"


def test_load_missing_roe_objectives_defaults_empty(tmp_path: Path) -> None:
    _write(tmp_path, "eng-y", "slug: eng-y\nname: Y\n")
    eng = EngagementLoader(tmp_path).load("eng-y")
    assert eng.rules_of_engagement == []
    assert eng.objectives == []


def test_load_skips_objective_without_id(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "eng-z",
        "slug: eng-z\nname: Z\n"
        "objectives:\n  - {goal: no id here}\n  - {id: o2, goal: valid one}\n",
    )
    eng = EngagementLoader(tmp_path).load("eng-z")
    assert len(eng.objectives) == 1
    assert eng.objectives[0].id == "o2"


def test_load_scalar_in_scope_degrades_to_empty_not_char_explosion(tmp_path: Path, caplog) -> None:
    # An operator who forgets the list `- ` writes a scalar. The OLD behavior
    # char-exploded `list("host01")` -> ['h','o',...] and silently broke scope
    # matching. Now it degrades to [] WITH a warning (visible, not silent).
    _write(tmp_path, "eng-s", "slug: eng-s\nname: S\nin_scope: host01\n")
    with caplog.at_level("WARNING"):
        eng = EngagementLoader(tmp_path).load("eng-s")
    assert eng.in_scope == []
    assert any("in_scope" in rec.message for rec in caplog.records)


def test_load_scalar_rules_of_engagement_degrades_to_empty(tmp_path: Path) -> None:
    _write(tmp_path, "eng-r", "slug: eng-r\nname: R\nrules_of_engagement: No DoS\n")
    eng = EngagementLoader(tmp_path).load("eng-r")
    assert eng.rules_of_engagement == []  # NOT ['N','o',' ','D','o','S']


def test_load_list_fields_still_parse_normally(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "eng-l",
        "slug: eng-l\nname: L\nin_scope:\n  - h1\n  - 10.0.0.0/24\nout_of_scope:\n  - h2\n",
    )
    eng = EngagementLoader(tmp_path).load("eng-l")
    assert eng.in_scope == ["h1", "10.0.0.0/24"]
    assert eng.out_of_scope == ["h2"]


def test_load_scalar_objectives_degrades_to_empty(tmp_path: Path) -> None:
    _write(tmp_path, "eng-o", "slug: eng-o\nname: O\nobjectives: not-a-list\n")
    eng = EngagementLoader(tmp_path).load("eng-o")
    assert eng.objectives == []
