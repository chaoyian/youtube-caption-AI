from __future__ import annotations

from datetime import UTC, datetime

import pytest

from yt_finance_kb.models import (
    Entity,
    KnowledgeCard,
    ResearchNote,
    TimedPoint,
    TranscriptSegment,
    Video,
)


@pytest.fixture
def sample_video() -> Video:
    return Video(
        id="abcdefghijk",
        channel_id="test-channel",
        title="测试财经节目",
        published_at=datetime.now(UTC),
        url="https://www.youtube.com/watch?v=abcdefghijk",
    )


@pytest.fixture
def sample_segments() -> list[TranscriptSegment]:
    return [
        TranscriptSegment(start=1, duration=2, text="今天讨论联准会利率政策"),
        TranscriptSegment(start=12, duration=3, text="降息预期可能影响科技股估值"),
    ]


@pytest.fixture
def sample_note() -> ResearchNote:
    point = TimedPoint(text="市场关注利率路径对科技股估值的影响", timestamp=12, source_type="主持人陈述")
    cards = [
        KnowledgeCard(
            title=f"知识点 {index}",
            insight="利率预期变化会影响成长型资产的估值折现率。",
            timestamp=12,
            source_type="模型归纳",
            topics=["利率", "科技股"],
        )
        for index in range(1, 6)
    ]
    return ResearchNote(
        summary="本期节目集中讨论利率政策与科技股估值之间的关系，并分析市场预期变化可能造成的资产价格波动和主要风险。",
        macro_context=[point],
        core_theses=[point, point, point],
        evidence=[point],
        bull_case=[point],
        bear_case=[point],
        risks=[point],
        time_sensitive=[point],
        entities=[Entity(name="联准会", type="政策")],
        cards=cards,
        disclaimer="内容来自节目字幕的结构化整理，未经外部核实，不构成投资建议。",
    )
