import pytest

import yt_finance_kb.transcripts as transcripts
from yt_finance_kb.models import TranscriptSegment
from yt_finance_kb.transcripts import TranscriptResult


def test_fetch_uses_backup_after_primary_failure(monkeypatch):
    def primary(video_id, languages):
        raise RuntimeError("blocked")

    expected = TranscriptResult("zh-TW", True, "backup", [TranscriptSegment(start=0, text="金融")])
    monkeypatch.setattr(transcripts, "fetch_with_ytdlp", primary)
    monkeypatch.setattr(transcripts, "fetch_with_transcript_api", lambda video_id, languages: expected)
    assert transcripts.fetch_transcript("abcdefghijk", ["zh-TW"]) == expected


def test_fetch_reports_both_failures(monkeypatch):
    monkeypatch.setattr(transcripts, "fetch_with_ytdlp", lambda *_: (_ for _ in ()).throw(ValueError("one")))
    monkeypatch.setattr(
        transcripts, "fetch_with_transcript_api", lambda *_: (_ for _ in ()).throw(ValueError("two"))
    )
    with pytest.raises(transcripts.TranscriptUnavailable) as error:
        transcripts.fetch_transcript("abcdefghijk", ["zh-TW"])
    assert "one" in str(error.value)
    assert "two" in str(error.value)

