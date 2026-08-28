from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yt_finance_kb.pipeline as pipeline
from yt_finance_kb.transcripts import TranscriptPending, TranscriptResult


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


def test_latest_per_channel_limits_each_channel(
    tmp_path, monkeypatch, sample_segments, sample_note
):
    config, state = _workspace(tmp_path)
    config.write_text(
        """
channels:
  - id: first-channel
    url: https://www.youtube.com/@first
  - id: second-channel
    url: https://www.youtube.com/@second
""".strip()
        + "\n",
        encoding="utf-8",
    )
    base_time = datetime.now(UTC) - timedelta(days=3)

    def videos_for(channel, *args, **kwargs):
        return [
            pipeline.Video(
                id=f"{channel.id}-{index}",
                channel_id=channel.id,
                title=f"{channel.id} video {index}",
                published_at=base_time + timedelta(days=index),
                url=f"https://www.youtube.com/watch?v={channel.id}-{index}",
            )
            for index in range(3)
        ]

    monkeypatch.setattr(pipeline, "fetch_channel_videos", videos_for)
    monkeypatch.setattr(
        pipeline,
        "fetch_transcript",
        lambda *args: TranscriptResult("en", False, "fake", sample_segments),
    )
    analyzer = FakeAnalyzer(sample_note)

    result = pipeline.process(
        tmp_path,
        config_path=config,
        state_path=state,
        latest_per_channel=2,
        analyzer=analyzer,
    )

    records = json.loads(state.read_text(encoding="utf-8"))["videos"]
    assert result.discovered == 4
    assert result.analyzed == 4
    assert analyzer.calls == 4
    assert set(records) == {
        "first-channel-1",
        "first-channel-2",
        "second-channel-1",
        "second-channel-2",
    }


def test_rediscovery_does_not_replace_original_publish_time(
    tmp_path, monkeypatch, sample_video, sample_segments, sample_note
):
    config, state = _workspace(tmp_path)
    discovered = [sample_video]
    monkeypatch.setattr(pipeline, "fetch_channel_videos", lambda *args, **kwargs: discovered)
    monkeypatch.setattr(
        pipeline,
        "fetch_transcript",
        lambda *args: TranscriptResult("zh-TW", True, "fake", sample_segments),
    )
    pipeline.process(
        tmp_path,
        config_path=config,
        state_path=state,
        analyzer=FakeAnalyzer(sample_note),
    )
    original = json.loads(state.read_text(encoding="utf-8"))["videos"][sample_video.id][
        "published_at"
    ]
    discovered[0] = sample_video.model_copy(
        update={"published_at": sample_video.published_at + timedelta(hours=2)}
    )
    pipeline.process(
        tmp_path,
        config_path=config,
        state_path=state,
        analyzer=FakeAnalyzer(sample_note),
    )
    record = json.loads(state.read_text(encoding="utf-8"))["videos"][sample_video.id]
    assert record["published_at"] == original


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
    state_data = json.loads(state.read_text(encoding="utf-8"))
    state_data["videos"][sample_video.id]["last_fetched_at"] = (
        datetime.now(UTC) - timedelta(days=8)
    ).isoformat()
    state.write_text(json.dumps(state_data), encoding="utf-8")
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


def test_missing_captions_waits_without_failing_or_calling_ai(
    tmp_path, monkeypatch, sample_video, sample_note
):
    config, state = _workspace(tmp_path)
    monkeypatch.setattr(pipeline, "fetch_channel_videos", lambda *args, **kwargs: [sample_video])
    calls = []

    def pending(*args):
        calls.append("fetch")
        raise TranscriptPending("native captions are not available yet")

    monkeypatch.setattr(pipeline, "fetch_transcript", pending)
    analyzer = FakeAnalyzer(sample_note)
    first = pipeline.process(tmp_path, config_path=config, state_path=state, analyzer=analyzer)
    second = pipeline.process(tmp_path, config_path=config, state_path=state, analyzer=analyzer)
    record = json.loads(state.read_text(encoding="utf-8"))["videos"][sample_video.id]
    assert first.waiting == 1
    assert first.failed == 0
    assert second.waiting == 1
    assert calls == ["fetch"]
    assert analyzer.calls == 0
    assert record["fetch_status"] == "waiting"
    assert record["analysis_status"] == "pending"
    assert record["alert_status"] == "none"
    assert datetime.fromisoformat(record["next_retry_at"]) > datetime.now(UTC)


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


def test_proxy_credentials_are_redacted_from_public_errors(monkeypatch):
    proxy = "http://secret-user:secret-password@proxy.example:8080"
    monkeypatch.setenv("YOUTUBE_PROXY_URL", proxy)
    message = pipeline._safe_error(
        RuntimeError(f"connection through {proxy} failed for secret-user / secret-password")
    )
    assert proxy not in message
    assert "secret-user" not in message
    assert "secret-password" not in message
    assert "***" in message


def test_api_keys_are_redacted_from_public_errors(monkeypatch):
    monkeypatch.setenv("SUPADATA_API_KEY", "supadata-sensitive-value")
    message = pipeline._safe_error(RuntimeError("request used supadata-sensitive-value"))
    assert "supadata-sensitive-value" not in message
    assert "***" in message


def test_discovery_and_apify_keys_are_redacted_from_public_errors(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "youtube-sensitive-value")
    monkeypatch.setenv("APIFY_TOKEN", "apify-sensitive-value")
    message = pipeline._safe_error(
        RuntimeError("youtube-sensitive-value then apify-sensitive-value")
    )
    assert "youtube-sensitive-value" not in message
    assert "apify-sensitive-value" not in message
    assert message.count("***") == 2
