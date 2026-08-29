from pathlib import Path

from yt_finance_kb.models import ChannelConfig
from yt_finance_kb.rendering import MAX_INDEX_TOPICS, note_topics, rebuild_indexes, render_note


def test_render_note_has_timestamp_and_no_transcript(sample_video, sample_note):
    channel = ChannelConfig(id="test-channel", url="https://www.youtube.com/@test", tags=["财经"])
    body = render_note(sample_video, channel, sample_note, 1)
    assert "https://youtu.be/abcdefghijk?t=12" in body
    assert "## 原子知识卡片" in body
    assert "涉及实体" not in body
    assert "来源类型" not in body
    assert "模型归纳" not in body
    assert "字幕正文" not in body


def test_rebuild_indexes_is_deterministic(tmp_path: Path):
    records = {
        "abcdefghijk": {
            "analysis_status": "complete",
            "title": "节目",
            "published_at": "2026-07-28T00:00:00+00:00",
            "note_path": "knowledge/channel/2026/2026-07-28-abcdefghijk.md",
            "topics": ["利率"],
        }
    }
    rebuild_indexes(tmp_path, records)
    first = (tmp_path / "indexes/topics/利率.md").read_text(encoding="utf-8")
    rebuild_indexes(tmp_path, records)
    second = (tmp_path / "indexes/topics/利率.md").read_text(encoding="utf-8")
    assert first == second
    assert "../../knowledge/channel/2026/2026-07-28-abcdefghijk.md" in first
    assert not (tmp_path / "indexes/entities").exists()


def test_note_topics_are_deduplicated_and_capped(sample_note):
    sample_note.cards = [
        card.model_copy(update={"topics": [f"主题{index}", f"行业{index}", f"资产{index}"]})
        for index, card in enumerate(sample_note.cards)
    ]
    channel = ChannelConfig(id="test", url="https://www.youtube.com/@test", tags=["财经"])
    topics = note_topics(sample_note, channel)
    assert topics[0] == "财经"
    assert len(topics) == MAX_INDEX_TOPICS
