from __future__ import annotations

import json
from pathlib import Path

import yt_finance_kb.pipeline as pipeline
from yt_finance_kb.transcripts import TranscriptResult


class FakeAnalyzer:
    def __init__(self, note):
        self.note = note
        self.calls = 0
        self.last_transcript = ""

    def analyze(self, video, transcript):
        self.calls += 1
        self.last_transcript = transcript
        return self.note


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / "config/channels.yaml"
    config.parent.mkdir()
    config.write_text(
        """
channels:
  - id: test-channel
    url: https://www.youtube.com/@test
    enabled: true
    languages: [zh-TW]
    backfill_days: 7
    tags: [财经]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    state = tmp_path / "state/videos.json"
    state.parent.mkdir()
    state.write_text('{"schema_version":1,"videos":{}}\n', encoding="utf-8")
    return config, state


def test_unchanged_transcript_never_calls_ai(
    tmp_path, monkeypatch, sample_video, sample_segments, sample_note
):
    config, state = _workspace(tmp_path)
    monkeypatch.setattr(pipeline, "fetch_channel_videos", lambda *args, **kwargs: [sample_video])
    monkeypatch.setattr(
        pipeline,
        "fetch_transcript",
        lambda *args: TranscriptResult("zh-TW", True, "fake", sample_segments),
    )
    analyzer = FakeAnalyzer(sample_note)
    first = pipeline.process(
        tmp_path, config_path=config, state_path=state, analyzer=analyzer
    )
    second = pipeline.process(
        tmp_path, config_path=config, state_path=state, analyzer=analyzer
    )
    assert first.analyzed == 1
    assert second.unchanged == 1
    assert analyzer.calls == 1


def test_changed_transcript_reanalyzes_once(
    tmp_path, monkeypatch, sample_video, sample_segments, sample_note
):
    config, state = _workspace(tmp_path)
    monkeypatch.setattr(pipeline, "fetch_channel_videos", lambda *args, **kwargs: [sample_video])
    current = list(sample_segments)
    monkeypatch.setattr(
        pipeline,
        "fetch_transcript",
        lambda *args: TranscriptResult("zh-TW", True, "fake", current),
    )
    analyzer = FakeAnalyzer(sample_note)
    pipeline.process(tmp_path, config_path=config, state_path=state, analyzer=analyzer)
    current.append(type(sample_segments[0])(start=30, text="新的金融观点"))
    pipeline.process(tmp_path, config_path=config, state_path=state, analyzer=analyzer)
    pipeline.process(tmp_path, config_path=config, state_path=state, analyzer=analyzer)
    record = json.loads(state.read_text(encoding="utf-8"))["videos"][sample_video.id]
    assert analyzer.calls == 2
    assert record["note_version"] == 2


def test_fetch_failure_does_not_call_ai(tmp_path, monkeypatch, sample_video, sample_note):
    config, state = _workspace(tmp_path)
    monkeypatch.setattr(pipeline, "fetch_channel_videos", lambda *args, **kwargs: [sample_video])
    monkeypatch.setattr(
        pipeline, "fetch_transcript", lambda *args: (_ for _ in ()).throw(RuntimeError("no captions"))
    )
    analyzer = FakeAnalyzer(sample_note)
    result = pipeline.process(tmp_path, config_path=config, state_path=state, analyzer=analyzer)
    record = json.loads(state.read_text(encoding="utf-8"))["videos"][sample_video.id]
    assert result.failed == 1
    assert analyzer.calls == 0
    assert record["fetch_status"] == "failed"
    assert record["alert_status"] == "pending"


def test_prompt_input_keeps_finance_but_local_filter_removes_ad(
    tmp_path, monkeypatch, sample_video, sample_note
):
    from yt_finance_kb.models import TranscriptSegment

    config, state = _workspace(tmp_path)
    monkeypatch.setattr(pipeline, "fetch_channel_videos", lambda *args, **kwargs: [sample_video])
    segments = [
        TranscriptSegment(start=1, text="記得訂閱按讚"),
        TranscriptSegment(start=5, text="今天分析金融市场和利率"),
    ]
    monkeypatch.setattr(
        pipeline, "fetch_transcript", lambda *args: TranscriptResult("zh-TW", True, "fake", segments)
    )
    analyzer = FakeAnalyzer(sample_note)
    pipeline.process(tmp_path, config_path=config, state_path=state, analyzer=analyzer)
    assert "訂閱按讚" not in analyzer.last_transcript
    assert "金融市场和利率" in analyzer.last_transcript

