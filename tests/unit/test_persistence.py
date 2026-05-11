"""SQLAlchemy Core + aiosqlite — episodic + longterm repos."""

import pytest
import pytest_asyncio

from redrobin.infrastructure.persistence.db import make_engine, metadata
from redrobin.infrastructure.persistence.episodic_repo import EpisodicRepo
from redrobin.infrastructure.persistence.longterm_repo import LongtermRepo


@pytest_asyncio.fixture
async def episodic_engine(tmp_path):
    engine = make_engine(tmp_path / "episodic.db")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def longterm_engine(tmp_path):
    engine = make_engine(tmp_path / "longterm.db")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_episodic_add_and_list(episodic_engine):
    repo = EpisodicRepo(episodic_engine)
    lesson = await repo.add(
        engagement_id="acme-q2",
        text="nanodump invisible to Falcon on Win 2022",
        source="auto-detected",
    )
    assert lesson.id is not None
    assert lesson.engagement_id == "acme-q2"
    assert lesson.promoted_to_longterm is False

    lessons = await repo.list_for_engagement("acme-q2")
    assert len(lessons) == 1
    assert lessons[0].text == "nanodump invisible to Falcon on Win 2022"


@pytest.mark.asyncio
async def test_episodic_isolates_per_engagement(episodic_engine):
    repo = EpisodicRepo(episodic_engine)
    await repo.add(engagement_id="acme-q2", text="lesson A")
    await repo.add(engagement_id="globex-q3", text="lesson B")

    acme = await repo.list_for_engagement("acme-q2")
    globex = await repo.list_for_engagement("globex-q3")
    assert {lesson.text for lesson in acme} == {"lesson A"}
    assert {lesson.text for lesson in globex} == {"lesson B"}


@pytest.mark.asyncio
async def test_episodic_mark_promoted(episodic_engine):
    repo = EpisodicRepo(episodic_engine)
    lesson = await repo.add(engagement_id="acme-q2", text="x")
    await repo.mark_promoted(lesson.id)
    refreshed = await repo.list_for_engagement("acme-q2")
    assert refreshed[0].promoted_to_longterm is True


@pytest.mark.asyncio
async def test_longterm_add_and_list(longterm_engine):
    repo = LongtermRepo(longterm_engine)
    fact = await repo.add(
        text="nanodump bypasses Falcon",
        source_engagement_id="acme-q2",
    )
    assert fact.id is not None

    all_facts = await repo.list_all()
    assert len(all_facts) == 1
    assert all_facts[0].source_engagement_id == "acme-q2"


@pytest.mark.asyncio
async def test_longterm_stores_embedding_bytes(longterm_engine):
    repo = LongtermRepo(longterm_engine)
    embedding_bytes = b"\x00\x01\x02\x03" * 256  # mock embedding
    fact = await repo.add(
        text="x",
        source_engagement_id=None,
        embedding=embedding_bytes,
    )
    assert fact.id is not None
