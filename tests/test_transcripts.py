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


def test_ytdlp_receives_proxy_without_logging_it(monkeypatch):
    captured = {}

    class FakeDownloader:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def extract_info(self, *args, **kwargs):
            return {
                "subtitles": {
                    "zh-TW": [{"ext": "json3", "url": "https://captions.invalid/file"}]
                }
            }

    monkeypatch.setenv("YOUTUBE_PROXY_URL", "http://user:password@proxy.invalid:8080")
    monkeypatch.setattr(transcripts, "YoutubeDL", FakeDownloader)
    monkeypatch.setattr(
        transcripts,
        "_download",
        lambda url: b'{"events":[{"tStartMs":1000,"dDurationMs":500,"segs":[{"utf8":"finance"}]}]}',
    )
    result = transcripts.fetch_with_ytdlp("abcdefghijk", ["zh-TW"])
    assert result.segments[0].text == "finance"
    assert captured["proxy"] == "http://user:password@proxy.invalid:8080"
    assert captured["logger"].__class__.__name__ == "_QuietYtDlpLogger"


def test_transcript_api_receives_same_proxy(monkeypatch):
    captured = {}

    def fake_proxy_config(**kwargs):
        captured.update(kwargs)
        return "proxy-config"

    class FakeApi:
        def __init__(self, proxy_config):
            captured["config"] = proxy_config

        def list(self, video_id):
            raise RuntimeError("stop after construction")

    monkeypatch.setenv("YOUTUBE_PROXY_URL", "http://user:password@proxy.invalid:8080")
    monkeypatch.setattr(transcripts, "GenericProxyConfig", fake_proxy_config)
    monkeypatch.setattr(transcripts, "YouTubeTranscriptApi", FakeApi)
    with pytest.raises(RuntimeError, match="stop after construction"):
        transcripts.fetch_with_transcript_api("abcdefghijk", ["zh-TW"])
    assert captured["http_url"] == "http://user:password@proxy.invalid:8080"
    assert captured["https_url"] == "http://user:password@proxy.invalid:8080"
    assert captured["config"] == "proxy-config"
