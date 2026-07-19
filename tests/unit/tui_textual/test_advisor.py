import pytest
from textual.widgets import Static

from redrobin.tui.textual_ui.advisor import format_recommendation
from redrobin.tui.textual_ui.app import REGISTRY, RedRobinApp

_PAYLOAD = {
    "suggested_action": "Validate svc_reports vs SQL01",
    "rationale": "a quiet bind beats a loud dump",
    "opsec_risk": "WARN",
    "confidence": "high cred, unsure scope",
    "references": ["vault/mssql.md#chunk_0"],
    "correlation_id": "B-7:type db_backup.config",
}


def test_format_recommendation_includes_all_five_parts():
    text = format_recommendation(_PAYLOAD).plain
    assert "Validate svc_reports" in text
    assert "quiet bind" in text
    assert "WARN" in text
    assert "unsure scope" in text
    assert "vault/mssql.md#chunk_0" in text
    assert "advisory only" in text.lower()


def test_registry_has_advisor_handler():
    assert REGISTRY.has_handler("advisor.recommendation")


@pytest.mark.asyncio
async def test_advisor_dock_reveals_and_renders_on_frame():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        # hidden until a recommendation arrives (reserved seam)
        assert app.query_one("#advisor-dock").display is False
        REGISTRY.dispatch(target=app, frame={"type": "advisor.recommendation", "payload": _PAYLOAD})
        await pilot.pause()  # let the mount settle
        dock = app.query_one("#advisor-dock")
        assert dock.display is True
        card = app.query_one("#advisor-card", Static)
        # Textual 8.x Static exposes .content (a Content object), NOT .renderable
        # — same access pattern as test_app_reactive.py.
        assert "Validate svc_reports" in str(card.content)
